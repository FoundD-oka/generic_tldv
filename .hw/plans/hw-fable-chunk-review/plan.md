---
generated_by: fable
task_id: hw-fable-chunk-review
created: 2026-08-11
size: M
runtime: inline
base-commit: 6334d0b6a86c8bc067ae7195add483f4ce46024f
---

# hw-fable-chunk-review: fable_review.py に上限超過時のチャンク分割レビューを実装する

## ゴール

`.hw/fable_review.py` のレビュー材料がプロンプト上限を超えるとき、単に FAIL する
のではなく、ファイル境界で決定的にチャンク分割して複数回レビューし、fail-closed に
集約する。上限以下の挙動は現行と完全同一に保つ。

## 制約(先に読むこと)

- `git diff` のフラグ(`--binary --no-ext-diff --full-index`)は**変更しない**。
  `-M` / `-C` は導入しない(実測でゼロ効果が確認済みのため。効果検証値は
  検証契約 NFT-002 参照)。
- `load_review_context()` と `target_sha256` の算出(全材料の sha256)は
  **変更しない**。`check_review_verdict.py` が同関数を共有しており、READY 束縛の
  互換を無改修で保つため。
- verdict JSON の `schema_version` は "1.0" のまま。追加情報は additive な
  フィールドでのみ入れる(`check_review_verdict.py:33` が等値検査している)。

## How

### 1. 分割ロジック(新関数)

`fable_review.py` に追加:

- `split_material_by_file(material: bytes) -> list[tuple[str, bytes]]`
  材料 bytes を `diff --git ` 行境界で per-file に分割する(bytes のまま処理。
  `--binary` 出力を壊さない)。返り値は (ファイルパス, そのファイルの差分bytes)。
- `pack_chunks(files: list[tuple[str, bytes]], budget: int) -> list[list[tuple[str, bytes]]]`
  入力順の first-fit で詰める(決定的。並べ替えや乱択をしない)。
  - 単一ファイルの差分が budget 超 → `ReviewError`。メッセージにファイルパスと
    実測 bytes を含める。
  - チャンク数が `HW_FABLE_MAX_CHUNKS`(env、既定 "4")超 → `ReviewError`
    「タスクを分割するか base-commit を見直してください」系のメッセージ。
  - budget = `HW_FABLE_MAX_PROMPT_BYTES` − (ヘッダ+契約+マニフェストの
    実バイト数)。budget が 0 以下になる場合も `ReviewError`。

### 2. run_review の分岐

- 現行どおりプロンプトを組み、既定上限以下なら**現行と同一の1回実行**
  (プロンプト文字列も呼び出しも一切変えない)。
- 超過時のみチャンク経路へ:
  1. 全体マニフェストを材料から生成(全変更ファイルパスと各差分 bytes 数の一覧。
     git 再実行ではなく split 結果から作る)。
  2. 各チャンク i/N のプロンプト = 現行ヘッダ(task/size/base/head/契約hash/
     全材料の target hash)+ チャンク表記 `chunk: i/N` + マニフェスト + 現行の
     判定指示 + 追記指示「このレビューは分割実行の一部。他チャンクの内容は
     マニフェストでしか見えていない。このチャンク内の差分に根拠がない違反は
     出さない」+ 検証契約全文 + チャンク差分。
  3. CLI 呼び出しは現行 `command` と同一構成。チャンクごとに実行し、raw 出力を
     `.hw/state/fable-review-<task>.chunk<i>.raw.json` に保存する。

### 3. 集約

- `violations` = 全チャンクの連結。1件以上 → 全体 `NEEDS_REPAIR`。
- violations 0 で、いずれかが `NEEDS_HUMAN` → 全体 `NEEDS_HUMAN`。
- 全チャンク `READY` → 全体 `READY`。
- `confidence` = 各チャンクの最小値(low<medium<high)。ただしチャンク実行時は
  上限 "medium"(high を出さない)。
- `advisory` は連結、`summary` は各チャンク summary をチャンク番号つきで連結。
- verdict JSON に additive で記録:
  `"chunked": true`, `"chunk_count": N`,
  `"chunks": [{"index", "files", "material_sha256", "prompt_sha256",
  "response_sha256", "verdict", "confidence"}]`。
  トップレベルの `target_sha256` / `contract_sha256` / `prompt_sha256`(単一パス時
  との互換キー。チャンク時は全チャンク prompt 連結の sha256 とする)は維持。
- exit code 規約は現行どおり(READY=0 / それ以外=1 / エラー=2)。

### 4. テストと配線

- `.hw/tests/fable-review-chunking.test.sh`(または同 .py。既存
  `pr-create-intercept.test.sh` の様式に合わせる)を新設:
  - mktemp 下に git リポジトリを作り、`.hw/plans/<t>/`(size M の sml-decision、
    契約、base-commit)を commit した後、複数ファイルで合計 246,456 bytes 以上の
    差分 commit を作る(単一ファイル最大は約 112KB 相当を含める。PR #77 実測比)。
  - `HW_FABLE_CLI` に stub(受信プロンプトを連番ファイルへ保存し、環境変数か
    設定ファイルの指示に従い READY / NEEDS_REPAIR の JSON を返す)を差して
    `fable_review.py` を実行し、検証契約 AT-001..AT-008 を検査する。
- `.hw/verify.sh` に本テストの実行を追加(pr-create-intercept.test.sh と同じ並び。
  失敗したら exit 1)。
- 注意: `hw-prime-worktree-guard` タスクも verify.sh に1行足す。両タスクは直列に
  実施し、後発がリベースする。

## Why(実装者に渡さない)

R軸 prime は「1つのコンテキストに収まらない仕事」を回す仕組みなのに、M/L レビューは
全差分が1プロンプトに収まる前提で、prime を使うほどレビューが構造的に通らない
(PR #77 で実測 241,644B > 200,000B、env 320,000 への引き上げで回避=対症療法)。
`-M -C` 仮説は実測 246,456B → 246,456B で棄却済み(分解型差分は rename 検出の
類似度閾値に届かない)。上限引き上げはレビュー品質劣化とコスト増でスケールしない。
Fable への tools 付与はハッシュ束縛と read-only 原則を壊す。よって R軸の思想を
レビュー層へ持ち込む map-reduce が本質解。チャンク数上限とファイル内分割拒否で
fail-closed を維持し、confidence 上限 medium と chunked 記録で横断盲点を隠さない。
詳細は `.pipeline/plans/hw-harness-prime-gaps-2026-08/design.md`。

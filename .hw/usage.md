# hw v2 の使い方

v2.1.3以降、検証コマンドには `HW_VERIFY_ROOT`、`HW_VERIFY_HEAD` と
`HW_VERIFY_BASE` を渡す。gateでは実際に選んだ比較先commit、単独verifyでは空文字を渡す。
独自の差分チェックはこの比較先を使い、main固定で別の範囲を検査しない。
これらは検証範囲の伝達であり、実行権限の証明ではない。

## 主担当と分担

主担当が調査・計画・分担を選ぶ。未知の導入では調査→小さな実験→計画の更新を使い、
小変更には不要な工程を課さない。モデルは呼び出し側が選び、モデル名を合格条件にしない。
Claude には hw-researcher / hw-builder / hw-verifier を配置する。これらのモデル指定は inherit。
任意の意図レビュー用 hw-intent-reviewer はfableを既定候補とする（後述）。
Codex には AGENTS.md の案内と共通の役割文書を使う。利用中のランタイムの分担機能で
新しいコンテキストを開始し、必要な資料を明示して渡す。特定の subagent API は再実装しない。
ツールがない環境では自分の再読を独立レビューと呼ばず、レビュー待ちを報告する。

設計探索では、同じ会話を続けるか、原要求と確認済み資料から別案を作るかを主担当が選ぶ。
毎回の分岐や2案生成は必須ではない。探索の分岐と、レビュー初回からBuilderの自己評価を
除くことは別の目的を持つ。別案が要求を満たしていても、誤案を検出した証明にはならない。
実行時のモデル・役割文・自動読込みされる指示やスキルはランタイムでも確認する。
manifestのrole.mdのhashだけでは、それ以外の実際の入力や独立性を保証しない。

Claude の tools に Bash を含めると、Write/Edit を外してもファイル書き換えは可能。
手引き・役割名・worktree・Context Manifest はOSの権限制限ではない。検証コードも
信頼できない場合は、書き込み可能な一時領域のみを持つ別コンテナ等で実行する。
本番認証情報や保護設定の変更権限は、Builder/Verifier の環境に渡さない。

## プロジェクトが所有する設定

`.hw/project.json` と `.hw/verify.sh` は更新時に保持する。
`verify` は shell 文字列でなく argv 配列の配列。例:

```json
{
  "schema": 1,
  "base_ref": "origin/main",
  "verify": [["npm", "test"]],
  "timeout_seconds": 900,
  "review": {"paths": ["src/auth/*", "migrations/*"], "check_name": null, "app_id": null}
}
```

これは例。base_ref と review.paths はプロジェクトの実物に合わせる。
`fnmatch` でパス全体を照合するため `*` は `/` も含む。認証・課金・データ変更など、
固有の重要経路を指定する。共通の .hw/.github/.claude/.codex と AGENTS.md/CLAUDE.md
の変更は設定によらずレビュー対象。パス判定は要求の危険度を完全に検出しない。
主担当が独立検証を追加すべきと判断した作業は task.review_required を true にする。
`.hw/tasks/` は実行中の要求だけを置く場所。CIも全ファイルを読み、CLIの --task 省略では
必須レビューを外せない。`review_paths` を指定すればその範囲の変更に適用する。
省略時は全変更に適用する。完了済みの要求は `docs/hw-tasks/` 等へ移す。
要求の追加・変更・削除自体も .hw の変更なので独立レビューの対象になる。
プロジェクトの verify が内部で検証をスキップして exit 0 を返すことまでは一般的な
ランナーでは検出できない。初回移行で既存 verify の実際のチェック内容を点検する。

## 検証

```bash
python3 .hw/runtime/hw.py doctor
python3 .hw/runtime/hw.py verify
python3 .hw/runtime/hw.py gate --base origin/main
```

verify は未commitの作業にも使える。gate は対象 checkout の clean な HEAD に対し、
比較先との merge-base からの変更全体を調べて検証を再実行する。
成功は checks_passed。merge・deploy・削除の許可は発行しない。
実行ログと前後の状態を `.hw/evidence/` に保存する。エラー時はJSONまたはログを確認する。
検証でソースやHEADが変われば失敗。ignoreされた依存・設定・外部DBは完全な状態固定の
対象ではないため、その環境に関する必要な確認はプロジェクトの verify に含める。
対象外のworktreeへ .hw をコピーしたり、planの文字列だけを根拠に別checkoutへ移動しない。

## 要求と独立レビュー

複雑な作業では、目的・元の要求を参照できる sources・受入条件を
`.hw/tasks/<task-id>.json` に保存する。Builderの自己評価を混ぜない。

```json
{
  "schema": 1,
  "purpose": "利用者が自分の申込履歴を確認できる",
  "acceptance": ["本人の履歴を表示する", "他人の履歴は表示しない"],
  "constraints": ["既存ログインを使う"],
  "sources": ["docs/requirements.md#history"],
  "review_required": true
}
```

仕様を含めてcommitした対象から、明示的な入力セットを作る:

```bash
python3 .hw/runtime/hw.py context --task history --role verifier \
  --base origin/main --output /tmp/history-review
```

request.json、role.md、changes.patch、manifest.json を出力する。manifest の binding は
比較先・HEAD・全差分・有効な方針のハッシュをまとめた値。parent の全履歴を渡さず、
新しいセッションへこの入力を渡す。ソースを調べる環境も別途用意する。
ソース差分には変更された文書も含む。入力セット生成だけで他文書や上位指示への
アクセスが禁止されるわけではない。manifest の isolation_enforced は false と明示する。

Verifier は元の要求と条件の抜け、変更全体、必要な実テストから独自に判断する。
結果のローカル形式:

```json
{
  "schema": 1,
  "binding": "manifest.json の binding",
  "reviewer": "実際に使った検証セッションの識別子",
  "context_mode": "fresh",
  "verdict": "pass",
  "checks": [{"method": "本人と別利用者のアクセスを実行", "result": "期待どおりの可否"}],
  "findings": [],
  "unverified": []
}
```

指摘は findings に `blocking`（boolean）、`scenario`（再現条件）、説明を記録する。
未実施・無応答・確認失敗は verdict を pass にしない。重大な欠陥は契約項目の有無と
無関係に blocking。修復後は新しい binding で全体を再確認する。
技術レビューのunverifiedは合否に必要だが未確認の項目。非空・null・不正な型のままのpassは拒否する。
従来の技術レビュー記録ではこの項目の省略を許すが、省略は確認済みであることの証明ではない。
reviewerには実際のセッションを識別する空でない文字列を記録する。これは身元の認証ではない。

```bash
python3 .hw/runtime/hw.py gate --base origin/main --task history \
  --review .hw/evidence/history-review.json
```

このJSONの存在・形式はチェックできるが、誰が作ったかや実際の独立性は証明できない。
ローカルの支援用記録であり、CIはこのJSONを合格根拠として受け付けない。
過去のHD記録は原因調査に利用する。新方式は再発カテゴリの文字列で解除する仕組みを
持たず、必要な回帰検証を通常のテストへ組み込む。旧記録だけでは現在の欠陥は閉じない。

## CI と外部の権限境界

CIは pull_request のHEADをcheckoutし、比較先のcommitに保存されたランナーと方針を使う。
独立レビューが必要なら、設定した GitHub App の Check Run をGitHubから読み取る。
ローカルの verdict は受け付けない。最新の該当チェックが次をすべて満たす必要がある:

- `name` = review.check_name、`app.id` = review.app_id
- `head_sha` = PRのHEAD、`external_id` = このPR範囲の binding
- `status` = completed、`conclusion` = success

チェックを発行する側は、同じ比較先・HEAD・方針で binding を計算し、独立した検証を
完了してから発行する。未構成・無応答・古い比較先・古いチェック・API失敗はblock。
ローカルのCLIがチェックを書き込む機能はない。reviewerの結論待ちで先にCIが失敗した
場合はチェック発行後にGitHubの Re-run jobs で再実行する。

重要: これはGitHub Appの接続契約までの実装。独立したAppの配備・鍵・ポリシーは
プロジェクト側で用意する。同じPRの自由に変更可能な処理から成功を発行するAppでは
独立性が成立しない。GitHub Actions全体という広い発行主体だけで独立と判断しない。
workflow自体の保護、必須チェック、保護変更や迂回の権限分離はGitHub側で設定する。
比較先が更新された後も古い成功結果でmergeできないよう、保護側で最新の比較先との
検証を要求する。bindingの照合だけでGitHubのmerge許可設定までは強制できない。
本番変更は別の実行主体で、操作・対象・上限・期限を制限し、実行時にも条件を再確認する。
履歴は監査、承認は業務上の許可、テストは動作の証拠であり、相互に代用しない。

初回導入PRはbase側にv2ランナーがないため自動の最終ゲートを通らない。
保護された経路を初めて設置する変更として、人間が別途差分・ローカル検証・外部設定を
確認する。自動fallbackで全チェックを省略して成功にはしない。
テンプレートは依存インストールや環境起動を推測しない。既存CIの環境準備を保持して接続する。
merge queue/非GitHub CIにはこのPR用アダプターをそのまま使わず、対象SHAと信頼元を明示した
接続を別途実装する。doctorは外部保護や本番権限を設定済みとは報告しない。

## 意図・体験レビュー（v2.1、案件単位で有効化）

新しい体験、要件解釈の幅が大きい変更、技術チェックを通っても利用者の目的からずれた
案件で使う。調査や技術QAとは別軸。小さな確定済みの変更に一律の工程を増やさない。
主担当は .hw/roles/intent-reviewer.md を読み、段階ごとの新しいセッションを用意する。
Fableを既定候補とし、Claude入口 hw-intent-reviewer のmodelもfable。projectの
intent_review.modelは候補をmanifestへ記録する設定であり、CLIを自動起動しない。
別モデルを選んだときは、呼び出し側でmodel引数も変える。実行ログの実モデル名を記録する。

既存taskのスキーマに次を追加する。review_required（技術QA）とは独立に有効化できる。
下記はtask全体の例。すべての入力はリポジトリ内の追跡済み通常ファイルを指定する。
URLや見出し参照は直接コピーできないため、取得日・URL付きの資料ファイルを作る。
秘密や不要な全会話を格納しない。引用の出典と省略範囲を残す。

```json
{
  "schema": 1,
  "purpose": "初見の人が触りながら音楽の仕組みを発見する",
  "acceptance": ["スマホのタッチで遊べる"],
  "constraints": ["最初は窓3つと猫1匹"],
  "sources": ["docs/night/user-request.md"],
  "review_required": true,
  "review_paths": ["app/night/*", "lib/night-town/*", "docs/night/*"],
  "intent_review": {
    "required": true,
    "sources": ["docs/night/user-request.md"],
    "baseline": "docs/night/intent-baseline.json",
    "design": ["docs/night/design.md"],
    "evidence": ["docs/night/observations.md", "app/night/page.tsx"]
  }
}
```

intent_review.sourcesは主担当の要約でなくユーザーの元の発言・参考資料。purposeや
acceptanceはBuilder向けにも残すが、意図レビューのbaseline入力にはコピーしない。
この機構はファイル内容が本当に原文か、選別が公正かまで検証しない。

v2.1.1から、入力セットのrole.mdは対象HEADの `.hw/roles/intent-reviewer.md` と同じバイト列。
別の短縮プロンプトへ置換しない。manifestのreview_protocol_sha256とdesign/deliveryのbindingに
その内容を含める。更新前のdesign/delivery receiptは再利用できない。原文が同じならbaselineの
source_bindingは保たれるが、baseline自身の解釈は原文から点検する。旧checkoutに役割ファイルが
なければ更新計画で導入し、不在を短縮版へのfallbackで隠さない。


### 1. 設計前に原文だけで基準を作る

原文とtaskをcommitし、cleanな対象checkoutで実行する。この段階では指定した
baseline/design/evidenceのファイルはまだ存在しなくてもよい。

```bash
python3 .hw/runtime/hw.py context --task night --role intent-reviewer \
  --phase baseline --base origin/main --output /tmp/night-baseline
```

出力はrole.md、manifest.json、inputs/sources/以下の原文のコピーだけ。全差分・
主担当の受入条件・設計・既存レビューは含まない。新しいセッションはmanifestにある
ファイルだけを読み、独自の期待を作る。親の会話をforkしない。
返された内容を次の形式で指定のbaselineファイルへ保存する。

```json
{
  "source_binding": "baseline manifest の source_binding",
  "expectations": [
    {"id": "E1", "statement": "触った対象から音と動きが返る", "basis": "元発言の箇所", "verification": "初回・反復タッチの音付き観測"}
  ],
  "assumptions": [],
  "unresolved": []
}
```

基準は主担当が都合よく書き換えない。原文に照らした訂正は理由を残して可能。
source_bindingはtask識別子と原文のパス・バイト列に結びつく。実装commitが増えても
変わらず、原文が変わると失効する。意味的な独立性を証明する署名ではない。

### 2. 設計を別セッションで照合する

baselineと設計をcommitしてから生成する。

```bash
python3 .hw/runtime/hw.py context --task night --role intent-reviewer \
  --phase design --base origin/main --output /tmp/night-design
```

原文・baseline・設計だけが入る。実装者の弁明や他レビュアーの結論を設計ファイルに
混ぜない。Reviewerは基準自体も原文で確認し、設計の不一致または必要な確認を返す。

レビュー記録の例（値とchecksは実際の結果に置き換える）:

```json
{
  "schema": 1,
  "axis": "intent",
  "phase": "design",
  "task": "night",
  "binding": "この段階の manifest.binding",
  "source_binding": "manifest.source_binding",
  "reviewer": "実際の独立セッション識別子・実モデル名",
  "context_mode": "fresh",
  "verdict": "pass",
  "checks": [{"method": "原文と設計の照合", "result": "対象ごとの応答と小規模試作の確認方法が設計されている"}],
  "findings": [],
  "unverified": []
}
```

```bash
python3 .hw/runtime/hw.py intent-gate --task night --phase design \
  --base origin/main --intent-review .hw/evidence/night-design-review.json
```

これは主担当が使う設計の確認点。Builder起動を強制的に制御するフックではない。
設計を変えたら再評価する。設計のpassは実際の楽しさを立証しない。

### 3. 小さな実物で確認し、納品判定へつなぐ

最小試作を動かし、必要な操作・画面・音・利用者観察を証拠として残す。どこで、誰が、
何を実施したか、取得対象と未確認範囲を書く。証拠をcommit後、delivery入力を作る。

```bash
python3 .hw/runtime/hw.py context --task night --role intent-reviewer \
  --phase delivery --base origin/main --output /tmp/night-delivery
```

原文・baseline・設計・明示したevidenceが入る。role省略は不可、phase省略時はdelivery。
コードや静止画だけで音・タッチ・初見の楽しさを確認したことにしない。
動画や実機が必要なのにアクセスできなければinsufficient_evidenceとして具体的に残す。
unverifiedには当該段階の合否に必要な未確認だけを入れ、範囲外や任意の留意点は
limitationsに書く。必須の未確認をlimitationsへ移して合格にすることは認めない。
同じ形式でphase=deliveryの結果を保存する。段階間の判定流用は拒否される。

```bash
python3 .hw/runtime/hw.py gate --base origin/main \
  --review .hw/evidence/technical-review.json \
  --intent-review .hw/evidence/night-delivery-review.json
```

gateは--taskの選択にかかわらず、変更に適用される全必須taskのdelivery判定を要求する。
複数なら--intent-reviewを繰り返す。技術レビューへ意図レビューを渡しても通らない。
revise / insufficient_evidence、unverifiedが残るpass、blocking指摘、別task、古い
bindingは失敗。status=checks_passedは従来どおりで、intent_review.status/tasksが加わる。
実行承認は発行しない。ローカルJSONの偽造や実際の閲覧順まではランナーで防げない。

### CIで必須化する場合

プロジェクト所有のproject.jsonに任意で追加する。既存ファイルは更新処理で自動変更しない。

```json
"intent_review": {
  "paths": ["app/night/*", "lib/night-town/*"],
  "check_name": "hw-intent",
  "app_id": 123456,
  "model": "fable"
}
```

pathsに合う変更には有効なtaskと意図レビューが必要。既存のreview.pathsとは別軸。
baseで有効なtaskの削除・無効化・関連範囲の縮小で、同じ変更から要件を外すことはできない。
CIは保護されたbase方針を用い、taskごとに次のCheck Runを要求する。

- name = `<intent_review.check_name>/<task-id>`（例: hw-intent/night）
- app.id = intent_review.app_id、head_sha = 対象HEAD
- external_id = そのtaskのdelivery manifest.binding
- status = completed、conclusion = success

技術側と同じApp・同じcheck名の組合せは拒否する。ただし別名だけでは独立性の証明に
ならない。発行側は意図レビューを独立に実施し、未確認や不一致があればsuccessにしない。
ローカルJSONへのfallbackはない。Appの配備・資格情報・必須チェック・変更保護は別途必要。
doctorのconfigured_not_verifiedは設定値の存在だけを示し、外部接続済みを意味しない。

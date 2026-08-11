# Verification Contract — hw-fable-chunk-review

対象: `.hw/fable_review.py` のチャンク分割レビュー。
検証はすべて機械実行(mktemp 一時リポジトリ + `HW_FABLE_CLI` stub)。
stub は受信プロンプトを連番ファイルへ保存し、指示された JSON verdict を返す。
「stub が呼ばれたか」はプロンプト保存ファイルの有無で判定する(出力文字列では
判定しない)。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | 材料+契約+ヘッダが既定上限以下のとき、CLI 呼び出しは正確に1回で、プロンプトは現行形式(ヘッダ・契約全文・差分全文を含み、chunk 表記を含まない)。verdict に `chunked` フィールドが無いか false | unit | `.hw/tests/fable-review-chunking.test.sh` の該当ケース出力 |
| AT-002 | 合計材料 246,456 bytes 以上の fixture(env 未設定=既定上限 200000)で、上限超過エラー(exit 2)にならず CLI が2回以上呼ばれ、レビューが完了する | unit | 同上(stub のプロンプト保存ファイル数 ≥ 2) |
| AT-003 | AT-002 の全チャンクプロンプトが各々 200,000 bytes 以下(`HW_FABLE_MAX_PROMPT_BYTES` 未設定のまま) | unit | 保存された各プロンプトの `wc -c` 検査 |
| AT-004 | 被覆完全性: 全チャンクに配られた per-file 差分を連結した集合が、全材料の per-file 分割と「ファイル集合一致」かつ「ファイルごとに byte 一致」 | unit | テスト内で材料を独立に分割して突合した結果 |
| AT-005 | チャンク実行後の verdict の `target_sha256` が全材料の sha256 と一致し、その READY verdict を commit した状態で `python3 .hw/check_review_verdict.py <t>` が exit 0 | unit | check_review_verdict の実行ログ |
| AT-006 | チャンク2に NEEDS_REPAIR(violations 1件)を注入すると、全体 verdict は NEEDS_REPAIR・exit 1 で、当該 violation が verdict の violations に含まれる | unit | verdict JSON と exit code |
| AT-007 | チャンク数が `HW_FABLE_MAX_CHUNKS`(既定4)を超える fixture では exit 2 で停止し、CLI は1回も呼ばれない | unit | exit code + プロンプト保存ファイル 0 件 |
| AT-008 | 単一ファイルの差分がチャンク予算超の fixture では exit 2 で停止し、エラーメッセージに当該ファイルパスを含み、CLI は1回も呼ばれない | unit | stderr + プロンプト保存ファイル 0 件 |
| AT-009 | `.hw/verify.sh` が本テストを実行しており、テストを故意に失敗させると verify.sh が非0で終わる | integration | verify.sh の変更行 + 故意失敗時の exit code |

## 想定迂回と対策(各ATが潰す抜け道)

| 迂回シナリオ | 潰す検査 |
|---|---|
| チャンク化を実装せず既定上限だけ引き上げて AT-002 を通す | AT-003(env 未設定で各プロンプト ≤ 200,000 を数値検査) |
| 一部ファイルだけレビューし残りを黙って捨てる(打ち切り) | AT-004(byte 一致の被覆検査) |
| チャンクごとに target hash を切って束縛を弱める | AT-005(全材料 sha256 との等値 + check_review_verdict 通過) |
| 失敗チャンクがあっても多数決や最後の値で READY にする | AT-006(1チャンク失敗で全体 NEEDS_REPAIR) |
| 上限なしで無限にチャンク化しコストとレビュー形骸化を招く | AT-007(チャンク数上限で fail-closed) |
| 巨大単一ファイルを黙って truncate して詰める | AT-008(分割不能は停止)+ AT-004(byte 一致) |
| テストを書くが CI から呼ばれない(死んだテスト) | AT-009(verify.sh 配線と故意失敗の伝播) |
| 小差分の挙動を変えてしまう(回帰) | AT-001(1回呼び出し・現行形式・chunked なし) |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | 上限以下の既存 M/L レビューの挙動(呼び出し回数1・プロンプト構成・exit code 規約) | unit | AT-001 |
| FP-002 | clean-tree 必須・M/L 限定・base-commit 祖先検査など `run_review` 冒頭の既存ガードが全経路で先に効く | unit | dirty tree fixture でチャンク経路に入らず exit 2 |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | チャンク実行の総 CLI 呼び出しは `HW_FABLE_MAX_CHUNKS`(既定4)以下。verdict の `chunk_count` と保存プロンプト数が一致 | unit | AT-002/AT-007 の計数 |
| NFT-002 | `target_material` の git diff フラグに `-M`/`-C` を追加していない(実測 246,456→246,456 bytes でゼロ効果のため不採用。base `4cac66f`..`fd657c1`) | source check | `git diff base-commit..HEAD -- .hw/fable_review.py` の目視 + grep |
| NFT-003 | チャンク実行時の verdict は `confidence` が "high" にならない(上限 medium) | unit | 全チャンク high を注入して全体 medium を確認 |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes
- hash-bound approval required: yes
- research brief required: no
- option matrix required: no(設計書 `.pipeline/plans/hw-harness-prime-gaps-2026-08/design.md` §1 に記録済み)
- kpi backcast roadmap required: no
- external consultation required: no

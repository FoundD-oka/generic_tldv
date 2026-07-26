# generic_tldv 全体リファクタリング v2 検証契約

## 1. 位置付けと正本

- Task ID: `full-repo-refactoring-2026-07-24-v2`
- 調査基準: `main@b2bcae8e88f0e73fe95343ee3a694a3afc4e1028`
- 実装内容・順序・各項目の完了条件: `plan.md`
- フェーズ、配備wave、operator停止点: `release-boundaries.json`
- 視覚回帰のシナリオ一覧: `planned-visual-changes.json`
- RF項目数は129件で、`plan.md`の`### RF-*`見出し順を変更しない。

`release-boundaries.json`をフェーズと配備境界の正本とする。
正式フェーズは次の3つである。

| phase | 項目範囲 | 目的 |
|---|---|---|
| Phase 1 | RF-00A〜RF-30 | 安全網、認可、秘密値、credential移行、非同期競合 |
| Phase 2 | RF-31〜RF-51 | tests3、配備、Harnessのfail-closed化 |
| Phase 3 | RF-52〜RF-75F | move-only抽出、重複・デッド資産・lint・文書の閉鎖 |

`plan.md`、本契約、`release-boundaries.json`の間に、項目範囲、依存、必須コマンド、期待結果の矛盾がある場合は推測で補わず停止して報告する。
表現差だけで実装意味が一意なら、各RF項目の具体的な変更内容と完了条件を優先する。

## 2. 採用しない運用プロトコル

本計画では、全byteレビュー、文書hash固定、immutable archive、archive hash chain、tribunal、Fable/Codex dual consensus、releaseごとの独立QA、merge permit、merge attestation、exactly-once receipt、single-write、ack喪失時の`verify-existing`、`env -i` trusted bootstrap、Git blobのrepo外抽出、`O_EXCL|O_NOFOLLOW` secure-copy、attempt token、state-transfer manifest、15分cutover permit、continuous freeze lease、final aggregatorを採用しない。

安全性は、1項目1コミット、明示テスト、tracked GitNexus wrapperの`impact`/`detect-changes`、配備waveごとのdrain、通常のCI、diff review、人間merge、標準的なrevertで担保する。

## 3. 作業開始前と項目0

実行者はリポジトリルートで次を実行し、結果を作業記録へ残す。

```bash
git status --short
git rev-parse HEAD
git branch --show-current
```

- 既存のtracked変更と未追跡物を列挙し、ユーザー所有物をstage、stash、移動、削除しない。
- 基準HEADから専用branchと隔離worktreeを作る。既存の作業treeへ上書きしない。
- 作業前commitは、実行者が新規作成した計画・checkpointだけを対象にする。既存ユーザー差分を混ぜない。
- RF-00AではGitNexusを開始条件にしない。RF-00Nのtracked wrapper作成とdependency installが完了した直後に`bash scripts/test/run-gitnexus-refactor.sh analyze`を実行し、失敗時はRF-00Eへ進まず停止する。
- Python、Node、npm、Docker、DB等の必須環境がない場合、skipやpassへ変換せず`blocked: <不足物>`として停止する。
- RF-00A、RF-00N、RF-00E、RF-00B、RF-00C、RF-00Dを順番どおり実行し、安全網が通る前にRF-01へ進まない。
- RF-00BとRF-00Dの特性テストは現行挙動を固定する。既知の不具合は対象RF項目まで限定的なxfailとし、対象項目で通常passへ戻す。
- RF-00Eは`python3.11` exact、repo内固定`RF_ENV_ROOT`、共通constraintsで再構築する4 venv、3 npm lock、Dashboard/Core各Chromiumを`plan.md`記載のliteral pathで検証する。不足・解決差・lock差はblockedとし、system packageや別requirementsへfallbackしない。
- RF-00Cで18枚の視覚baselineを取得し、同じcommitへfixture、Playwright spec、snapshotを含める。Transcriptの`panel20.test.ts`は必須fixtureを使う5件のnormal pass、skip 0へ変える。

現在の`.claude/hooks`は、Git管理外の旧`harness-init` absolute symlinkでbrokenである。
旧commitのpinned配置や`closed/harness-init`へのfallbackを実装開始条件にしない。
この不具合だけを理由にRF項目のローカル実装・テストを止めない。

ただし、`.claude/hooks/pr-ready-gate.sh`が実在かつ実行可能でない限り、PR-readyや完遂を主張してはいけない。
環境所有者が別のHarness保守作業で、現行`hw-init`由来のproject-owned regular fileへ移行する。
実行者は本リファクタリングの項目commitへsymlink修理やHarness世代移行を混ぜない。

## 4. 各RF項目の共通手順

各項目は1件ずつ、次の順序で実施する。
RF-00A/RF-00Nだけはwrapper作成前のためstep 3を3章のbootstrap手順へ置き換え、それ以外のstepは省略しない。
`refactor-item-matrix.json`は129 IDを見出し順に持ち、stateは`planned|active`だけとする。RF-00E作業中にA→N→Eを順にactive化し、以後は自IDだけをplanned→activeへ変えて同じitem commitへ含める。runnerは自IDと全先行IDがactive、全後続IDがplannedでなければ実行せず、最終planned 0とする。
RF-00B以降のmatrix自ID stateは暗黙の共通targetである。commands/nodeids/suites/他IDは変更せず、planned entryのtest不存在は許可するがactive entryの不存在、0件、skipは失敗にする。
各itemのwrite unionは、対象欄のwrite path、自ID matrix state 1値、完了条件のliteral `path::nodeid`またはrepo内で一意な既存test basenameから解決したexact test file、11 resolverだけ`plan.md` 2.3表のowner exact test file/parameter marker、の和集合である。braceと直前full path継承を展開後、testは変更/新規作成だけを許す。先行pathなし/区切り越え/曖昧継承、production fallback、別test/parameter、他ID state変更、union外pathは停止する。required suite全体とread-only inventoryはwrite targetにしない。

1. `plan.md`記載の依存IDと、必要なOP停止点がすべて完了済みか確認する。
2. `ITEM_BASE_SHA="$(git rev-parse HEAD)"`を記録する。
3. 対象symbolごとに`bash scripts/test/run-gitnexus-refactor.sh impact --target "<symbol-or-file>" --direction upstream`を実行し、直接caller、関連process、riskを記録する。
4. HIGHまたはCRITICALなら編集前にユーザーへ報告する。計画外processへ到達した場合は再計画まで停止する。
5. bug修正項目は、項目に指定されたtestが意図した理由でredになることを先に確認する。
6. `plan.md` 2.1のwrite unionだけを変更する。
7. `完了条件`に列挙されたtest名とコマンドを省略せず実行する。
8. `git diff --check`後、write unionのexact pathだけをstageし、`git diff --cached --check`とcached path一覧を確認する。ユーザー所有path、union外path、欠落した新規fileがあればunstage/deleteせず停止する。
9. staged新規fileを含めるため`bash scripts/test/run-gitnexus-refactor.sh analyze --force`を先に実行し、次の共通検査を実行する。

```bash
bash scripts/test/run-gitnexus-refactor.sh detect-changes --base-ref "$ITEM_BASE_SHA"
git status --short
```

10. detectがexit 0・非空・partial/errorなしで、reported risk/processが計画内、symbolへmapされるcached codeは期待symbol/processを含むことを確認する。JSON/PNG/lockfile等symbolなしpathの表示は要求せず、step 8のcached write-union照合を完全性の正本とする。解析後にindex/worktreeが変わった場合はstep 7から再検証し、変化なしならliteral subjectで1 commitだけ作成する。

テストは0件collection、unexpected skip、xfail、xpass、warningによる成功扱いを禁止する。`plan.md` 2.3のintentional exclusionだけは収集前にexact inventoryを検査して除外し、収集対象のskipは0とする。
Docker、DB、registry、browser等が必須のコマンドは、環境不足を成功として扱わない。
秘密値canaryがresponse、URL、log、Redis、job、evidenceへ現れた場合は即時停止する。
予定外のpublic API、status、DB metadata、Redis key/channel、credential境界の差は、テストが通っても不合格とする。

### 4.1 失敗時とrollback

- commit前の失敗は変更をcommitせず、失敗コマンド、期待値、実値、現在のdiffを報告する。
- RF-00A失敗時は元workspace status byte一致を確認し、このattemptのexact path/branchを列挙して保持・停止する。自動cleanup/force削除はせず、原因修正後に人間指定の`RF_BOOTSTRAP_ATTEMPT=retry-N`（Nは正整数、`retry-1`可、0/非数字不可）から同base/同artifact bytesでsuffix付きの全新規path/branchへ再試行する。旧attemptはread-onlyとし、全review、新master task、archive/hash chainを要求しない。
- 同じ必須testが原因未解明のまま2回失敗したら自動修正を続けず停止する。
- 直前までの合格commitを再開点として保持し、`git reset --hard`、force push、ユーザー差分の削除を行わない。
- commit後に回帰を発見した場合は、その項目だけを戻す通常のrevert commitまたはrevert PRを作る。
- 配備後の失敗は、各waveで保持した直前合格artifactへ切り戻す。legacy認証を無認証状態で復活させない。
- rollback後も当該項目を完了扱いにせず、原因と再開条件を報告する。

## 5. フェーズgateと配備wave

各Phase末尾で、項目ごとのtestをreceipt生成目的で再実行せず、そのPhaseが触れたサービスのfull relevant suiteを一度実行する。
加えて、lint、build、`git diff --check`、Phase baseに対するtracked wrapperの`detect-changes`、項目ID・commit subject・順序の一致を確認する。
全件pass後、通常のdiff reviewを受けてから次Phaseへ進む。

配備は`release-boundaries.json`のD1〜D7を正本とする。

| wave | 項目範囲 | 後続停止点 |
|---|---|---|
| D1 | RF-00A〜RF-05B | OP-05A-DRAIN |
| D2 | RF-05C〜RF-05D1B | OP-05D-DRAIN |
| D3 | RF-05C2〜RF-06C1 | OP-06C-DRAIN |
| D4 | RF-05F2〜RF-06D1 | OP-06D-DRAIN |
| D5 | RF-06D2〜RF-06F | OP-06F-DRAIN |
| D6 | RF-06G〜RF-09A | OP-09-DRAIN |
| D7 | RF-09B〜RF-75F | なし |

D1〜D6は互換cutoverの技術配備境界である。D7は単一PRを意味せず、M1/M2/M3または項目単位の通常PRへ分割できる。どの分割でもRF見出し順と1項目1commitを維持する。

D1〜D6は互換実装をmerge・配備した後、認可済みoperatorが対応するOP停止点を合格させる。
OP evidenceは、checkpoint ID、wave ID、配備commit、対象環境、観測開始・終了時刻、観測時間、旧経路count、新経路成功count、operator ID、判定を持つ1つのJSONまたはMarkdownでよい。
hash、receipt、attestationは要求しない。

各OPで`release-boundaries.json.minimum_gate`の観測時間、旧経路0、新経路1件以上、rollback artifact保持を確認する。
OP-05A不合格ならRF-05C、OP-05DならRF-05C2、OP-06CならRF-05F2、OP-06DならRF-06D2、OP-06FならRF-06G、OP-09ならRF-09Bへ進まない。
fixture counterをproduction drainの代用にしない。
実行AIはproductionへ配備せず、operator evidenceを捏造せず、権限がなければ`blocked: operator evidence pending`として停止する。

## 6. Visual/E2E契約

正本は`planned-visual-changes.json`、実装は`services/dashboard/tests/e2e/refactor-visual.spec.ts`の1ファイルとする。
9シナリオをdesktop `1440x900`、mobile `390x844`の2 projectで実行し、合計18 snapshotを作る。
JSONはinventoryであり実行DSLではない。操作はPlaywright spec内へ通常の`goto`、`fill`、`click`、`expect`として直接記述する。

全HTTP response、43件のlive transcript、WebSocket frameは具象JSONとしてcommitする。
音声と動画は固定fixture binaryをcommitする。
template materialization、recipe、projection、canonical serialization、receipt、fixture hashは作らない。
runtime configは固定test origin `http://127.0.0.1:4173`と`ws://127.0.0.1:4173/ws`を直接持つ。

```bash
cd services/dashboard
REFRACTOR_VISUAL_MODE=baseline npm run test:e2e:refactor -- --update-snapshots
REFRACTOR_VISUAL_MODE=final npm run test:e2e:refactor
```

baseline commandはRF-00Cで一度だけ実行し、fixture、spec、18 snapshotを同じcommitへ入れる。
final commandではsnapshotを更新しない。
視覚差なしのシナリオは画面全体を`maxDiffPixels: 0`で比較する。
S02、S03、S06はJSONの`allowed_selectors`だけをPlaywrightの`mask`へ渡し、許可領域外を0 pixel差で比較する。
許可領域内はJSON記載のsemantic assertionをすべてpassさせる。

各testは新しいBrowserContextを使い、固定locale、timezone、clock、color scheme、reduced motionを設定する。
未mock API、外部通信、page error、console error、request failure、unexpected 4xx/5xx、fixture不足、子process残存、実tokenが1件でもあれば失敗する。

## 7. PR、merge、最終review

各PRは対象waveまたはPhaseの項目commitだけを含める。
項目単位のrollback境界を残すため、squashせずitem commitを保持するmergeを原則とする。
force push、review後の履歴書換え、無関係なcherry-pickを行わない。

PR-readyの条件は、対象test、Phase suite、Visual対象時のE2E、tracked wrapperの`detect-changes`、通常CI、reviewer 1名以上のdiff reviewがすべてpassすること。
L作業に対するAGENTS.mdの要件として外部相談が必要な場合は、全体最終時に1回だけ実行し、短いsidechain synthesisへまとめる。
releaseごとのFable、dual consensus、tribunalは行わない。

`.claude/hooks/pr-ready-gate.sh`が実在・実行可能になった後、最終PR前に次を実行する。

```bash
bash .claude/hooks/pr-ready-gate.sh full-repo-refactoring-2026-07-24-v2
```

hookがbrokenまたは不存在なら、実装結果を保持し`blocked: current project PR-ready hook is unavailable`と報告する。
CI green、diff review、人間承認の後だけ人間がmergeする。

## 8. 完了判定

- 129項目が`plan.md`の順序で、1項目1コミット、literal subject一致になっている。
- 全項目testと3つのPhase suiteがpassし、unexpected skip、xfail、xpassが0である。
- GitNexusで計画外HIGH／CRITICAL impactが0である。
- public API、status、DB metadata、Redis、credential境界の予定外差が0である。
- 秘密値canaryのresponse、URL、log、Redis、job、evidence露出が0である。
- D1〜D6の削除項目は対応する6つのOP停止点合格後にだけ実施されている。
- 9シナリオ×2 viewportのVisual E2Eがpassし、未承認の領域外差が0である。
- 通常CI、diff review、利用可能なPR-ready gate、人間mergeが完了している。

一つでも満たせない場合は、最後に合格したitemまたはwaveを明示し、失敗コマンド、影響範囲、rollback状態、再開条件を日本語で報告する。
未完の項目やoperator待ちを完遂と主張しない。

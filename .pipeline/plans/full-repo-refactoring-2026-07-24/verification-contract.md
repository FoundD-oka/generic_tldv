# generic_tldv 全体リファクタリング検証契約

## 1. 契約の位置付け

- Master Task: `full-repo-refactoring-2026-07-24`
- 調査基準: `main@b2bcae8e88f0e73fe95343ee3a694a3afc4e1028`
- authoritative plan: `.pipeline/plans/full-repo-refactoring-2026-07-24/plan.md`
- visual contract: `.pipeline/plans/full-repo-refactoring-2026-07-24/planned-visual-changes.json`
- release contract: `.pipeline/plans/full-repo-refactoring-2026-07-24/release-boundaries.json`
- 規模: L

plan本文と本契約が食い違う場合は実装を始めず、両方を同一hashで再reviewする。実行者が本文を補完・推測してよいという意味にはしない。

7 releaseはrelease task単位でcheckpoint、build、test report、review、QA、outcome、approval、merge、operator evidenceを分離する。共有するのはreview済みmaster static 8 file、R1のimmutable visual baseline、R1で固定したdependency/GitNexus runtimeだけである。

## 2. Release境界とbase chain

| release | item範囲 | merge後operator gate | expected item |
|---|---|---|---|
| r1 | RF-00A→RF-00N→RF-00E→RF-00B→RF-00C→RF-00D→RF-01…RF-05B | op-05a-drain | RF-05B |
| r2 | RF-05C〜RF-05D1B | op-05d-drain | RF-05D1 |
| r3 | RF-05C2〜RF-06C1 | op-06c-drain | RF-06C1 |
| r4 | RF-05F2〜RF-06D1 | op-06d-drain | RF-06D1 |
| r5 | RF-06D2〜RF-06F | op-06f-drain | RF-06F |
| r6 | RF-06G〜RF-09A | op-09-drain | RF-09A |
| r7 | RF-09B〜RF-75F | なし | なし |

R1 baseは上記調査SHAとexact一致させる。R2〜R7を作る時点では、fresh `origin/main`、直前releaseのcanonical merge record `merged_main_sha`、直前operator gateのcomponent source SHAが同じ値でなければならない。merge後の検証で「現在のorigin/mainが当時のbaseのまま」を要求しない。過去releaseのbase/head/mergeはimmutable archiveとmerge recordのhash chainで検証する。

初回branchは`codex/$TASK`。次だけを追加で許す。RF-00A initial transactionがcommit後captureまで到達しない場合は同master task内で再試行せずblockedとし、別master task IDでstatic reviewからやり直す。

- R2〜R7 bootstrap retry: `codex/$TASK-retry-release-bootstrap-<32 lowercase hex>`。直前archiveから発行した`new-release-attempt` receiptへbindする。
- 通常item retry: `codex/$TASK-retry-<failed-id>-<token>`。last-good SHAと`state-transfer.json`へbindする。

既存branch/pathの削除、rename、force reuse、squash、rebase、cherry-pick、release branch内merge、打消しcommitは禁止する。first-parentにはplan見出し順の各RF subjectがexact 1件ずつ存在しなければならない。

## 3. 実装開始前のstatic review gate

RF-00Aを含む実装開始前に次をすべて満たす。

1. 環境所有者が隔離packageへ事前配置したcanonical `claude-dotfiles@fb9bd5f0d2a20a52d9d48dc56d4080a65c5c3233`で4 absolute symlinkが解決する。計画作成hostでは外部checkoutが`70d84688a33815c892cd3da3900b80c19a159ae7`へ進み旧targetがbrokenのため未合格であり、実行AIはcheckout switch、symlink変更、current `closed/harness-init/`へのfallbackをしない。
2. `plan.md`、本契約、visual JSON、release boundaries JSONを独立したread-only reviewer 2名が全byte reviewする。
3. 2 reviewとcoverage manifestはduplicate JSON key、unknown/missing field、duplicate/extra/reordered path・reviewer・range、および`start_byte/end_byte/size`のboolean-as-integerを拒否するexact schemaで、4 static fileのtop-level reviewed SHA、path、size、SHA-256、全byte range、distinct reviewer/run IDへbindし、critical/high/MUST_FIX 0。数値欄はJSON integerかつPythonで`type(value) is int`（`bool`不可）として検証する。
4. Fable plan review、Codex ultra plan review、dual consensusが同じ4-file material hashへbindし、open MUST_FIX 0、consensus AGREE。
5. `.claude/hooks/pre-implementation-review-gate.sh "$MASTER_TASK"`がpass。

外部送信承認またはloginがなければ「計画完成・実装開始は権限待ち」で停止する。truncateされた外部出力を全文reviewに代用しない。

## 4. Bootstrap acceptance

### RF-00A

- 既存ユーザー未追跡物をstage、stash、移動、削除しない。
- master static/review/gateとR1 approved copy/lifecycle/baseline、tracked bootstrap helper/testだけをresolverのexact setでcommitする。
- `python3 -I -S scripts/test/test_refactor_bootstrap_receipt.py -v`と本文の`rf-00a-bootstrap-verifier`がpass。
- commit後の`rf-00a-postcommit` receiptがbranch/base/head、approved copy 5 hash、lifecycle/build/state/sessionへbind。
- pre/post commitを問わずactual failure、またはcanonical `rf-00a-postcommit` receiptをverifyできないprocess喪失はterminal。capture single-write後のstdout/process ack喪失だけ、同じbranch/head/taskでstandalone `verify-only`が全hashをpassする場合に限りrecoverable。その他は旧branch/worktree/artifactを保持し、別master task IDでstatic/external/independent reviewからやり直す。未commit helperや旧review gateを流用しない。

### RF-00N

- exact 5 pathだけでGitNexus 1.6.9 runtime、lock、wrapper、testをcommit。
- commit前はfake childを用いる標準ライブラリtest、commit後はtracked wrapperでRF-00A parentとのcompare。
- `rf-00n-postcommit` receiptをsingle-writeし、ack喪失は`verify-only`。

### RF-00EとR2〜R7 bootstrap

- Python 3.11/3.12、Node 22、npm 10、GitNexus 1.6.9以外はblocked。
- 4 venv、3 application lock、GitNexus lock、constraints、`pip freeze --all`をhash固定し、`npm install`、global/latest、lock rewriteを禁止。
- R2〜R7 bootstrap CLIは`--attempt-id`必須。initialとreceipt発行済みretry branch/pathだけを許す。
- `release-bootstrap` capture前失敗はcurrent partial taskをrestoreせず、直前immutable archiveから別attempt/branch/pathへ全文再実行する。
- capture後worktree消失はreceiptからrestore/verifyし、GitNexus runtimeをlockどおり再installする。

## 5. 項目共通acceptance

RF-00E以降、各項目で次を順番どおり実行する。

1. release task artifactを除くimplementation tree clean。
2. `ITEM_BASE_SHA`、対象resolverのwrite/read/runtime scope、existing production impact anchorを保存。
3. tracked GitNexusでpre analyzeと対象symbol/fileごとのupstream impact。HIGH/CRITICALはユーザーの明示承認まで編集禁止。計画外processへ到達したら承認があっても再計画。
4. bug項目は指定testが意図した理由でredになることを先に記録。
5. 本体変更。
6. `bash scripts/test/run-refactor-item.sh <ID>`。
7. `bash scripts/test/run-required-suites.sh <ID>`。
8. `git diff --check`、post analyze/impact、detect compare to `ITEM_BASE_SHA`。
9. actual cached pathがnon-emptyかつ`cached_paths ⊆ write_target_allowlist`、`required_changed_targets ⊆ cached_paths`。read-only/runtime/user pathはstage 0。
10. 当該matrix entryだけ`planned -> active`にして、plan literal subjectで1 commit。

runnerは`pytest|vitest|core-registry|shell-json|argv`だけ。test runnerは0 collection、missing required name、unexpected skip/xfail/xpassを失敗にする。`shell-json`はZoom shell test 1 pathだけ。argvはAppendix A.3/A.5の配列とexpected objectにbyte一致し、各command exact 1件の`operator="argv_expectation"` assertionをmachine reportへ持つ。RF-00NのPython argvはabsolute `/usr/bin/python3,-I,-S`とcurrent HEADのtracked exact script/flagだけを許し、root所有・group/world非writable・Python 3.9以上を検証してPATH探索、別script、追加引数を拒否する。missing Docker/DB/registryはskip/passでなく`blocked,exit_code=2`。

同じtest 2回失敗、未知status、空report、missing script、scope外差、予定外API/schema/DB/Redis差、credential canary、GitNexus計画外riskで中断する。checkpoint state=`building`の単一item commit前failureだけ、失敗branch/evidence全体をcontrol rootへappend-only保全し、last-good SHA以前のapproved copy/lifecycle/baseline/bootstrap/completed item・phase evidenceだけをcutoff manifest付きで新attemptへ移して同じ項目を再実行できる。failed item以降、release/full/review/QA/outcome/approval/closure/postmerge artifactと元session ledger全体はcopyしない。

## 6. Phase/release gate

phase gateはordered item→stable-unique suite→phase assertionを一度だけ実行し、canonical pass JSONをsingle-writeする。ack喪失は`--verify-existing`だけ。R7の5 phase gateは各phase末尾HEADへbindする。

phase/release/closure gateの実failureはentry stateに応じて合法遷移だけを使う。phase中は`building -> blocked`、build済みだが未verifyingなら`built -> verifying -> verification_failed -> blocked`、release/closure中は`verifying -> verification_failed -> blocked`。`awaiting_approval|approved|merged|completed`では過去stateを変更せずfailed selection/receiptをappend-onlyで残してterminal停止する。同task内のsource/static/matrix/commitを変更せず、修復は別task IDのremediation planを独立reviewしてから行う。ack喪失だけは同じcanonical selection/stage attemptのverify-existingでwriter/test再実行なしに再開する。

release gateは次のmodeを分離する。

1. `--execute --attempt-id`: R1先頭からcurrent release末尾までのactive replayable prefixとstable-unique suiteを一度だけ実行し、attempt execution reportをsingle-write。
2. `--verify-execution`: 既存execution/report/bootstrap receipt importをread-only検証。
3. final GitNexus analyze/impact/detect。R7だけ既存item/suite/phase reportを再parseするfull verification。
4. `--finalize --attempt-id`: 上記source、commit history、scope、static、receipt chainを検証してcanonical release manifestをsingle-write。
5. `--verify-existing`: manifestとsource/hash/argvだけを再計算し、item/suite/GitNexus/E2Eを呼ばない。

RF-00A/RF-00N/RF-00Cをrelease gateで再実行しない。R7でもphase/item/suite reportを再生成・上書きしない。QA、approval、merge、operator、aggregatorはrelease gate時点の未来artifactなので要求しない。

## 7. Visual/E2E契約

R1 RF-00Cで同一staged treeからbaselineを1回だけ取得しcommitする。R7で同一release HEADからfinalを取得する。

- 9 scenario × desktop `1440x900` / mobile `390x844` = 18 PNG。
- baseline sourceは`git write-tree`、final sourceは`git rev-parse HEAD`。wrapper自身がbuild/startし、外部base URL/port/serverを拒否。CLIは両modeでexplicit `--task --attempt-id`を必須にし、baselineはmaster task+canonical RF-00C item attempt、finalはR7 task+selected closure attemptへexact bindする。evidence path、nonce、mtime、`latest`から逆推測しない。
- `.next/BUILD_ID`、served response header、wrapper算出SHA、manifest sourceが一致。
- action/fixture/assertionはvisual JSON exact。HTTP/WSはgoto前に全bindingをinstallし、未bind、未消費、過消費、別context流用を拒否。
- fixture metadataはtransport別exact allow-list、body authoring keyはvisual JSONのprojection表だけを使い、`exact_identity|meeting_identity -> root`、`data_exact -> data`、segments/recordingsのexact arrayをliteral DTO keyへ変換してからstrict fixture DTO contractのexact root/nested keyとclosed type unionを検証する。projection衝突、unknown metadata/authoring/DTO key、boolean-as-integer、非finite number、default値の意図しない残存は失敗。
- `http-json-template`だけはtracked templateを変更せず、loopback port/basePath固定後かつserver起動前に`.pipeline/tmp/<validated-task-id>/e2e/<canonical-attempt-id>/`のtask-owned untracked regular fileへallow-list placeholderをsingle-pass materializeする。receiptへ非secretのexact placeholder mapを保存し、visual JSONのcanonical UTF-8 serializationでmap/body/receipt hash inputを固定する。temp削除後もtracked Git blob+receiptから全hashを再構築できなければ不合格。tracked blob/worktree hash、placeholder aggregate、materialized body、receiptをmanifestへbindし、未知/残存placeholder、非loopback origin、capture後tracked差を失敗にする。manifestの`fixture_materialization_receipts`はtemplate件数exact（現在2件）で、template bindingのreceipt hashだけ64hex、concrete JSON/binary/fake-WS bindingはreceipt hashをJSON nullかつtracked/body hashを必須・同値にする。
- console/page/request error、unexpected 4xx/5xx、子process残存、実データ、実tokenは0。
- RGBA 1 channelでも差があれば1 diff pixel。stable scenarioは0 pixel、planned scenarioはexact selector union外0 pixelかつsemantic assertion全pass。
- baseline manifest/integrity/18 PNGはRF-00C commit blobと照合し、後続で再生成しない。

## 8. Security/cutover acceptance

- auth、identity、capability、service principalはmissing/empty/placeholderでstartup failureまたは副作用前401/403。
- raw API/service/relay/provider tokenはresponse、URL/query、log、Redis/job、evidence、別subject/workloadへ0。
- workloadへ渡せるraw credential例外は、RF-05Cのsubject-owned Agent `VEXA_BOT_API_TOKEN`、RF-06Eの同subject Agent `ANTHROPIC_API_KEY`、RF-06F/06Gのexact Zoom Botへ最大2時間の`ZOOM_SDK_JWT`だけ。
- audience/subject/owner/session/method/path/body/jti cross matrixは副作用0。
- Redis ACLはexact key/channel/commandだけ。temporary SCAN principalは本文のR1 migration 2件だけで、OP-05A後にcredential/grant/ref 0。permanent principalのSCAN/EVAL/KEYS/anonymousは0。
- workloadはnon-root、caps 0、no-new-privileges、read-only root、private writable path、cross-session ingress 0。Liteのroot `lite-init`例外はlistener前にpermanent dropし、readiness後root/capability process 0。
- compatibility credential削除は対応OP gate後だけ。
- R3〜R7のdecommission deploy直前にcurrent merged SHA/environment/fingerprintへbindした15分以内のfresh cutover permit、連続freeze lease、generation不変を確認し、配備後は別completion attestationを必須にする。

## 9. Preapproval closure

9.1〜9.7のoperator entrypointはplan 9.0のpreapproval trusted bootstrapだけとする。bootstrapはfresh `env -i /bin/bash --noprofile --norc`へ入り、control/release worktreeのGit identityとclean HEADを照合し、HEADの`refactor-preapproval-bootstrap.py`、trusted source manifest、closure runner、全transitive helperをrepo外regular fileへ全量抽出・blob/hash/構文検証してから実行する。worktree内script、ambient cwd/PATH/BASH_ENV/PYTHONPATH/GIT_*を実行sourceにしない。各呼出しはtask/base/head/control root/environment/parent attemptを再水和し、最初の副作用前にERR classifierを設置する。canonical parent/stage selectionはtask/stage/head/parent/environmentへsingle-writeし、attempt receipt作成後selection/stdout喪失時はmatching exact 1件だけを回収する。複数・failed・不一致はblocked、mtime/latest推測や自動別token発行は禁止する。

stage順:

1. `build-and-state`
2. `release-execution`
3. `release-finalize`
4. `manifest`
5. `tribunal`
6. `post-review`
7. `approval-target`
8. 人間承認後の`approval`

tribunalとQAは`prepare-external`でhash-bound input manifestをsingle-writeして`waiting_external`, exit 2、別sessionのabsolute external artifactを`import-external`でsecure-copy/O_EXCL importする二相方式とする。finder/adversarial/judge/synthesizer/QAはdistinct reviewer/run IDで、QAはimplementerと全reviewerから独立する。post Fable/Codex/dualはinput manifestを先に固定し、provider summaryごとに`exists -> validator only / absent -> run once`。login/authorization/provider disconnectはfailed selectionにせずwaiting、partial successは既存summaryをverifyして未作成suffixだけを呼ぶ。tribunal/post/QAが実際にblockならblocked outcomeを一度作り、`verifying -> verification_failed -> blocked`、exit 3。source/static/matrixを同taskで直さない。

`approval-target` stageはQA import、preapproval verifier、feedback prune、session success、outcome、immutable targetを順に完了した後、stateの未完suffixだけを進める。各artifactは不存在時だけcreate、既存時はsource/hashをverify-onlyする。session eventはtask/head/parent/stageから導いたidempotency keyでexact 1件。target作成後にsession/feedback/outcome/review/sourceを書かない。合法chainは`verifying -> verified -> evidence_ready -> awaiting_approval`。ack喪失時はtarget/source/state eventをverifyし、`verified|evidence_ready|awaiting_approval`の既済prefixをskipする。self-transition、target再生成、evidence_missingへの誤遷移を禁止する。

人間approval stageの順序は次へ固定する。

1. final approval completionが既にあればtarget/decision/prearchive/archive/completionをverify-onlyして終了する。このack-loss recoveryではmerge後の`origin/main=release_base`を要求しない。
2. completionがなければimmutable target hashとcaller approverを照合し、decision新規作成直前だけfresh baseを確認する。
3. approval decisionをcreateまたは`--verify-existing`。immutable approval modeはdecisionだけを書き、manifest/pack/target/source/stateを変更しない。
4. approval/hash/backcast/Fable/Codex/dual validator。
5. state `awaiting_approval -> approved`または既存approvedをverify。
6. PR-ready gateをcreate、既存時はcanonical `--verify-existing`。
7. `prearchive-ready`新規作成直前にfresh baseを再確認し、target/decision/approved event/PR-ready/stage attemptへbind。既存時はverify-only。
8. premerge archiveをcreateまたはverifyし、manifest SHA-256を計算。新規archive直前にもfresh baseを確認する。
9. archive外final approval completionをcreateまたはverifyし、prearchive receipt SHA-256とarchive manifest SHA-256へbind。

callerが渡したtarget SHA文字列またはhuman approverの不一致はsource driftではない。field/value hashへbindしたappend-only input-rejection receiptだけを残してexit 2とし、state、target、decision、selected stage attemptを変更しない。正しい提示済み値で同selectionを再開できる。`expired_approval`へ進められるのは、再計算したorigin/baseまたはimmutable target/sourceが実際に変化し、entry stateが`awaiting_approval`の場合だけである。state=`approved`後のdriftはapprovedを保持したままarchive/merge前で失敗させる。actual failureはfailed receiptでterminalとし、同attempt再開や自動rolloverをしない。

archiveはapproval decision、approved state/event、PR-ready、prearchive-ready receiptを含む。final approval completionはarchive manifest SHA-256をbindするためarchive外のcontrol-root canonical stage directoryだけに置き、archive memberへ含めない。repo-relative memberは`premerge/files/<repo-relative-path>`、trusted postmerge launcher/helperはrelease HEADのGit objectから`premerge/trusted-launchers/`へ抽出し、repo path/blob SHA/archive SHAをmanifestへ固定する。manifestは自己hashをmemberへ含めず、自己hashはfinal approval completion、merge record、final aggregatorが保持する。

## 10. Merge/postmerge contract

postmergeの正本は`$ARCHIVE_ROOT/$TASK/premerge`、Git release HEAD object、control-root archive外final approval completion、`$ARCHIVE_ROOT/$TASK/postmerge/lifecycle`であり、release worktreeやlocal mainではない。

各9.8 invocationは人間承認時に別経路で提示されたapproval-target SHAをroot anchorとして必須入力にし、最初のcommandからfresh `/usr/bin/env -i /bin/bash --noprofile --norc`へ入る。root所有・group/world非writableなabsolute `/usr/bin/env,/usr/bin/python3,/usr/bin/git,/bin/bash`をapproval targetのexact 4-key toolchain（envはversion null、他3件はexact version）へ照合する。control repository ID/origin/common-dir/object format、archive内prearchive-ready、archive外final completion、archive manifestを順に照合する。release HEADのrunnerと全transitive helperはGit blobをrepo外0700 temporary rootのregular fileへ**全量抽出後**、target source、Git tree mode/blob、archive manifest member/mode、抽出SHA-256を比較し、`bash -n`成功後だけ実行する。外部attestationは元pathをopen後全量readし、temporary rootへ`O_EXCL|O_NOFOLLOW` secure-copyしたhash-bound fileだけをrunnerへ渡す。runner cwdは検証済みcontrol root realpathへ固定しreceiptへbindする。run exit 0だけで成功とせず、同じselection/inputで`--verify-existing`を別process実行してcompletion/output/state hashを再検証した後だけtemporary rootを削除する。`git show | bash`、stdin/FIFO、caller worktree、archive member、ambient PATH/BASH_ENV/PYTHONPATH/GIT_*からの直接実行は禁止する。worktreeが消失していてもcanonical selection/lifecycle storeからresumeできなければ不合格。

postmerge stage:

1. `premerge-lease`: trusted GitHub App/merge queueの5分permitをbase/head/tree/check/environmentへbind。base/head/tree/queue driftはmerge側でatomic reject。
2. 認可済み経路だけがfast-forwardまたはitem commitを保持したno-ff merge。squash/rebase禁止。
3. `merge-record`: canonical merge attestationをsingle-write copyし、remote ref/tree、permit、approval、environmentを照合。recordへ`approval_stage_attempt_id,completion_sha256,parent_closure_attempt_id`を固定し、state `approved -> merged`。
4. R3〜R7 `cutover-predeploy`と別の`cutover-postdeploy`。
5. R1〜R6 `operator`でdrain evidenceを検証し`merged -> completed`。
6. R7 `aggregate`で7 archive/merge、6 operator、5 cutover completion、**全7 release**のtribunal/post/QA/outcome/approvalとclosure/postmerge selection/attempt/completion/lifecycle chain、R7 phase/UI/itemをordered `release_chains[7]`として検証し`merged -> completed`。

`premerge-lease`と`cutover-predeploy`の正常な期限切れだけは、未使用・未merge/未deployをfresh attestationで検証して現generationを`expired` completionで閉じ、次generationへ進める。actual/schema failureやack喪失ではrolloverしない。aggregateは`aggregate-body.json`、R7 completed event+aggregate completion、`final-aggregate.json`の二相で自己hash循環を避ける。

全canonical artifactはsingle-write、ack喪失はcanonical stage selectionの同一tokenでverify-existing。失敗はappend-only failed receiptを残し、approval、archive、merge履歴、過去attemptを変更しない。failed selectionから別tokenを自動発行しない。state/event/current/checkpointはcanonical lifecycle storeへstage receiptとともにpromoteする。

## 11. 完了判定

次をすべて満たした場合だけ完遂。

- 全RF itemがexact 1 commit、subject/order一致。
- required command失敗0、unexpected skip/xfail/xpass 0、argv assertion 0件なし。
- public API/schema/status/DB metadataの予定外差0。
- golden transcript text/speaker/timestamp/count/order差0。
- Dashboard lint error/warning 0。
- production import cycle 0、structure budget pass。
- GitNexus計画外HIGH/CRITICAL 0。
- 18 final screenshotがsource SHA一致、未承認差0。
- tribunal/post/QA/outcome/人間approval/PR-ready pass。
- 7 immutable premerge archive、7 merge permit/attestation/record、6 operator gate、5 cutover completion、R7 final aggregator pass。
- 7 release lifecycleがapproved→merged→completedまでhash-boundで、worktree消失後のverify/resume testもpass。

operator evidence、外部review login、human approval、branch protection、fresh attestation等のrepo外前提が欠ける場合はlocal成果を保全して`blocked`と報告し、完遂と主張しない。

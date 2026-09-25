# 実行コンテキスト（圧縮版）

このファイルは、長い調査会話を実装開始前に圧縮した再開点。事実の詳細、入力fixture、全test名、コマンド、戻し方は `plan.md` と `verification-contract.md` を正本とする。会話履歴から推測で条件を足さない。

## 目的と完了条件

初期表示、終了済み会議一覧、録音再生の遅延・読み込み失敗を、R00〜R11の順で修正する。各項目は一つの採用commitとし、指定test・適用する全体suite・`.hw/verify.sh`をclean commitで通す。R11でFable READYとPR ready gateを通す。push、PR作成、merge、deploy、本番計測は範囲外。

## 現在地

- 調査HEAD: `67ea03210c2de4c8723780402d302948b138d939`
- GitNexus索引: 同HEAD、1,154 covered files一致
- 元workspaceの既存dirty: `AGENTS.md`、`CLAUDE.md`。触らない
- 計画成果物: `.hw/plans/app-loading-recording-refactor-20260905/`
- 実装・アプリtest・commit: 未着手
- 実行規模: L、runtimeはprime
- Fable認証: 制限外環境で `loggedIn: true` を確認
- Prime CLI: `/Users/bonginkan-3-gouki/.local/bin/prime-agent`

## 変更順

1. R00: 独立worktree、Fable再採用、正常契約の特性test、基準commit
2. R01: 一覧mountの重複fetchを一つにする
3. R02: `/bots`失敗を`/bots/status`の空成功で隠さない
4. R03: Gatewayの録音バイナリ3routeだけstreaming化し、Range/closeを固定
5. R04: masterメタ情報のDB再検索と同期storage SDK blockを除く
6. R05: detail/transcript/chat/recordingsをownerと世代で隔離
7. R06: production pollingをsingle-flightへ統合
8. R07: 再生descriptor、有限URL retry、audio/video error分離
9. R08: HTML audio elementの自動load retryを3回に制限
10. R09: Gateway `/auth/me`だけ認証基盤障害と無効tokenを分離
11. R10: BFF/browser認証にdeadlineと正しい終端状態を置く
12. R11: 最終全検証、Fable review、verdictだけを独立commit

## 壊してはいけない契約

- 会議completedと録音master完成は別。master 404は未準備として扱う
- 一覧は50件、limit+1、API page基準offset、ID重複除去、created_at降順
- 一覧は要約data、detailは完全data。wire/UIのID名差はadapter
- 再生はcanonical masterのみ。任意chunk結合や時系列gap推測をしない
- raw/MP3の200/206/416、Range関連header、8MiB窓、MP3 180秒を維持
- owner/scope/API key/Cookie境界を弱めず、secretや録音内容を証拠へ出さない
- legacy pollerとその「重なりを保つ」testは残し、production callerだけ移行
- persisted token/isAuthenticatedを信用しない

## 確認済みの主要因

- MeetingsPageの二つのeffectがmountで同じ一覧を要求
- BFFが`/bots`障害時に稼働中集合を200として返し履歴を欠落させる
- Gateway共通forwarderが`resp.content`で録音全量をbuffer
- master handlerが所有者付き録音検索を重複し、storage SDKをevent loopで同期実行
- 詳細の遅着成功/失敗、transcript、chat、録音更新の所有者guardが不十分
- stoppingで複数poller/bootstrapが重なり、共通pollerのrejectが未処理
- playback hookが配列identityで再取得し、audio/videoが同じerrorを上書き
- AudioPlayerの1500ms retryに上限なし
- Gateway/BFF/storeが認証基盤障害をinvalid tokenへ変換し、複数fetchにdeadlineなし

## リスクと停止条件

`forward_request`はGitNexus CRITICAL・直接caller 61。streamingは録音GET 3routeだけopt-inする。`_resolve_token`は73 impacted・直接6なので、strict動作は`auth_me`だけ。GitNexus UNKNOWNを未使用判定に使わない。

計画とコードの不一致、既存baseline failure、Fable再採用不能、Prime不能、partial/truncated graph、指定testの0件収集、契約を弱めないと通らない状況では次項へ進まず報告する。元workspaceのdirtyをstash/reset/commitしない。

## 検証台帳

各項目で、commit SHA、GitNexus impact/detect結果、実行command、test収集数、exit code、外部証拠pathを記録する。実測していない高速化率は報告しない。

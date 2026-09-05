# 検証契約 — app-loading-recording-refactor-20260905

作成: Codex（ユーザー明示承認）。状態: 計画のみ。基準コード: `67ea03210c2de4c8723780402d302948b138d939`。
本書はplan.mdのHow/完了条件を短く固定したもの。入力fixture・test名・commandの完全仕様はplan.mdのR00–R11を適用する。本書だけを理由にtestを省略しない。実装前のFable再採用と実装後のFableレビューはplan.mdの手順どおり。

## C00 — 安全網と変更境界

- R00で独立worktreeを作成。元workspaceのAGENTS.md/CLAUDE.mdの未commit変更は触らない。
- R00で正常契約の特性test 8ケース以上を追加し、sourceを変えず基準commitを作る。5 Dashboard / 1 Gateway / 2 Meeting API。plan.mdに記載した全test名が収集されること。
- D（Dashboard test/typecheck/build/lint ratchet）・G（Gateway全suite）・M（Meeting API非live suite）・H（.hw/verify.sh）をclean commitで通す。環境不足と既存失敗は中断して報告。
- R01開始時のFable再確認でbase-commitをR00へ移す。大きいHTML/索引/原稿はR00に含め、実装レビューの差分へ混ぜない。
- R00–R11は直列・一項目一つの採用commit。テスト削除、skip追加、assertion緩和、lint/verify baseline追加、依存更新、本番操作は禁止。

## C01 — 一覧初回要求

- R01: productionの通常mountでforeground一覧要求1回。status/platform変更と300ms検索debounceの引数は従来どおり。unmount後の予約検索は0回。
- 50件単位offset、redacted表示除外、ID重複除去、並び順、has_moreを維持。
- 対象: test_meetings_initial_load.test.tsx、R00特性、既存store race。D/H。

## C02 — 履歴障害の可視化

- R02: /meetings→/botsを1回だけ要求し、/bots/status fallbackは0回。completedを含む成功bodyは維持。
- upstream401/402/403/429/5xxを200へ変換しない。malformed200は502。headers/body両方の待ちを5000ms以内で504に終端。接続例外502。
- Cache-Control=no-store、429 Retry-After保持。Cookieを削除しない。storeは旧一覧を残してerror/402を既存経路で表示する。
- 対象: test_meetings_proxy_contract.test.ts、既存sensitive proxy/master proxy。D/H。

## C03 — 音声byte転送

- R03: Gatewayのraw / media-mp3 / master-mp3のGET3routeだけstream_response=True。その他61callerを一律変更しない。
- tail未解放でもfirst chunkをASGI sendで観測。downstream停止中にtailをprefetchしない。.content/aread/全chunk蓄積なし。
- 正常・read例外・最初のchunk前後の切断・send失敗・cancelでupstream資源を回収。共有clientを閉じない。
- status200/206/404/416、Range関連header、Content-Type/Disposition/Encodingとraw bytesを保持。hop-by-hopは除去。raw30秒/MP3 180秒の既存socket timeoutを維持。
- missing token401・scope不足403・spoof header除去・owner404を維持。拒否時のupstream stream要求0回。
- BFF直接3routeのJSON416もContent-Rangeを保持。master?proxy=1の既存416と認証エラー分類を維持。
- 対象: test_media_streaming.py、test_recording_routes.py、test_header_injection.py、R00 gateway特性、test_recording_master_proxy_route.test.ts。D/G/H。

## C04 — masterメタ情報

- R04: masterの所有者付きDB検索1回。downloadも従来の所有者条件を維持。route同士の直接呼出しをhelperへ置換。
- 同期storage client取得/exists/presignはto_thread内、DB/ORMはthreadへ渡さない。SDKを止めてもevent loop heartbeatが進む。
- local/MinIO/GCS、presign空のraw fallback、metadata shape、master選択、404、TTL3600を維持。
- 対象: test_recording_metadata_resolution.py、既存recordings/download fallback/GCS。M/HとR03 Gateway回帰。

## C05 — 詳細の所有者と応答世代

- R05: detail/transcript/chatの成功・失敗・finallyをowner+epoch+channel世代で制御。clear後や他会議へ切替後の応答はstate/managerを変えない。
- detail/refresh/transcriptが共用する録音世代により古い録音を戻さない。最新の空配列は適用する。
- 既存currentMeetingだけをseedしたrefreshは成功し、clear後は復活しない。一覧generation/offset、WS、文字起こしの正規化を維持。
- 対象: test_meeting_detail_request_scope.test.ts、既存refresh signature/store race/single flight、R00特性。D/H。

## C06 — pollingの所有者

- R06: 同会議の最大同時poll batch=1。status/artifact併存時はartifact優先。初回即時・2500/5000msを維持。
- batchは3Promiseすべてのsettlementを待つ。同IDのmode切替でも旧taskが終わるまで追加0。
- stoppingのbootstrap/chat重複0。通常active bootstrapは各1回、WS購読維持。
- 旧startImmediateIntervalPollingと既存の重複特性testを変更しない。本番callerのみ移行。共通pollerのreject/同期throwはunhandledにならず次tickへ進める。
- 対象: test_meeting_polling_ownership.test.tsx、既存meeting polling/single flight/transcript reprocess。D/H。

## C07 — URL解決と音声/映像

- R07: 同値descriptorの10回rerenderで再取得0。masterの実体/長さ変更は再取得。meeting切替後に旧結果・旧seekを反映しない。
- audio/video errorは独立。video失敗でaudio Playerを隠さず、artifact停止判定もaudio側だけ。
- batch deadline10秒。通信/502/503/504/timeoutおよび全件404確認の再試行は1500/3000/6000ms、初回込み4attemptまで。401/403/その他4xx/不正200は自動再送しない。
- playback_url無しならmaster探索0。video無しならvideo error無し。404でない一件の失敗を黙って捨てて部分playlistを公開しない。
- 音声created_at順、映像既存順、404除外、同一origin src、duration/fragment/virtual seekを維持。手動再試行で回復可能。cleanupでabort/timer回収。
- 対象: test_playback_resolution.test.tsx、既存master API/proxy、R00特性、R06 hook。D/H。

## C08 — media要素の有限retry

- R08: HTML audio errorの自動loadは3回×1500msまで。連続error10回でも予約timer1つ。枠を使い切ったらspinner終了・既存手動retryを表示。
- src変更/canplay/手動retryでbudget reset、unmountでtimer0。旧srcのtimerは新srcをloadしない。
- R07のURL解決retryと相互resetしない。metadata再同期・fragment auto advance/seekを維持。
- 対象: test_audio_retry_lifecycle.test.tsx、R07、R00特性。D/H。

## C09 — 認証基盤の可用性

- R09: /auth/meだけstrict resolverを使用。Admin401のみ無効token、内部secret403/429/5xx/不正200/接続例外は503。
- Redis不良はcache miss、正常Admin結果をcache write失敗で失わせない。strict get/set各1秒、全体8秒。既存validate5秒/cache TTL60を維持。
- default=Falseの他callerとscope/identity注入は従来互換。欠けた/不正なidentityで許可しない。
- 対象: test_auth_availability.py、既存header injection/WS、R03 streaming。G/H。

## C10 — 認証初期化の終端

- R10: BFF GET10秒、browser GET群12秒、shared POST60秒でheaders/body両方を終端。401だけCookie削除/unauthorized。
- outage/不正応答はnetworkの非認証状態へ。AuthProviderはsharedResult.reason=networkならそのawait直後にreturn、redirect/POST自動再試行0。
- 並行checkAuthは1要求。logout/setAuth/shared開始後に旧checkがstateを戻さない。shared POSTも同時実行をまとめ、自動retryなし。
- 正常SSO/OAuth/public/protected/明示logoutを維持。persisted token/isAuthenticatedを信用・保存しない。
- 対象: test_auth_me_availability.test.ts、test_auth_initialization.test.tsx、既存auth redirect/login、R00特性。D/H、R09 Gateway。

## C11 — 完成証拠

- R10のclean commitで最終D/G/M/Hと全追加test、base=R00の完全なGitNexus compareを実施。partial/truncated/zero testsは不合格。
- Fable契約レビューのviolationsゼロ・READY。利用不可/契約違反なら停止し報告。実行役の自己合格は禁止。
- R11はreview-verdict.jsonだけをcommit。reviewed_commit=R10を祖先として残す。R10をamendしない。
- check_review_verdict.pyとpr-ready-gate.shがexit0、clean。12項目12採用commitと外部証拠を報告。PR/push/merge/deployは含めない。

## 報告項目

各項目ID・commit SHA・対象test名と収集数・実行command/exit code・外部証拠pathを記録。失敗時は期待/実際・再現入力・直前の戻せるcommitを報告し、次項を始めない。本番高速化率は未計測なら記載しない。

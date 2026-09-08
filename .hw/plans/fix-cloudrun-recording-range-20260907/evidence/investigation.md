# 原因調査（2026-09-07）

本番リビジョン: kabosu-dashboard-00069-7q8。ビルドcommit: d7a4d729e95981f59c3653351e41089d0d523184。
実録音ID: 841188337344。メディアID: 800059320721。実サイズ: 52,676,203 bytes。
公開dashboard /api/vexa/recordings/841188337344/master?type=audio&proxy=1:
- Range bytes=0-65535 → HTTP 206、Content-Length 65536、Content-Range bytes 0-65535/52676203。
- Range bytes=0- → HTTP 500（5.7秒）。
- master JSON情報取得 → HTTP 200。
- 先頭64KiBはbackend直結と公開dashboardでSHA256一致: 8f91839b896b006dc74aa402c095574ad50fad16573a5369a6384ded412ba803。
- Cloud Run systemログ 2026-09-07T13:29:57.857455Z: `Response size was too large. Please consider reducing response size.`
- 対応requestログ 2026-09-07T13:29:54.924122Z: HTTP 500。
- 両ログtrace: c1f312cd407a0f583070c4ccb6ff0a2a。
- https://docs.cloud.google.com/run/quotas （同日確認）: 非chunked HTTP/1 response上限32MiB。
- GCS署名認証警告は存在するがraw fallback経路から取得できており本症状の直接原因ではない。

事前impactは67ea032の索引。対象routeと既存proxyテストについてd7a4d729との差分なし。新worktree索引と変更解析を追加実施する。

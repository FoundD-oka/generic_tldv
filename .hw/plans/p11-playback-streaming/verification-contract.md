# Verification Contract — p11-playback-streaming

対象: `b547429..HEAD` の差分。テストは commit 済み clean tree に対して実行する。
meeting-api: python3.11 fresh venv。dashboard: `npm install --no-audit --no-fund && npm test`。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | /raw の Range 要求 → 206 + 正しい Content-Range / Content-Length / Accept-Ranges。ストレージからは要求範囲のバイトのみ読む(download_file 不使用) | unit(test_recordings.py) | pytest ログ |
| AT-002 | /raw の全量要求(Range なし)→ 200 + Content-Length=total。download_file を呼ばず、windowed download_file_range のみで body が元バイト列と一致 | unit | pytest ログ |
| AT-003 | window サイズより大きいコンテンツが複数 window に分割され、連結結果が元バイト列と一致する(境界 off-by-one なし) | unit(window を小さく設定して 2+ 分割を強制) | pytest ログ |
| AT-004 | メディア bytes のストレージ読みはすべて asyncio.to_thread 経由で、メディア応答経路に全量メモリ読み(download_file / 一括 download_file_range)が残存しない | unit + `grep -n "download_file(" services/meeting-api/meeting_api/recordings.py` でメディア応答経路の呼び出し 0 件(構造レビュー) | grep 出力 + レビュー verdict |
| AT-005 | dashboard メディア中継 fetch に AbortSignal が渡り、ヘッダ未受信のまま MEDIA_PROXY_HEADERS_TIMEOUT_MS 経過で 504 を返す | unit(test_recording_master_proxy_route.test.ts、fake timers) | vitest ログ |
| AT-006 | ヘッダ受信後はタイマー経過でも signal が abort されない(長尺 body ストリーミングを切らない) | unit(fetch 解決後にタイマーを進め signal.aborted === false を検証) | vitest ログ |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | meeting-api テスト: ベースライン 591 passed / 11 skipped / 0 failed に対し新規 fail 0(test_recordings.py:173 の全量 download_file lock は AT-002 の新セマンティクスへの更新を許容。それ以外の既存テストは green 維持または同等更新) | pytest 全件実行しベースライン比較 | pytest サマリ全文 |
| FP-002 | dashboard テスト: ベースライン 32 files / 240 tests 全 pass に対し新規 fail 0(追加テストは増加のみ) | `npm test` | vitest サマリ全文 |
| FP-003 | dashboard lint: ベースライン 61 errors / 87 warnings に対し新規 error / warning 0 件(exit 0 は要求しない) | `npm run lint` 出力をベースラインと比較 | lint 出力全文 |
| FP-004 | mp3 経路(/master/mp3, /media/{id}/mp3)の 206/200・Content-Range 挙動が維持される | 既存 mp3 テスト(test_recordings.py:212, :251)green | pytest ログ |
| FP-005 | 存在しないオブジェクトへの /raw が 200 を返さない(Local backend: FileNotFoundError→404 維持) | unit | pytest ログ |
| FP-006 | route.ts の URL 選択順(:288-291)・unsafe-host 判定・passthrough ヘッダ集合が無変更(既存2テスト green) | vitest | vitest ログ |
| FP-007 | 416(不正 Range)の挙動が維持される(_parse_range_header は無変更) | `git diff` で _parse_range_header 無変更 + 既存テスト green | diff 出力 |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | メディア応答のピークメモリが window サイズ(既定 8MiB)+定数に有界(全量を bytes に保持する経路が存在しない) | AT-002/AT-004 + コード構造レビュー | レビュー verdict |
| NFT-002 | window サイズは env `RECORDING_STREAM_WINDOW_BYTES` で調整可能、不正値は既定 8388608 へフォールバック | unit | pytest ログ |

## KPI Checks

なし。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p11-playback-streaming/` へ)
- hash-bound approval required: yes(M のため Fable 契約レビュー必須)
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

なし(依存するのはリポジトリ内 storage 抽象と Node fetch の AbortSignal 挙動のみ。
後者は既存コードの metadata fetch タイムアウト実装(route.ts:228-231)と同型で裏取り済み)。

## 契約外 advisory(修正義務なし・記録のみ)

- 署名URL直配信の有効化(route.ts:288-291 の優先順位反転 + GCS signBlob IAM 確認)は
  別タスク。ストリーミング化後も二段プロキシのホップ自体は残る。
- ヘッダ受信後の body 停止型ハングは undici bodyTimeout(既定300s)頼み。
  実害が観測されたら idle-timeout 型の対策を別途。

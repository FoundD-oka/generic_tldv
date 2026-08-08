---
generated_by: fable
task_id: p11-playback-streaming
base-commit: b547429380effc854b6b271e087364a41d771b54
size: M
---

# ST-14/ST-15: /raw の windowed streaming 化とダッシュボードメディアプロキシのタイムアウト付与

## ゴール

依頼の文字通りの内容:
- ST-14: 「再生が常に meeting-api のメモリ経由プロキシ。Range なし/bytes=0- で全量同期読み
  → イベントループ停止。/raw のストリーミング化**または**署名URL直配信の有効化」
- ST-15: 「ダッシュボードプロキシのメディア fetch に中断シグナルなし → 上流ハングで永久滞留。
  タイムアウト付与」

reframe(ST-14 の「または」を確定): 本タスクは **/raw の windowed streaming 化**を採用する。
署名URL直配信は配備依存(GCS signBlob IAM の付与状況が未確認・MinIO 内部ホスト名は
既存コードが proxy-unsafe 判定で raw にフォールバックする設計)でローカル検証不能のため
今回は採用せず、契約外 advisory として残す。ストリーミング化は全ストレージバックエンド・
全配備形態で有効で、既存の `download_file_range`(3実装とも end-inclusive で実装・テスト済み)
だけで実現できる。

達成すべき成果:
- メディア応答(/raw および同関数を使う mp3 経路)がイベントループをブロックせず、
  ピークメモリが window サイズ(既定 8MiB)に有界。
- ダッシュボードのメディア中継 fetch がヘッダ未受信ハングで永久滞留しない。
  ただし**長尺再生の body ストリーミングは時限で切らない**。

## 現状分析(b547429 で現物確認済み)

- `meeting_api/recordings.py:264-276` `_build_storage_media_response`(同期関数):
  - Range あり: `storage.download_file_range(path, start, end)` を**全量メモリに同期読み**
    (:270)。`bytes=0-` はファイル全量。
  - Range なし: `storage.download_file(path)` 全量(:275)。
  - いずれも sync boto3/gcs 呼び出しを async ハンドラから直接実行 → **イベントループ停止**。
  - 呼び出し元: /raw エンドポイント(:860)、mp3 エンドポイント(:900-906)。
- `_parse_range_header`(:162-196)は正しく実装済み(inclusive、416 処理あり)。変更不要。
- storage 抽象(storage.py): `download_file_range` は MinIO(:209-216)・GCS(:369-380、
  end-inclusive を明記しテストで固定)・Local(:508-513)の3実装済み。`get_file_size` も
  3実装済み(GCS はメタデータのみ取得 :360-367)。**新しい抽象メソッド追加は不要**。
- dashboard `src/app/api/vexa/[...path]/route.ts`:
  - proxy 本体は `mediaResponse.body` の passthrough でストリーミング済(:350-353, :368-371)。
    メモリ問題は meeting-api 側のみ。
  - メディア中継 fetch(:329-332)に **signal なし**(ST-15)。metadata fetch には
    30s/180s タイムアウトあり(:228-231)だが media fetch は対象外。
  - AbortError → 504 の catch は実装済み(:401-402)。
  - :288-291 で raw_url 優先のため署名URLは実質デッドコード → 本タスクでは**変更しない**
    (advisory: 署名URL直配信の有効化は signBlob IAM 確認を含む別タスク)。

## 仮説と確信度

1. 「download_file_range は3バックエンドとも end-inclusive」— 確信度: 高
   (storage.py:370-375 の契約コメント + test_gcs_storage.py でバイト厳密に固定済み)。
2. 「FastAPI StreamingResponse + asyncio.to_thread の windowed 読みでイベントループ
   非ブロック・メモリ有界になる」— 確信度: 高(リポジトリ内で to_thread パターンは
   finalize・mp3 変換で実績あり)。覆る条件: なし(window 毎の range GET による
   レイテンシ増はあるが 8MiB 窓なら無視できる)。
3. 「ST-15 はヘッダ受信タイムアウトで十分」— 確信度: 中。監査の指摘は
   『上流ハングで永久滞留』=応答が返らないケース。ヘッダ受信後の body 停止は
   undici の bodyTimeout(既定300s)が下段ガード。覆る条件: body 停止型ハングが
   支配的な場合 → その際は idle-timeout 型の TransformStream を別タスクで検討。
4. 「全量応答(Range なし)のストリーミング化で既存クライアントが壊れない」—
   確信度: 高。Content-Length を明示するため応答セマンティクスは同一。
   覆る条件: Content-Length 欠落時の挙動差 → 契約 AT-002 で Content-Length を固定。

## How

### 1. meeting-api: `_build_storage_media_response` の streaming 化(recordings.py)

関数を async 化し `fastapi.responses.StreamingResponse` を返す:

- window サイズ: env `RECORDING_STREAM_WINDOW_BYTES`、既定 8388608(8MiB)。
  parse 失敗・0以下は既定値へフォールバック。
- `total = await asyncio.to_thread(storage.get_file_size, storage_path)`
  (Range 有無に関わらず取得。FileNotFoundError は現行どおり呼び出し元の
  except で 404 にマップされる)。
- Range あり: 既存 `_parse_range_header(range_header, total)` で (start, end) を得て
  status 206、ヘッダ `Content-Range: bytes {start}-{end}/{total}`、
  `Content-Length: end-start+1`、`Accept-Ranges: bytes`、既存の Content-Disposition。
- Range なし: status 200、`Content-Length: total`。total==0 は空 body の 200。
- body は async generator:
  `pos = start` から `while pos <= end:` で
  `chunk = await asyncio.to_thread(storage.download_file_range, path, pos, min(pos+W-1, end))`
  を yield。**download_file はメディア応答経路で使用しない**。
- ストリーム開始後(1st chunk 送出後)の storage 例外は log して停止(HTTP 的には切断)。
  開始前の例外は現行の except マッピング(FileNotFoundError→404 / その他→500)を維持。
- 呼び出し元 2箇所を `await` に変更: /raw(:860)、mp3(:900-906)。
  `_ensure_mp3_media_file` の to_thread 実行(:894)は不変。

### 2. dashboard: メディア中継 fetch へのヘッダ受信タイムアウト(route.ts)

:329-332 の `fetch(mediaUrl, ...)` に対し:

- env `MEDIA_PROXY_HEADERS_TIMEOUT_MS`(既定 30000。既存 VOICEPRINT 定数群
  :8-30 と同じ parse ガードパターン)を追加。
- `const mediaController = new AbortController();` +
  `const mediaTimeoutId = setTimeout(() => mediaController.abort(), MEDIA_PROXY_HEADERS_TIMEOUT_MS);`
- `fetch(mediaUrl, { headers: mediaHeaders, cache: "no-store", signal: mediaController.signal })`
  の `await` 解決(=ヘッダ受信)直後に `clearTimeout(mediaTimeoutId)`。
  **body ストリーミングに時限を掛けない**(長尺再生を途中切断しないため)。
- abort 時は既存 catch の AbortError → 504(:401-402)に乗る(新規分岐不要)。
- :288-291 の URL 優先順位・:356-372 の passthrough は変更しない。

### 3. テスト

meeting-api(`tests/test_recordings.py` ほか):
- :135 `test_range_request_reads_only_requested_storage_range`: 要求 range(1-2)が
  window 未満のため `download_file_range(path, 1, 2)` 1回のままで成立する見込み。
  必要なら同等セマンティクス(206 + Content-Range + 要求範囲のみ読む)で更新。
- :173 `test_full_request_keeps_legacy_full_download`: 「全量= download_file」を
  lock している。**「全量要求も windowed range 読みでストリームし download_file を
  呼ばない」に更新**(download_file.assert_not_called + body バイト一致 + Content-Length)。
- 新規: window より大きいコンテンツ(例: W を小さく注入 or env で設定)で
  複数 window に分割され、連結結果が元バイト列と一致(境界 off-by-one なし)すること。
- mp3 range テスト(:212, :251)と voiceprint 系
  (test_voiceprint_master_download_hardening.py 等)は green を維持
  (mock ストレージに get_file_size が必要になる場合は同等更新)。
- 環境: python3.11 fresh venv。ベースライン 591 passed / 11 skipped / 0 failed。

dashboard(`tests/test_recording_master_proxy_route.test.ts` に追加):
- media fetch(fetchMock 2回目呼び出し)に AbortSignal が渡ること。
- media fetch が解決しない場合(pending promise)、fake timers で
  MEDIA_PROXY_HEADERS_TIMEOUT_MS 経過後に 504 が返ること。
- ヘッダ受信後にタイマーを経過させても signal が aborted にならないこと
  (clearTimeout の検証 = 長尺 body を切らない保証)。
- 既存2テスト(raw_url 経由ストリーム / unsafe URL 拒否)は green 維持。
- ベースライン: 32 files / 240 tests 全pass。lint はベースライン(61 errors /
  87 warnings)比較で新規 0 件。

## Why(実装者に渡さない)

- 新 storage 抽象メソッド(open_stream 等)を追加せず download_file_range の
  windowed ループにした理由: 3バックエンド×新メソッドの実装・テストを避け、
  既にバイト厳密テストで固定済みの range 実装だけに依存するため。ネットワーク
  ラウンドトリップは window 毎に増えるが 8MiB 窓では再生体験に影響しない。
- 署名URL直配信を見送った理由: GCS signBlob IAM の付与状況が本番でしか確認できず、
  MinIO(ローカル)は内部ホスト名で proxy-unsafe → 結局 raw フォールバックに戻る。
  ストリーミング化はどの配備でも独立に効く。直配信は将来 signBlob 確認込みの
  別タスク(dashboard は既に unsafe-host フォールバック機構を持つため小改修で済む)。
- ST-15 を「ヘッダ受信タイムアウト」に限定した理由: AbortSignal.timeout の全体時限は
  body ストリーミング(数十分の再生)を途中切断する。監査の実害は「応答が返らない
  ハングによるハンドラ滞留」であり、ヘッダタイムアウトで解消する。

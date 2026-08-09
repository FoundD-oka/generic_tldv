---
generated_by: fable
task_id: p12-audio-disk-streaming
base-commit: 5561cb9ec3c9f994fb5f74a3802bcacec8d08665
size: M
---

# ST-9 + ST-10: final transcription 音声経路のディスクストリーミング化と ffmpeg タイムアウトの音声量比例化(P1-2 第2弾)

## ゴール

依頼の文字通りの内容:
- ST-9: 音声全体をメモリ展開(WAV ~115MB/時 × コピー複数)+ mem 1GiB 制限 →
  2〜3時間会議で OOM、同居コレクタ道連れ。ディスクベース化する。
- ST-10: ffmpeg タイムアウトが既定120秒固定で音声長に比例しない →
  長時間会議はタイムアウト → 500 → retryable → 24回リトライ全滅で永久失敗。

reframe: 不要(監査どおり。コード裏取りでメモリ積み上げ箇所を特定済み、下記)。
達成すべき成果を明確化すると:
- **final transcription 実行中の meeting-api プロセスのピークメモリが音声長に
  比例しない**(定数オーダー + セグメント JSON 分のみ)。mixed 経路・lane 経路の両方。
- ffmpeg タイムアウトが入力ファイルサイズに比例して伸び、下限・上限でクランプされる。
- 処理セマンティクス(状態機械・エラー分類・retryable 判定・lane の all-or-nothing と
  duration budget・skip_no_speaker_events ガード)は一切変えない。
- deploy のメモリ上限(compose `mem_limit: 1g`、helm 1Gi)は**変更しない**。

## 現状分析(main 5561cb9 で現物確認済み。PR #54 は final_transcription.py に触れない
ことを前タスク契約 FP-003 で保証済みのため、マージ後も以下の行番号は有効)

`services/meeting-api/meeting_api/final_transcription.py` のメモリ積み上げ箇所:

### mixed-master 経路(run_deferred_transcription :1334-1371)
1. `_download_recording_audio`(:816-818)= `storage.download_file` で**ソース全量を
   bytes 化**(webm/opus 3h ≈ 30〜90MB)。
2. `_convert_audio_to_wav`(:821-851)= bytes を temp file に書き、ffmpeg は
   file→file 変換なのに、**結果 WAV を `converted.read()` で全量読み戻す**
   (:841-842、16kHz mono 16bit = ~115MB/時、3h ≈ 345MB)。to_thread 中は
   入力 bytes と出力 bytes が同時に在留。
3. `_audio_duration_seconds`(:977-987)= `io.BytesIO(audio_data)` が **WAV 全量を
   もう1コピー**(BytesIO は初期化時にバッファを複製する)。
4. `_call_transcription_service`(:872-931)= bytes を httpx `files=` に渡し、
   STT 応答まで最大 1680 秒(`DEFERRED_TRANSCRIPTION_TIMEOUT_SECONDS`)**WAV 全量を
   保持**し、multipart 送出時にチャンクコピーが乗る。
   → 合計ピーク ≈ WAV×2〜3 + ソース。3h 会議でアプリ常駐分と合わせ 1GiB を超え OOM。

### lane 経路(_transcribe_lanes :558-711)
- `_prepare`(:583-596)は semaphore(2) で並行ダウンロード+変換するが、
  **`prepared` リスト(:599-631)が全 lane の WAV bytes を同時保持**したまま
  全 lane のダウンロード完了を待つ(transcribe タスク生成 :666-670 まで解放されない。
  BUG-010 の改善は STT 開始後の話で、prepare 完了〜STT 開始の間は全量在留)。
  duration budget 上限 `MAX_LANE_TOTAL_DURATION_SECONDS`=4h → WAV ~460MB + コピー。

### ffmpeg タイムアウト(ST-10)
- `_convert_audio_to_wav` :836 で `DEFERRED_TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS`
  (既定 "120")を固定値として `subprocess.run(timeout=...)` に渡す。音声量非比例。
  タイムアウト → HTTPException 500(:843-844)→ `_is_retryable_http_error` で
  retryable → sweep が `FINAL_TRANSCRIPTION_MAX_ATTEMPTS`=24 まで再試行 → 毎回
  同じ箇所で失敗し永久失敗(監査どおり)。

### ディスク化の下地(確認済み)
- `storage.py`: `download_file_to_path` / `upload_file_path` が基底クラス(:40-55)に
  あり、MinIO(:182,218)・GCS(:346,382)は streaming 実装で override 済み。
  LocalStorageClient(:467-)は基底の read-all フォールバック(ローカル開発専用で
  ファイルは既にローカルディスクにあるため実害なし。ただし検証テストで Local を
  使う場合はこのフォールバックがピークに乗る点に注意 — How §5 で対処)。
- transcription-service 受け口(`services/transcription-service/main.py:273-`)は
  FastAPI `UploadFile`。Starlette は multipart を SpooledTemporaryFile へ
  ストリーミング受信する(Content-Length 必須でなく chunked 送信可)ため、
  **meeting-api 側がファイルオブジェクトを httpx に渡すストリーミング送信と互換**。
  (Soniox 分岐 :328 の `await file.read()` は transcription-service 側の全量読みだが
  別サービス・別メモリ空間でスコープ外。advisory として記録するに留める。)
- httpx は requirements.txt で `>=0.28.1`。`files=` にファイルオブジェクトを渡すと
  multipart body を 64KiB チャンクで遅延読みし、Content-Length は seek/tell で算出
  (0.18 以降安定した挙動)。

### 既存テストの依存
- `_download_recording_audio` / `_convert_audio_to_wav` / `_call_transcription_service`
  / `_audio_duration_seconds` は**モジュール外から import されていない**(sweeps.py :709 は
  定数と run_deferred_transcription 等のみ、voiceprint_matching.py の言及はコメント)。
- テスト5ファイル(test_final_transcription_lanes / test_final_transcription_gemini_dictionary
  / test_voiceprint_hook_integration / test_speaker_clusters / test_final_transcription)が
  上記4関数を**名前で patch** している。関数名を維持すれば mock 境界は生きたまま:
  mock は任意引数を受けるので signature 変更で壊れず、mock 戻り値 `b"wav"` が
  path として下流に流れても、下流も全て mock 済みか `_audio_duration_seconds` の
  None-on-error フォールバックで吸収される(現状も b"wav" は duration=None になる)。
- 唯一の実体直呼びは `test_final_transcription.py:346-382`
  (`_call_transcription_service(b"wav", "wav", ...)`)。path 渡しに書き換えが必要。

## 仮説と確信度(反証を優先して確認した結果)

1. 「httpx AsyncClient はファイルオブジェクトの multipart を全量メモリ化せず
   チャンク送信する」— 確信度: 高(httpx の FileField 実装は 64KiB チャンク読み。
   0.18 以降の安定挙動、pin は >=0.28.1)。覆る条件: httpx が将来 body を先読み
   集約する変更をした場合。**検証契約 AT-001 の tracemalloc 実測が経験的に固定する**
   ため、覆っていればテストが落ちる(仕様信頼に依存しない検証設計)。
2. 「ffmpeg file→file 変換のメモリは音声長に比例しない」— 確信度: 高
   (ストリーミングトランスコードでコーデック内部バッファのみ)。覆る条件: なし
   (ffmpeg の基本設計)。サブプロセスのため tracemalloc 対象外だが、
   もともと file→file であり今回の変更で悪化しない。
3. 「関数名を維持すれば既存 mock 境界テストは概ね無修正で通る」— 確信度: 中〜高。
   覆る条件: mock 戻り値の bytes を実 path として os 操作する新コードが例外を
   握り潰さない場合 → How §4 の防御的クリーンアップで回避。残余は FP-001 の
   ベースライン比較が検出し、修正は「assertion の意図を変えない最小修正」に限定。
4. 「入力サイズ比例タイムアウト(0.25MiB/s 前提)は実環境で十分保守的」— 確信度: 高。
   ffmpeg の音声デコード+リサンプルは 1 vCPU 級でも実測数 MiB/s 以上
   (実時間比 30〜100x)。0.25MiB/s は10倍以上の安全率。覆る条件: 極端に CPU を
   絞ったコンテナ — その場合も env(下記)で係数を引き上げ可能。

## How

すべて `services/meeting-api/meeting_api/final_transcription.py` と meeting-api の
テストのみ。**他モジュール・deploy・transcription-service は変更禁止**。

### 1. パイプラインを bytes 受け渡しから path 受け渡しへ(関数名は維持)

- `_download_recording_audio(source, dest_path: str) -> str`:
  `asyncio.to_thread(storage.download_file_to_path, source.storage_path, dest_path)`
  に変更し dest_path を返す。bytes 版 `storage.download_file` の呼び出しを廃止。
- `_convert_audio_to_wav(src_path: str, media_format: str) -> tuple[str, str]`:
  入力は既にファイル。変換不要フォーマット(wav 等、現行 :822-824 と同じ判定)は
  `(src_path, media_format)` をそのまま返す。変換時は ffmpeg file→file
  (現行コマンドライン維持)で `(dst_path, "wav")` を返し、**`converted.read()` を
  廃止**。成功時は src_path を削除してディスクピークを半減(削除失敗は無視)。
  タイムアウトは §2 の `_ffmpeg_timeout_seconds(os.path.getsize(src_path))`。
  エラー時の挙動(returncode≠0 → HTTPException 500 "Audio conversion failed"、
  TimeoutExpired → 500 "Audio conversion timed out")は**文言含め現状維持**
  (retryable 分類を変えないため)。
- `_audio_duration_seconds(path: str, media_format: str) -> Optional[float]`:
  `wave.open(path, "rb")` を直接使う(wave はファイル名を受ける)。BytesIO コピー廃止。
  wav 以外は None、例外時 None のセマンティクスは現状維持。
- `_call_transcription_service(audio_path: str, media_format: str, *, language,
  prompt=None)`: `open(audio_path, "rb")` した file object を
  `files={"file": (f"recording.{fmt}", fh, f"audio/{fmt}")}` で渡す
  (with で確実にクローズ)。httpx がチャンク読みでストリーミング送信する。
  エラーマッピング(:907-931)は無変更。

### 2. ST-10: サイズ比例タイムアウト

モジュールレベルに追加:

```python
def _ffmpeg_timeout_seconds(input_size_bytes: int) -> float:
    floor = float(os.getenv("DEFERRED_TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS", "120"))
    cap = float(os.getenv("DEFERRED_TRANSCRIPTION_FFMPEG_TIMEOUT_MAX_SECONDS", "1800"))
    per_mib = float(os.getenv("DEFERRED_TRANSCRIPTION_FFMPEG_SECONDS_PER_MIB", "4.0"))
    proportional = (input_size_bytes / (1024 * 1024)) * per_mib
    return min(max(proportional, floor), max(cap, floor))
```

- 既存 env `DEFERRED_TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS` は**下限**として意味を
  引き継ぐ(既定120で小ファイルの現行挙動を維持しつつ、運用で引き上げていた環境の
  期待「最低このくらい待つ」を後方互換に保つ)。
- 既定係数 4.0 秒/MiB(=0.25MiB/s)の根拠: ffmpeg の opus/webm→wav 16k mono 変換は
  1 vCPU 級で実測数 MiB/s 以上。10倍超の安全率。3h 会議 64kbps ≈ 84MiB → 336秒
  (現行120秒では確実に死ぬケースが救済される)。
- 上限既定 1800 秒: ハングした ffmpeg の占有を run lease(RUN_LEASE_SECONDS=2100、
  heartbeat で延長)内に抑えつつ ~450MiB 入力まで線形域。cap < floor の誤設定は
  floor を優先(fail-safe)。

### 3. mixed 経路の書き換え(:1334-1340 相当)

- `tempfile.TemporaryDirectory(prefix="final-tx-")` を STT ブロックの周りに
  コンテキストとして張る(with)。中で download→convert→duration→STT を path で
  接続。**STT 応答受領後、`_parse_segments` の前に with を抜けてよい**
  (以降は音声不要。tx_result はセグメント JSON で音声長非比例 ~1-2MB/時)。
  例外時も TemporaryDirectory が確実に掃除する。
- `fallback_duration` は wav path から取得(変換なしフォーマットでは従来同様 None)。

### 4. lane 経路の書き換え(:558-711)

- `_transcribe_lanes` 冒頭で lane-set 共通の TemporaryDirectory を1つ作り、
  関数全体を覆う try/finally で削除(prepare タスクの cancel 経路・
  LaneTranscriptionFallback 経路も含めて漏れなし)。
- `_prepare`: lane ごとに一意なファイル名(lane_key ベース)で download→convert し
  `(lane, wav_path, fmt, duration)` を返す。duration は path 版
  `_audio_duration_seconds`(WAV ヘッダ読みのみで安価。budget チェックの
  セマンティクス BUG-010 は不変)。
- `_transcribe`: `_call_transcription_service(wav_path, ...)` に変更。各 lane の
  STT 完了後 finally で自 lane の wav を削除(ディスクも lane 単位で解放。
  mock が bytes を返すテスト経路を考慮し、削除は `try/except OSError: pass` の
  防御的実装にする)。`prepared.clear()` 等の構造は維持。

### 5. 新規テスト `tests/test_final_transcription_memory.py`(本タスクの本質検証)

既存テストの流儀(make_meeting + AsyncMock db、`test_final_transcription_gemini_dictionary.py`
参照)で run_deferred_transcription を丸ごと動かす。音声は `wave` モジュールで生成する
無音 WAV(format="wav" なので ffmpeg 不要 = CI で ffmpeg 非依存)。ストレージは
LocalStorageClient(`STORAGE_BACKEND=local` + `LOCAL_STORAGE_DIR=tmp_path`)。
**注意: Local の `download_file_to_path` は基底クラスの read-all フォールバック**
のため、そのままではピークに全量が乗る。テストでは download 境界の実測目的に応じて:
メモリ計測テストは `download_file_to_path` を `shutil.copyfile` する薄い stub に
monkeypatch する(本番 MinIO/GCS の streaming 実装の代理。加えて AT-004 の静的
ガードで「bytes 全読み API を呼ばない」ことを固定するので、стub は検証を弱めない)。

- httpx は**実物**を使い、`httpx.AsyncClient` を monkeypatch で
  `lambda **kw: httpx.AsyncClient(transport=DrainTransport(), **kw)` に差し替える。
  `DrainTransport(httpx.AsyncBaseTransport)` は `request.stream` を async for で
  チャンク消費して総バイト数を数え、`{"language":"ja","segments":[...]}` を返す。
  → httpx の multipart エンコードが実際にストリーミングであることまで実測に含める。
- (a) mixed 経路: 48MiB と 192MiB の WAV で各1回実行し、`tracemalloc` で
  ピークを計測。**peak(192MiB) ≤ 64MiB かつ peak(192) − peak(48) ≤ 16MiB**
  (非比例性の実測。閾値はチャンクバッファ+固定費に対し十分な余裕)。
  DrainTransport の受信総バイト ≥ WAV サイズも assert(送信が実際に走った証明)。
- (b) lane 経路: 3 lane × 64MiB で peak ≤ 64MiB。
- (c) 静的ガード: `inspect.getsource(final_transcription)` に対し、
  `download_file(` の出現がすべて `download_file_to_path(` であること、
  `BytesIO(` が音声経路(_audio_duration_seconds)に存在しないことを assert。
- (d) 一時ファイル掃除: 成功・STT失敗(DrainTransport が 500 を返す変種)・
  lane fallback の各経路後に temp dir が残存しないこと。
- (e) ST-10: `_ffmpeg_timeout_seconds` の floor/線形/cap/env override の単体
  (パラメトライズ)+ `subprocess.run` を patch して `_convert_audio_to_wav` が
  算出値を timeout kwarg に渡すことの確認。

### 6. 既存テストの修正(最小限)

- `test_final_transcription.py::test_call_transcription_service_marks_deferred_tier`
  (:346-382): `b"wav"` を tmp_path 上の実ファイル path に変更。assertion の意図
  (deferred tier ヘッダ・トークン・timeout)は不変。
- 他の mock 境界テスト5ファイルは**原則無修正**。FP-001 のベースライン比較で
  落ちたものだけ、assertion の意図を変えない最小修正を許す(修正一覧を評価証跡に残す)。

### 7. 変更しないもの

- `sweeps.py` / `meetings.py` / `storage.py` / deploy 一式 / transcription-service 一式。
- 状態機械(`_set_final_transcription_state` のキー・値)・エラー分類・retryable 判定・
  リトライ上限・lease/heartbeat・lane の all-or-nothing と budget・
  skip_no_speaker_events ガード・Gemini 分岐のセマンティクス。
- deploy のメモリ上限(compose :262 `mem_limit: 1g`、helm values.yaml :141-143
  memory 1Gi)。**据え置きが本タスクの回帰カナリア**(上限変更で誤魔化さない)。

## Why(実装者に渡さない)

- 関数名を維持して semantics を path 化する理由: 4関数を patch する既存テストが
  5ファイル・30箇所以上あり、新名関数に切り替えると全 patch の書き換えが必要になる。
  名前維持なら mock 境界がそのまま生き、実測で落ちた分だけ直せばよい。レビュー差分も
  「経路の置換」として読みやすい。
- deploy のメモリ上限を触らない理由: 依頼の明示要求(上限変更は根治でない)に加え、
  1GiB 据え置き自体が「ストリーミング化が効いている」ことの本番での継続的な検証に
  なる。上限を上げると退行が黙って隠れる。
- 既存 env をタイムアウトの「下限」に読み替える理由: 固定値のまま残すと ST-10 が
  直らず、廃止すると運用で引き上げていた環境(実在し得る)の意図が消える。
  下限化は両方を満たす唯一の後方互換解。
- LocalStorageClient に streaming override を足さない理由: Local はファイルが既に
  ローカルディスクにあり本番経路でない。足すと storage.py が差分に入り、
  「final_transcription.py とテストのみ」という影響範囲の明快さが崩れる。
  テスト側 stub + 静的ガードで検証の実質は落ちない(advisory: 将来 storage.py を
  触るタスクで shutil.copyfile override を足すのは望ましい)。
- transcription-service 側の Soniox `file.read()`(main.py:328)と Whisper 経路の
  受信後メモリはスコープ外とする理由: 別サービス・別コンテナで ST-9 の実害
  (meeting-api の OOM・コレクタ道連れ)と独立。混ぜると影響範囲が2サービスに
  広がる。advisory として記録し、必要なら別タスク化。
- httpx のチャンク読みが event loop 上の小さな同期 read になる点: 64KiB のローカル
  ディスク読みで実害なし(現状の bytes 版も同等以上のコピーコストをループ上で
  払っていた)。advisory に記録。

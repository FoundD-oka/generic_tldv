---
generated_by: fable
task_id: p12-gemini-concurrency
base-commit: 0900ad8c5424322369e2676de2148c44c334b49d
size: M
---

# ST-11: Gemini 同時実行の既定値を根拠付きで引き上げ、429 で自滅しない設計にする(P1-2 第3弾)

## ゴール

依頼の文字通りの内容: 「`gemini_adapter.py` のモジュールレベル semaphore の既定が 1 で、
全社の Gemini 文字起こしが同時1本に直列化される。適切な既定値を根拠付きで決めよ。
既定値を上げても 429 で自滅しない設計にすること」。

reframe: 不要(監査は正しく、未解消。コード裏取り済み、下記)。
達成すべき成果を明確化すると:
- 複数会議が同時に終了した際、Gemini deferred 文字起こしが直列待ちで積み上がらない
  (現状: 2件目以降は `_semaphore.acquire()` で最大 OPERATION_TIMEOUT_SECONDS=1500秒
  待ち → admission_timeout 503 → meeting-api リトライ、の繰り返しで遅延が線形に累積)。
- **429(レート制限)を踏んでも会議が手動 reconcile 必須の永久失敗にならない**。
  これは現状コードの実バグでもある(下記「429 の現状」)。concurrency を上げる前提条件。
- 既定値は env(`GEMINI_MAX_CONCURRENCY`)で従来どおり上書き可能なまま。

## 現状分析(現物確認済み。行番号は 2026-08-09 時点の main 系)

- `services/transcription-service/gemini_adapter.py:31`
  `MAX_CONCURRENCY = int(os.getenv("GEMINI_MAX_CONCURRENCY", "1"))`、
  `:49` `_semaphore = asyncio.Semaphore(max(1, MAX_CONCURRENCY))`(モジュールレベル)。
- Gemini 経路は `main.py:304-322` で early return し、`MAX_ACTIVE_REQUESTS=20` の
  ロード管理(semaphore/queue/fail-fast)を**完全にバイパス**する。つまりこの
  `_semaphore` が Gemini の唯一のゲート。監査の「別物」指摘は正しい。
- 1リクエスト内のチャンクは直列処理(`_transcribe_sync` :3744 の for ループ)。
  よって同時 generate_content 数 ≤ concurrency が構造的に保証される。
- 待機側: `_transcribe_with_loader` :3813 で deadline(1500秒)内 acquire 待ち。
  超過で `admission_timeout`(503)→ meeting-api `final_transcription.py:1462-1465` で
  retryable=True + `:1473-1474` で provider_started_at リセット(自動再試行可能)。

### 429 の現状(自滅リスクの所在)

- file API(upload/get/delete)は `_retryable_file_call`(:142-153)が 429/5xx を
  最大3回・指数バックオフ(1,2,4秒)で再試行する。**こちらは対処済み**。
- generate_content は `HttpRetryOptions(attempts=1)`(:3724)で SDK リトライ無効、
  かつ例外ハンドラ(:3509-3520)が 401/403/404 以外の**すべて**(429 を含む)を
  `unknown_manual_reconcile`(422)へ落とす。meeting-api 側ではこの code は
  status="unknown_manual_reconcile" + retryable=False となり、`:1234,:1243` の
  ガードにより**手動 reconcile するまで再実行もブロック**される。
  → concurrency を上げて 429 を1回でも踏むと、その会議は人手介入まで失敗のまま。
  これが「429 で自滅」の実体であり、既定値引き上げの前に塞ぐ必要がある。
- 429 は「プロバイダが処理せず拒否した」ことが確定している応答であり、
  「受理・課金済みかもしれない」という unknown_manual_reconcile の前提
  (:3515 コメント)が当てはまらない。429 に限り再試行は安全。

### 影響範囲の判定(依頼事項)

- コード変更は `services/transcription-service/` に閉じる。meeting-api は無変更
  (429 枯渇を既存の `admission_timeout` code にマップすることで、既存の
  retryable=True 経路をそのまま使う。下記 How §2)。
- **ただし配線の更新は必要**: transcription-service は GPU ホスト上の独立 compose
  (`services/transcription-service/docker-compose.yml` / `docker-compose.cpu.yml`)で
  デプロイされ、そこに `GEMINI_MAX_CONCURRENCY=${GEMINI_MAX_CONCURRENCY:-1}` の
  **`:-1` フォールバックが書かれている**(docker-compose.yml :39,:78 /
  docker-compose.cpu.yml :44 / `.env.example` :65)。コード既定だけ変えても
  compose デプロイでは常に 1 が注入されて潰されるため、これらの `:-1` も更新する。
- `deploy/compose/docker-compose.yml` と `deploy/helm/` は transcription-service を
  含まない(GEMINI_* の配線なし、確認済み)→ 変更不要。

### テストの現状(依頼の注意事項)

- CI 未整備(ST-26/P1-4 の範囲)だが、`services/transcription-service/tests/` に
  pytest ベースの `test_gemini_adapter.py`(monkeypatch + fake genai Client の流儀、
  例: :220 `test_sync_generation_is_called_once_and_file_is_deleted`)が存在する。
  検証はローカル fresh venv での pytest 実測 + ベースライン比較で行う(契約参照)。

## 既定値の決定(リサーチ: 仮説 → 反証確認 → 確信度)

**決定: 既定 3**(env で上書き可能)。

負荷の実測ベース見積り(現物コードから導出):
- 1チャンク = 300秒音声(`GEMINI_CHUNK_DURATION_SECONDS=300`)≈ 入力 ~9.6k トークン
  (音声 ≈32 tok/秒)+ 出力は日本語会話5分で実測数千トークン
  (`gemini_chunk_usage` ログ :3524 で本番実測可能)。
- 1チャンク処理は upload+poll+generate+delete で数十〜150秒 → 1スロットあたり
  generate ≈ 0.5〜2 RPM、file ops 含め ≤ ~8 API calls/分。
- concurrency 3 の最悪: generate ~6 RPM、トークン ~0.5M TPM 未満。

仮説(外部依存): 「Gemini API の paid Tier 1 レート制限(flash 系で数百 RPM /
1M+ TPM、pro 系で 150 RPM / 2M TPM オーダー)に対し、上記負荷は1桁以上の余裕がある」
— 確信度: 中〜高。覆る条件: (a) 無料枠キーでの運用(5〜10 RPM 級)、
(b) gemini-3 系 preview モデル固有の低い上限。**覆っても How §2 の 429 バックオフが
自動退行(実効直列化)させるため自滅しない** — 検証契約はこの機構の方を固定する。
数値そのもの(3)は運用パラメータであり env で即調整可能。

メモリ考慮: Gemini 経路はリクエストごとに音声全量 bytes を保持
(`MAX_AUDIO_BYTES` 既定 400MB 上限)+ チャンクコピー。concurrency 3 の最悪
~1.5GB/worker。GPU ホスト(Whisper large 同居)では許容範囲だが、
4 以上に上げる運用は RAM を確認してからにする(README 追記は任意、advisory)。

## How

変更は `services/transcription-service/` 配下のみ。

### 1. 既定値の変更

- `gemini_adapter.py:31` の既定 `"1"` → `"3"`。`max(1, ...)` ガード(:49)は維持。
- `docker-compose.yml` :39/:78、`docker-compose.cpu.yml` :44 の
  `${GEMINI_MAX_CONCURRENCY:-1}` → `${GEMINI_MAX_CONCURRENCY:-3}`。
- `.env.example` :65 の `GEMINI_MAX_CONCURRENCY=1` → `3`(新設 env も追記)。

### 2. generate_content の 429 バックオフ(自滅防止。本タスクの前提条件)

`_transcribe_chunk_sync` の generate_content 呼び出し(:3493-3520)を再試行ループ化:

- `_status_code(exc) == 429` の場合**のみ**再試行。それ以外の例外分類
  (ValueError→config_invalid、401/403→auth_failed、404→model_not_found、
  その他→unknown_manual_reconcile)は**一切変えない**。
- 再試行パラメータ(モジュールレベル env、既存の命名規約に合わせる):
  `GEMINI_RATE_LIMIT_RETRY_ATTEMPTS`(既定 6)、バックオフは
  `min(2**attempt, 60)` 秒 + full jitter(`random.uniform(0, ...)` 型)。
- sleep の前後で `_ensure_operation_active(stop_event, deadline_monotonic)` を呼び、
  sleep 時間は deadline までの残りでクランプ(:3446-3452 の file poll ループと
  同じパターン)。呼び出し元が消えた run が retry で居座らないため。
- 試行枯渇時は `GeminiError("admission_timeout",
  "Gemini provider rate limit persisted beyond the retry budget", status_code=503)`
  を raise。→ meeting-api は無変更で retryable=True + provider_started_at リセット
  (final_transcription.py:1462-1474 の既存経路)となり、sweep が自動再試行する。
- 各 429 発生時に warning ログ(attempt 番号・待機秒を含む)を出す(運用でレート
  制限逼迫を観測可能にする)。

### 3. 変更しないもの

- `_retryable_file_call` の file API リトライ(現状で十分)。
- `HttpRetryOptions(attempts=1)`(SDK 側リトライは無効のまま。再試行の可否判断を
  自前ハンドラに一元化するため)。
- meeting-api・deploy/compose・deploy/helm・main.py のロード管理・
  チャンク処理/マージのセマンティクス全部。

### 4. テスト(`tests/test_gemini_adapter.py` に追加。既存の fake Client 流儀)

- (a) 既定値: env 未設定でモジュールを importlib.reload した際
  `MAX_CONCURRENCY == 3` かつ semaphore 初期値 3。env override(例 "5")と
  `max(1,...)` ガード("0"/"-2" → 1)。reload 後は必ず元へ復元する
  (autouse fixture か finally。他テストがモジュール状態に依存するため)。
- (b) 同時実行上限の実測: concurrency=2 に reload した状態で fake Client の
  generate_content 内で同時在圏カウンタを取り、3リクエスト並行実行時の
  最大同時在圏 ≤ 2 を assert(asyncio.Event で全員入室を待つ構造)。
- (c) 429 → バックオフ後成功: fake Client が 2 回 429(status_code=429 属性付き
  例外)→ 3 回目成功。`time.sleep` を monkeypatch して待機列を記録し、
  正常結果が返ること・sleep が指数的に伸びること(上限 60 クランプ含む)を assert。
- (d) 429 枯渇: 常に 429 → `GeminiError` の code == "admission_timeout" かつ
  status_code == 503(unknown_manual_reconcile で**ない**こと)。
- (e) 429 再試行中の deadline 超過: deadline_monotonic を過去に設定 → 全試行を
  消費せず即座に中断される(既存の deadline セマンティクス
  "unknown_manual_reconcile / exceeded its deadline" のまま)。
- (f) 回帰: 非 429 の generate 失敗が従来どおり unknown_manual_reconcile、
  401/403 → auth_failed のまま(既存テストがあれば流用、なければ追加)。

## Why(実装者に渡さない)

- 429 枯渇を新 code(例 rate_limited)でなく `admission_timeout` にマップする理由:
  meeting-api の Gemini 分岐は code 文字列 "admission_timeout" のみ retryable=True
  とする(:1462-1465)。新 code を足すと meeting-api の分類変更が必要になり
  影響範囲が2サービスに広がる。意味的にも「プロバイダ容量を確保できないまま
  期限切れ」であり admission の失敗として整合する。トレードオフ: run 途中の 429
  枯渇では処理済みチャンク分の課金が再実行で二重になるが、正しさには影響せず、
  §2 のバックオフ(最大 ~3分)が枯渇自体を稀にする。
- SDK の `HttpRetryOptions` に 429 リトライを任せない理由: SDK リトライは
  stop_event/deadline を知らず、呼び出し元が消えた後も内部で再試行し続ける。
  自前ループなら既存の deadline 機構(:3446-3452 パターン)に載せられる。
- 既定 3 で 4 以上にしない理由: レート制限には余裕があるが、リクエストあたり
  最大 400MB の音声 bytes 常駐(ST-9 と同型の bytes 保持がこのサービスにも
  ある)× concurrency がホスト RAM に直撃する。bytes 経路のディスク化は
  スコープ外(advisory: ST-9 の類推で将来タスク化の価値あり)。
- main.py の Soniox 経路 `file.read()` や Whisper 経路は本タスクと無関係のため
  触らない(前タスク p12-audio-disk-streaming の advisory 継続)。

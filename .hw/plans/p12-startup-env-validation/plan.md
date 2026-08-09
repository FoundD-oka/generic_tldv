---
generated_by: fable
task_id: p12-startup-env-validation
base-commit: 0900ad8c5424322369e2676de2148c44c334b49d
size: M
---

# ST-12: meeting-api 起動時の必須 env 検証(fail-fast)を導入する(P1-2 第4弾)

## ゴール

依頼の文字通りの内容: 「compose の `TRANSCRIPTION_SERVICE_URL=${...:-}`(空既定)でも
meeting-api は起動成功し、初検知が会議終了後の `_call_transcription_service` の 503。
会議1本を失ってから気づく。起動時に必須 env を検証して fail-fast する仕組みを設計せよ。
線引きが要点」。

reframe: 不要(監査は正しく、未解消。コード裏取り済み、下記)。
達成すべき成果を明確化すると:
- **主機能(文字起こし)を必ず壊す設定不備は、会議を1本失う前=プロセス起動時に
  検知され、起動が失敗する**(理由が全件列挙されたログ付き)。
- オプション機能の設定不備は起動を妨げず、warning ログで「どの機能が無効化されるか」
  が観測できる。
- 開発環境向けの明示的な逃げ道(warn モード)があり、既定は strict。

## 現状分析(現物確認済み)

- 検証済みの env: `config.py:6-8` が REDIS_URL 欠落で import 時 ValueError、
  `database.py:21` が DB_HOST/PORT/NAME/USER/PASSWORD 欠落で import 時 raise。
  startup()(main.py:265-408)は DB init と Redis ping(bounded retry → raise)のみ。
- `TRANSCRIPTION_SERVICE_URL` は未検証。空のときの実害:
  - realtime: `meetings.py:1141` で bot_config の transcriptionServiceUrl に None が
    入り、bot は文字起こし不能のまま会議に参加する(realtime も壊れる)。
  - deferred: `final_transcription.py:940` で 503 "TRANSCRIPTION_SERVICE_URL not
    configured" — 会議終了後が初検知。監査の記述どおり。
- `deploy/compose/docker-compose.yml:132`(meeting-api)・`:323`(runtime-api)・
  `:557` が `${TRANSCRIPTION_SERVICE_URL:-}` 空既定。
- 既存方針: main.py:276-283 のコメント「silent-skip anti-pattern の排除 / No
  silent-degraded mode anywhere」。raise → プロセス終了 → restart policy に委ねるのが
  このコードベースの確立された流儀(Pack C.4)。

## 必須/オプションの分類(env 参照箇所を読んで決定。実装はこの表に従う)

### 必須(欠落 = 主機能「文字起こし」が必ず壊れる → 起動失敗)

| env | 根拠 |
|---|---|
| `TRANSCRIPTION_SERVICE_URL`(非空) | realtime(meetings.py:1141 経由で bot へ)と deferred(final_transcription.py:922 フォールバック先)の両方の到達先。空なら文字起こしは全経路で不能 |
| `STORAGE_BACKEND` が既知値(minio/s3/gcs/local) | storage.py:589 は未知値で使用時 raise。録音保存不能 = deferred 文字起こしの素材喪失 |
| `STORAGE_BACKEND=gcs` のとき `GCS_BUCKET`(非空) | storage.py:312-314 は使用時 raise。起動時に前倒しする(録音アップロード失敗 = 会議喪失と同型) |

REDIS_URL・DB_* は既に import 時検証済みのため本タスクの対象外(重複させない)。
MINIO_* は全てにコード既定値があり(storage.py:107-112)compose 開発環境で動くため
必須にしない(既定値が本番で誤っている場合は録音アップロード失敗として別途顕在化
するが、「未設定で静かに壊れる」クラスではない)。

### オプション(欠落 = 機能単位の劣化 → warning ログのみ)

| env | 欠落時に無効化・劣化する機能 |
|---|---|
| `TRANSCRIPTION_SERVICE_TOKEN` 空 | transcription-service 側は API_TOKEN 未設定なら全許可(後方互換)のため動作はし得る。認証なし運用の warning |
| `VOICEPRINT_SERVICE_URL` 空 | 話者アトリビューション(voiceprint 照合)が無効 |
| `KABOSU_DRIVE_EXPORT_ENABLED` が truthy かつ `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `KABOSU_GOOGLE_REFRESH_TOKEN` / `KABOSU_DRIVE_FOLDER_ID` のいずれか空 | Drive 出力が実行時に失敗する。機能有効化と資格情報の不整合を warning |
| `INTERNAL_API_SECRET` 空 | bot コールバック認証が弱化(compose/helm に既定値があるため実際には稀) |

`DEFERRED_TRANSCRIPTION_SERVICE_URL` 空は「realtime と同一エンドポイントを共用」という
仕様どおりの正常系(compose :332 コメント)のため warning 対象にしない。

## fail-fast 方式の選定(依頼事項)

**起動時例外(startup() 冒頭で検証し RuntimeError → プロセス終了)を採用する。**

理由:
- 一貫性: 「No silent-degraded mode anywhere」(main.py:281-283)と config.py /
  database.py の既存 raise 方式に揃う。K8s は CrashLoopBackOff(指数バックオフ内蔵)、
  compose は restart policy のバックオフ(100ms から倍増)があり、再起動ループは
  安全かつ `docker ps` / `kubectl get pods` で即可視。env は静的なので再起動で直る
  ことはないが、「直らないことが騒がしく見える」のが fail-fast の目的そのもの。
- 対案(ヘルスチェック degraded / readyz 503)を退ける理由: compose の meeting-api
  healthcheck(deploy/compose/docker-compose.yml :104-109)はルート `/` を叩くため
  readyz 劣化を検知せず、「起動成功に見えるが機能しない」という現状の問題の再演に
  なる。K8s でも Ready にならないだけでプロセスは走り続け、ログを読むまで原因が
  分からない。起動時例外なら stderr の最終行が原因の全件列挙になる。
- 逃げ道: `STARTUP_ENV_VALIDATION=warn`(既定 "strict")で必須違反も warning に
  降格して起動継続可能。文字起こし基盤を持たない部分開発環境(dashboard 開発等)用。
  値が strict/warn 以外なら strict 扱い(fail-safe)。

## How

変更は `services/meeting-api/`(meeting_api/ 本体 + tests/)のみ。deploy は無変更
(compose の空既定はそのまま。fail-fast がデプロイ側の設定漏れを検知する側なので、
compose に値を強制する変更はしない)。

### 1. 新モジュール `meeting_api/env_validation.py`

- `collect_env_issues() -> tuple[list[str], list[str]]`: `(violations, warnings)` を
  返す純関数。**env は呼び出し時に os.environ から読む**(import 時ではない。
  テストの monkeypatch を効かせるため)。判定内容は上記分類表のとおり。
  各 violation 文字列は「env 名 + なぜ必須か + 設定例」を1行で含める。
- `validate_startup_env(logger) -> None`:
  - warnings を全件 `logger.warning`(機能名を含む定型フォーマット)。
  - violations があれば全件を `logger.error` で列挙後、
    `STARTUP_ENV_VALIDATION`(既定 strict)が warn でない限り、全件を連結した
    1つの RuntimeError を raise(1件目で止めず**全件集約**する)。
    warn モードでは violations も warning として出力し起動継続。

### 2. startup() への組み込み

- `main.py` の `startup()` 冒頭、`init_db()` より**前**に
  `validate_startup_env(logger)` を呼ぶ(DB/Redis 待ちの後に env で死ぬ、という
  無駄な順序を避ける)。他の startup 処理・_startup_complete の流れは無変更。

### 3. テスト `services/meeting-api/tests/test_env_validation.py`(新設)

既存 tests/ の流儀(conftest がテスト用 env を設定)に従い、monkeypatch で env を
操作する。少なくとも:
- (a) TRANSCRIPTION_SERVICE_URL 空/空白のみ → violation。非空 → violation なし。
- (b) STORAGE_BACKEND=gcs + GCS_BUCKET 空 → violation。gcs + 非空 → なし。
  未知 backend("foo")→ violation。既定(未設定=minio)→ なし。
- (c) 複数違反時、RuntimeError メッセージに**全 env 名が含まれる**(集約の検証)。
- (d) strict(既定・未設定時)で raise、`STARTUP_ENV_VALIDATION=warn` で raise せず
  caplog に warning が出る。不正値("yes" 等)は strict 扱い。
- (e) オプション分類: TRANSCRIPTION_SERVICE_TOKEN 空 / VOICEPRINT_SERVICE_URL 空 /
  Drive 有効+資格情報欠落 → violation ではなく warning(caplog)。
  DEFERRED_TRANSCRIPTION_SERVICE_URL 空 → warning も出ない。
- (f) 呼び出し順の静的ガード: `inspect.getsource(main.startup)` 内で
  `validate_startup_env` の出現位置が `init_db` より前であることを assert
  (startup() 全体の実行は Redis 依存で重いため、静的検査で代替)。

### 4. 変更しないもの

- config.py / database.py の既存 import 時検証(重複チェックを足さない)。
- deploy/compose・deploy/helm(空既定のまま)。
- runtime-api の同種問題(compose :323。別サービス、advisory として記録)。
- ドキュメント(docs/deployment.mdx への必須 env 追記は任意。契約外)。

## Why(実装者に渡さない)

- 分類を「文字起こしが必ず壊れるもの」だけに絞る理由: 必須を広げるほど開発環境と
  オプション構成(Gemini・Drive・voiceprint・TTS)の起動が壊れ、fail-fast が
  「うるさいので warn モード常用」に堕ちる。最小の必須集合が strict 既定を維持する
  唯一の方法。INTERNAL_API_SECRET を warning に留めるのも同じ理由(compose/helm に
  既定値があり、必須化の実益より偽陽性リスクが大きい)。
- MINIO_* を必須にしない理由: コード既定値で開発が動く設計が既にあり、「未設定」と
  「誤設定」を起動時に区別できない。誤設定は録音アップロード失敗として finalizer 側で
  顕在化し、会議終了前(録音中)に検知される経路が既にある。
- startup() 冒頭で呼び、import 時にしない理由: import 時 raise はテストで
  monkeypatch が効かず(conftest の env 設定順に依存)、config.py の REDIS_URL 検証で
  既にその硬さが問題になり得る前例がある。呼び出し時読み取りの純関数なら
  単体テストが完全に書ける。
- runtime-api をスコープ外にする理由: 同じ空既定だが、bot 起動経路の URL は
  meeting-api が bot_config で渡すものが主で、影響分析が別途必要。混ぜると
  影響範囲が2サービスに広がる。advisory として残し必要なら別タスク化。

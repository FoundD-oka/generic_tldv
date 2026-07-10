# Test Evidence — issue-27-voiceprint (L・生体PIIパス)

Date: 2026-07-10. 実装コミット: aae86b5、tribunal修正コミット: 7a5df37。
実装はSonnetサブエージェント3バッチ並列＋修正3バッチ並列。検証はオーケストレータが
独立再実行して記録（自己申告をevidenceにしない）。

## 実装後の検証（aae86b5時点）

- `pytest services/meeting-api/tests/ -q` → **482 passed, 18 skipped**
  （+64新規: consent不変条件のSQLite実FKテスト、Fernet暗号化＋無効モード、
  マッチング（lane offset演算・タイムアウト・照合後破棄）、redaction
  （MeetingResponse.data／transcript data）、PG/Redis両経路のsuggested overlay、
  replace時のstale削除、transcript非遅延・非失敗、affected_clusters、
  retention sweep、enroll-from-clusterの単一トランザクション）
- `pytest services/voiceprint-service/tests/ -q` → **17 passed**
  （モデル完全モック: 認証・413容量・422短音声・429同時実行・health遷移・
  192次元出力・**ベクトル非ログのassert**）
- `npx vitest run`（dashboard）→ **112 passed** ＋ tsc clean
- adapter検証 5/5（voiceprint-embedder.adapter.json含む）
- `docker compose --profile voiceprint config` → mem_limit 1.5g・env全解決

## Tribunal（L必須・PII特化観点）

- Phase 1 Bug-Finder: `tribunal/finder-report.md`（12 findings）
- Phase 2 Adversarial: `tribunal/adversarial-report.md`（**12件全confirmed・反証ゼロ**。
  criticalの追加裏付け: expire_on_commit=False、遅延botコールバックwebhook経路）
- Phase 3 Referee: `tribunal-report.json` — **confirmed 12（critical 2, medium 4,
  low 6）、false positive 0**。合意バイアス対策として全件を独立再検証済み
- HD: 12件を issue-27-voiceprint 配下で記録

## Tribunal後の修正・再検証（7a5df37）

- **BUG-001（critical）**: webhook redactionを `MEETING_DATA_REDACTED_KEYS`
  単一ソース化 — speaker_suggestionsは全webhook出口から遮断（envelope回帰テスト）
- **BUG-002（critical）**: マッチングfollow-upは書き込み直前に
  `with_for_update` 再取得＋suggestionsキーのみマージ — 併走PATCHが生存
- BUG-003〜012: env名統一＋clip系配線、受諾時オファー抑止、retention guard、
  ストリーミング容量チェック、temp leak、compare_digest、unique制約、
  reject再調整、NaNフィルタ、merge掃除
- 独立再検証: meeting-api **492 passed**、voiceprint-service **20 passed**、
  dashboard **121 passed**、tsc clean

## 備考

- 機能はopt-in（composeプロファイル"voiceprint"＋VOICEPRINT_ENCRYPTION_KEY必須。
  キー未設定なら機能無効モード）。デプロイ即影響なし
- suggest専用（auto適用なし）。閾値0.78はFMR/FRRログ実測までの初期値
- PIIポリシー8項目＋代理同意運用受容は承認済み（approvals.jsonl）

## Fable外部コンサル（final audit）と対応（2026-07-10）

- verdict: **SHOULD_FIX**（MUST_FIXなし。PII露出制御は全出口経路で確認、
  consent不変条件・テナント分離・disabledモード・retention・transcript非遅延・
  match-then-discardの整合を実コードで確認済み）
- F1（採用・修正）: BUG-002修正の残存レース — 最終書き込みをエントリ単位マージ化
  （freshな行のsuggestions＋本runの新規エントリのみ）。併走reject/confirmの
  復活を3本の回帰テストで封じ（修正前失敗・修正後成功を実証）
- F2（延期）: retention 30日近似は暦24ヶ月よりわずかに早い削除＝安全側
- F3（既対応）: suggestions内profile_idは全出口でredaction済み
- 修正後の独立再検証: meeting-api **495 passed**

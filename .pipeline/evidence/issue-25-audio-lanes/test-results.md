# Test Evidence — issue-25-audio-lanes (L)

Date: 2026-07-10. 実装コミット: 9aed38b。検証はオーケストレータが独立再実行して記録。

## 実行結果

- `pytest services/meeting-api/tests/ -q`
  （/tmp/generic_tldv_meeting_api_venv）→ **393 passed, 18 skipped**
  - 新規: test_recordings_lane_media.py **6 passed**
    （lane型述語、lane webm=audio/webm、audio+2レーンの独立エントリ＋メタ継承、
    U.7 laneマスター保持、公開APIビューのlane除外、削除経路のlane包含）
  - 新規: test_recording_finalizer_lanes.py **5 passed**
    （audio master連結入力にlaneキー不混入=バイト不変、lane master生成＋
    playback_url境界、sweepのlaneキーparse、未finalize laneのsweep残留、
    reconciler単一ライター一般化）
  - 新規: test_final_transcription_lanes.py **5 passed**
    （lane master発見、単独レーン自動確定＋segment_id laneKey、
    all-or-nothing失敗フォールバック、時間予算超過フォールバック、
    保存済み `lane:{key}` 修正の優先＋speaker_auto保持）
- `cd services/vexa-bot/core && npx tsc --noEmit` → exit 0
- `cd services/vexa-bot/core && npm run build` → 成功、
  `dist/browser-utils.global.js` に BrowserLaneRecorderManager 含有確認
- `cd services/dashboard && npx vitest run` → **73 passed**（回帰なし）
- GitNexus `detect-changes` → 変更シンボルは想定内
  （uploadChunk/_handleChunk/internal_upload_recording/
  run_deferred_transcription/startGoogleRecording 系フロー）

## Tribunal（L必須）

- Phase 1 Bug-Finder: `tribunal/finder-report.md`（23 findings）
- Phase 2 Adversarial: `tribunal/adversarial-report.md`
- Phase 3 Referee: `tribunal-report.json`（pr-ready gate対象）
- confirmed bug の修正・再検証は本ファイル末尾に追記する。

## 備考

- 実会議PoC（lane数・分断率・自動確定精度の計測）は計画どおり実装後の
  運用検証項目。フラグ default off のためデプロイ即影響なし。

## Tribunal後の修正・再検証（2026-07-10、コミット後追記）

Referee確定: confirmed 16 / false-positive 7 / critical 2（tribunal-report.json）。
confirmed全16件を2バッチ並列（meeting-api側・vexa-bot側、Sonnetサブエージェント）
で修正し、オーケストレータが独立再検証：

- `pytest services/meeting-api/tests/ -q` → **401 passed, 18 skipped**
  （修正前393→+10新規テスト: offset伝播/DOM-vote整合、all-or-nothing
  未finalize検出、予算の逐次チェック、BUG-011スキップガード、
  lane session_uid/source_lane_paths、BUG-003ゲート、BUG-014警告ログ）
- `npx tsc --noEmit`（vexa-bot core）→ clean
- `npm run build` → 成功。`dist/browser-utils.global.js` の
  `window.VexaBrowserUtils = {...}` 代入に `BrowserLaneRecorderManager`
  が含まれることをgrepで実物確認（BUG-001解消）
- `npx vitest run`（dashboard）→ **73 passed**（回帰なし）

HD: 16件を `issue-25-audio-lanes` 配下で記録済み（.pipeline/rules/hd-log.tsv）。
修正コミット: tribunal修正コミット（親: 9aed38b）。

## Fable外部コンサル（final audit）と対応（2026-07-10）

- verdict: **SHOULD_FIX**（MUST_FIXなし・confidence high）。
  `external-consultation/consultation-summary.json` / `fable.md` 参照。
- F1（採用・修正）: BUG-011再ガードのsession非対称迂回 → 再ガードから
  lane_fallback_reason要件を除去＋マルチセッションfixtureテスト追加。
- F2（採用）: `cd services/vexa-bot/core && npm test` → **18 passed, 0 failed**
  （本追記が証跡）。「フラグoffバイト比較テスト」は明示的deviationとして記録:
  uploadChunkのlane引数はoptionalで、未指定時のmetadata構築は
  `...(lane ? {...} : {})` により従来キー集合と構造的に同一（コード検査＋tsc、
  かつサーバ側連結キー集合不変テストで担保）。自動バイト比較は実会議PoC時に
  ゴールデン録取と合わせて追加する。
- F3/F5（延期）: メモリ実測・offsetスキュー実測は有効化前PoCの計測項目。
- F4（既対応）: 承認はリポジトリ前例（空diffハッシュ＋target欄にコミット範囲明記）
  に従いつつ、最終コミット後に範囲付きで再追記。

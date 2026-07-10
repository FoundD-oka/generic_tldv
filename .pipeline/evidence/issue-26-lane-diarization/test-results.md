# Test Evidence — issue-26-lane-diarization (L)

Date: 2026-07-10. 実装コミット: 58200e2、tribunal修正コミット: 26ff257。
実装はSonnetサブエージェント3バッチ並列＋修正1バッチ。検証はオーケストレータが
独立再実行して記録（自己申告をevidenceにしない）。

## 実装後の検証（58200e2時点）

- `pytest services/meeting-api/tests/ -q` → **411 passed, 18 skipped**
  （+9新規: 共有マイク2安定クラスタでspeaker/speaker_auto=None（AC1/AC5）、
  単独＋微小ノイズクラスタで誤分割なし（AC3）、全微小クラスタ→solo扱い（AC4）、
  saved rename が共有マイク処理後も適用（AC2）、needs_review読み出し導出）
- `pytest services/transcription-service/tests/ -q` → **42 passed, 1 skipped**
  （golden fixture replay: token_count 7+4+5=16 を実foldから導出して完全一致）
- `npx vitest run`（dashboard）→ **87 passed** ＋ `tsc --noEmit` clean
  （+13新規: identity/表示ラベル分離、要確認の話者A/B、生lane id非表示、
  REST mapperのstatus保持）
- GitNexus detect-changes → 想定シンボルのみ（15 symbols）

## Tribunal（L必須）

- Phase 1 Bug-Finder: `tribunal/finder-report.md`（6 findings）
- Phase 2 Adversarial: `tribunal/adversarial-report.md`（5 confirmed / 1 disproved）
- Phase 3 Referee: `tribunal-report.json` — **confirmed 5（medium 2, low 3）、
  false positive 1、critical 0、fix-immediately 0**
- HD: 5件を issue-26-lane-diarization 配下で記録済み

## Tribunal後の修正・再検証（26ff257）

- BUG-001: clusterlessセグメントを `lane:{key}:unclustered` へ名前空間化
- BUG-002: buildSpeakerMergeをidentityキー基準に（needs_review含むマージ）
- BUG-004: 閾値envのdecimal許容 / BUG-005: Redis経路もderive-first
- BUG-006: monitor判定（不変条件コメントのみ）
- 独立再検証: meeting-api **415 passed**、dashboard **89 passed**、tsc clean

## 備考

- 本機能はPhase 2のレーン基盤（RECORD_PARTICIPANT_LANES、既定off）に乗るため
  デプロイ即影響なし。安定性閾値（2.0s/5tokens）は有効化前PoCで実測チューニング。
- AC5の挙動変更（共有マイクレーンのDOM投票名破棄）はプラン承認時にユーザー確認済み。

## Fable外部コンサル（final audit）と対応（2026-07-10）

- verdict: **MUST_FIX**（実装本体は計画v2と整合をソース検査で確認、指摘は検証の抜け）
- F1（MUST_FIX・採用）: 契約必須の「安定2＋不安定1クラスタ非吸収」fixture追加
- F2（採用）: soloレーン生id漏れ修正 — `未特定の話者X` ラベル＋
  `resolveSpeakerLabelByKey` を全キー描画箇所に適用
- F3（採用）: 閾値ちょうど境界値fixture追加（unit＋end-to-end）
- F4（既対応）/ F5（延期・構造担保）
- 修正後の独立再検証: meeting-api **418 passed**、dashboard **102 passed**、tsc clean

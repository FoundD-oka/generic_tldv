# 実装計画: Dashboard UX・再文字起こし状態改善

## 目的

辞書の有効状態、ナビゲーション名、声紋プレビュー準備、再文字起こし状態を、ユーザーが見誤らない表示と操作制御へ揃える。

## 実装範囲

1. 無効化した辞書行の背景と文字色をグレー化する。
2. サイドパネルの日本語ナビゲーション「会議」を「会議一覧」へ変更する。
3. 声紋録音停止から確認音声のメタデータ読込完了まで、全画面ローディングで操作を遮断する。Blob URL生成直後の `readyState` 確認と10秒タイムアウトを設け、解除不能を防ぐ。
4. 再文字起こしの `queued` / `running` を会議の共通表示で「処理中」、`failed` を「再処理失敗」と表示する。
5. 再文字起こし開始直後はPOSTレスポンスの `queued` / `running` を詳細画面へ反映し、処理中は詳細APIを2.5秒間隔で再取得する。POST後の旧 `completed` データで処理中表示を上書きしない。
6. 一覧APIの軽量データにも `final_transcription.status` を含め、処理中の会議がある間は一覧を2.5秒間隔でサイレント更新して完了・失敗表示へ追従する。処理中がなくなった時とアンマウント時にタイマーを解除し、一時的な取得失敗は既存一覧を維持して次回更新で再試行する。

## 対象ファイル

- `services/dashboard/src/app/dictionary/page.tsx`
- `services/dashboard/src/lib/dashboard-copy.ts`
- `services/dashboard/src/app/voiceprints/page.tsx`
- `services/dashboard/src/types/vexa.ts`
- `services/dashboard/src/components/transcript/transcript-viewer.tsx`
- `services/dashboard/src/app/meetings/[id]/page.tsx`
- `services/dashboard/src/app/meetings/page.tsx`
- `services/dashboard/src/stores/meetings-store.ts`
- `services/dashboard/src/lib/retranscription-status.ts`
- `services/dashboard/src/lib/single-flight-polling.ts`
- `services/dashboard/src/lib/voiceprint-preview-state.ts`
- `services/dashboard/src/components/voiceprints/voiceprint-preparation-gate.tsx`
- `services/meeting-api/meeting_api/meetings.py`
- 関連するDashboard／Meeting APIテスト

## 完了条件

- 辞書をオフにすると行全体の背景と文字がグレーになる。
- サイドパネルに「会議一覧」と表示される。
- 声紋確認音声の準備中は他の操作ができず、読込完了・エラー・タイムアウトのいずれでも解除される。
- 再文字起こし開始後は一覧・詳細とも「処理中」、成功後は「完了」、失敗後は「再処理失敗」と表示される。
- 再処理が失敗しても既存の文字起こし閲覧・再実行操作は維持される。
- 対象テスト、Dashboard全テスト、production build、Meeting API対象テスト、GitNexus検査が通る。

## スコープ外

- 辞書APIや声紋APIのデータ契約変更。
- 再文字起こしジョブ実行方式の変更。
- 英語ロケールのナビゲーション文言変更。

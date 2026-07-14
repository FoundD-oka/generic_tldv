# KPI逆算ロードマップ: 無言参加者ロスター

## 将来状態

Google Meetでボットが観測できた実参加者を、発話の有無に関係なく会議データへ残し、既存ダッシュボードの参加者一覧・参加者由来タイトル・エクスポートへ反映する。

## KPIと検証可能な品質条件

| KPI | 現状 | 今回の品質ライン | 証跡 |
|---|---|---|---|
| 無言・途中退出者の保持 | 話者にならないと欠落 | 複数スナップショットのテストで100%保持 | `participant-roster.test.ts` |
| 終了経路の保存 | `speaker_events`のみ | completed・failed・STOPPING deferredの全対象経路で保存 | `test_callbacks.py` |
| 後方互換 | ロスター項目なし | 未指定時は従来ペイロード、旧会議は話者フォールバック | callback・post-meetingテスト |
| 手動編集の尊重 | 自動値と出所区別なし | PATCH後は`manual`として自動更新から保護 | `test_meetings.py` |
| データ境界 | 上限・不透明IDなし | 250件、入力1000件、時刻上限、ロスター内の生Google ID 0件 | TS/Python正規化テスト |
| 既存UI反映 | 話者由来のみ | 既存`participants`契約を維持し、カード・エクスポートテスト成功 | dashboardテスト |

## チェックポイント

1. ブラウザ内でPeople panelと表示タイルを定期走査し、退出後も累積値を保持する。
2. 終了前にNodeへスナップショットし、任意フィールドとしてmeeting-apiへ送る。
3. APIで正規化・永続化し、手動値を優先しながら既存`participants`へ投影する。
4. 対象テスト、meeting-api全単体テスト、ダッシュボード契約テスト、GitNexus変更検出を通す。

## 運用後の確認

実会議での観測率はGoogle MeetのDOMと会議規模に依存するため、リリース後に「People panel表示人数」と`observed_participants`件数をサンプル比較する。今回は認証済み会議と参加者同意を伴うライブ検証を自動実行しない。

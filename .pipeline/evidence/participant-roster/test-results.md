# 検証結果 — participant-roster

実施日: 2026-07-14

## 結論

検証契約で定めた自動テスト・ビルド・変更影響検査はすべて成功した。無発言・途中退出参加者の累積、手入力参加者の優先、旧botの話者フォールバック、会議単位の不透明ID、既存ダッシュボード表示の互換性を確認した。

## 実行結果

- Vexa bot: `cd services/vexa-bot/core && npm test`（合計53件成功）
  - Zoom既存回帰: 18件成功
  - participant roster正規化・累積: 18件成功
  - Google Meet実ブラウザDOM: 7件成功
  - chat / People panel共有ロック: 5件成功
  - unified callback: 5件成功
- Vexa bot build: `cd services/vexa-bot/core && npm run build` → 成功
- meeting-api対象テスト: 115件成功（警告16件、失敗0件）
- meeting-api全単体テスト: 585件成功、18件skip（既存のPostgreSQL実接続テストを契約どおり除外）
- dashboard対象テスト: 2ファイル・10件成功
- `git diff --check` → 成功
- Evidence manifest: 必須コマンド6件すべてexit 0、`missing_evidence=[]`、範囲外変更なし
- PR-ready gate: `bash .claude/hooks/pr-ready-gate.sh participant-roster` → `pr-ready: ready`（全必須ゲートOK）。
- Outcome judge: `bash scripts/harness/outcome-judge.sh participant-roster` → `outcome pass`。
- GitNexus detect-changes: 実行成功。共通callbackを含むためリスク判定はCRITICALだが、変更は任意の末尾引数・任意JSONフィールドで、bot/API回帰テストにより後方互換を確認した。

## 独立QAで見つけた問題と修正

独立QAは、通常のparticipant tile regionをPeopleパネルと誤認し得る問題を検出した。People panelのfallback rootから`role="region"`を除外し、Peopleボタンの`aria-controls`が明示的に指す可視要素だけをregionとして許可するよう修正した。回帰テスト追加後、独立QAの再判定は`pass`、blocking findingは0件となった。

Sol Ultra監査で追加検出した、Peopleパネル行の話者検出混入、チャット送信とのパネル切替競合、多言語の自己表示名除外も修正した。初回・周期スキャン前にPeopleボタンの`aria-controls`を確定し、発言イベントごとにもPeopleパネル配下か再判定する。chat sendとPeople snapshotは共有ロックで直列化し、親要素の`data-self-name`、`is_self`、各言語の括弧付き自己注記を除外する。

実ブラウザDOM回帰では、開いた状態の`role="region"`パネル、途中退出者の累積、親に自己マーカーを持つ入れ子DOM、stale observer、Peopleボタン差し替え、パネル行からの誤発言イベントがないことを確認した。

最終Sol Ultra監査は`SHIP`、confidence=`high`、MUST_FIX=0。5件のSHOULD_FIXは、実Google Meetの追加タイミング耐性、チャット復元の遅延競合、STOPPING時の既存`deferred`再試行、API受信前の本文サイズ制限、手動PATCHとの同時更新競合であり、今回の通常経路とデータ完全性を止めない残存リスクとして扱う。

## 未実施・残存リスク

- 認証済みGoogle Meetと参加者同意が必要なため、実会議E2Eはこの作業環境では未実施。初回リリース時は小規模canaryでPeopleパネル人数と`observed_participants`を照合する。
- Google Meet DOM、表示言語、仮想スクロールに依存するため、大規模会議の画面外参加者はベストエフォート。
- 同一表示名の参加者は1名へ統合する仕様。
- Zoom・Teamsの実会議回帰は未実施。共通callbackの互換性は自動テストで確認した。

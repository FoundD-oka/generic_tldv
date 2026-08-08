---
generated_by: fable
task_id: p05-detail-wiring
base-commit: 5cae3a05550e8679b156e88652b0dfa2193d30f1
size: M
---

# P0-5(部分): 会議詳細ページの配線 — SRT/VTT エクスポート露出(FT-7)+ 失敗時の再実行導線(UI-5)

## ゴール

依頼の文字通りの内容: P0-5「作ったのに効いていないものの配線」のうち、
(1) 実装済みの SRT/VTT エクスポートをエクスポートUIに露出する、
(2) 失敗会議の `BotFailedIndicator` に `onRetry` を渡して再実行導線を接続する。

再設計後のゴール(reframe なし・分割のみ): P0-5 の3項目のうち ST-21(helm
liveness 配線)は検証手段が異なる(ローカルに helm なし)ため後続タスク
`p05-collector-liveness` に分離。本タスクは dashboard 詳細ページ1ファイルで
完結する FT-7 + UI-5 を最小単位として実装する。監査文書
(current-state.md FT-7 / UI-5)と乖離なし。

## 対象

- 変更: `services/dashboard/src/app/meetings/[id]/page.tsx` のみ
- 追加: `services/dashboard/tests/test_meeting_detail_wiring.test.ts`(新規)
- 不変更(禁止): pyproject.toml / .github/workflows / sweeps.py / meetings.py /
  retry.py / src/types/vexa.ts / tests/test_meeting_status_display.test.ts /
  postgres統合テスト関連 /
  Codex並行変更中の4ファイル(src/components/meetings/meeting-card.tsx,
  src/app/login/page.tsx, deploy/compose/docker-compose.yml,
  tests/test_meeting_cards_ui.test.ts)

## 実装 How(設計判断。実装者の裁量に委ねない)

### 1. FT-7: エクスポートドロップダウンに SRT/VTT を追加(2箇所)

`page.tsx` にはエクスポートドロップダウンが2つある(base-commit 時点で
:1367-1418 の通常ヘッダと :1674-1723 の録画表示時コンパクトヘッダ)。
**両方**の `.jsonをダウンロード` 項目の直後に以下2項目を追加する。

- `<DropdownMenuItem onClick={() => handleExport("srt")}>` — 表示文字列
  `.srtをダウンロード`、アイコンは隣接項目と同じく
  `<FileText className="h-4 w-4 mr-2" />`(既存 import を再利用、新規 import 不可)
- `<DropdownMenuItem onClick={() => handleExport("vtt")}>` — 表示文字列
  `.vttをダウンロード`、同上
- コンパクトヘッダ側の2項目には、隣接項目と同様に
  `disabled={transcripts.length === 0}` を付ける(通常ヘッダ側は隣接項目に
  合わせて付けない。`handleExport` 内の空ガードが既にある)。

`handleExport`(:527)は既に `"srt" | "vtt"` を処理する。`lib/export.ts` /
handleExport / MIMEタイプは変更しない。

### 2. UI-5: BotFailedIndicator への onRetry 接続

a. `page.tsx` に state `const [isRetryingBot, setIsRetryingBot] = useState(false);`
   を追加する。

b. ハンドラ `handleRetryBot` を `useCallback` で追加する(仕様固定):

```tsx
const handleRetryBot = useCallback(async () => {
  if (!currentMeeting || isRetryingBot) return;
  setIsRetryingBot(true);
  try {
    const data = currentMeeting.data ?? {};
    const request: CreateBotRequest = {
      platform: currentMeeting.platform,
      native_meeting_id: currentMeeting.platform_specific_id,
    };
    if (typeof data.passcode === "string" && data.passcode) {
      request.passcode = data.passcode;
    }
    if (typeof data.meeting_url === "string" && data.meeting_url) {
      request.meeting_url = data.meeting_url;
    }
    if (data.transcribe_enabled === false) {
      request.transcribe_enabled = false;
    }
    const meeting = await vexaAPI.createBot(
      applyBotCreationDefaults(withPostMeetingAutoStop(request))
    );
    toast.success("新しいボットをリクエストしました");
    router.push(`/meetings/${meeting.id}`);
  } catch (error) {
    toast.error("ボットの再リクエストに失敗しました", {
      description: (error as Error).message,
    });
  } finally {
    setIsRetryingBot(false);
  }
}, [currentMeeting, isRetryingBot, router]);
```

   - `applyBotCreationDefaults` / `withPostMeetingAutoStop` は
     `@/lib/bot-create-defaults` から import(zoom callback :74 と同じ経路。
     bot_name「カボス」・language ja・auto-stop の既定を揃える)。
   - `CreateBotRequest` 型は `@/types/vexa` から import(型ファイル自体は不変更)。
   - 成功時は新規会議IDへ遷移する(POST /bots は failed 会議に対して新しい
     Meeting 行を作る。meetings.py:965-982 の409ガードは
     requested/joining/awaiting_admission/active のみ対象と確認済み)。
   - 409(既にアクティブな会議あり)や Zoom OAuth 要求はエラートーストで表示
     するに留める。Zoom OAuth 再認証フローの組み込みは本契約のスコープ外
    (既知の制限として後続に委ねる)。

c. :1963 付近の `<BotFailedIndicator ... />` に `onRetry={handleRetryBot}` を
   追加する。`bot-status-indicator.tsx` は既に onRetry 受理・ボタン表示
  (「新しいボットでもう一度試す」:393-399)を実装済みのため**変更しない**。

### 3. テスト(ソース文字列契約、既存パターン準拠)

新規 `services/dashboard/tests/test_meeting_detail_wiring.test.ts`
(test_transcript_reprocess_ui.test.ts と同形式)で最低限:

- page.tsx に `handleExport("srt")` と `handleExport("vtt")` が**各2回以上**
  出現する(split で出現数を数える)
- page.tsx に `.srtをダウンロード` と `.vttをダウンロード` が出現する
- page.tsx に `onRetry={handleRetryBot}` / `vexaAPI.createBot` /
  `applyBotCreationDefaults(withPostMeetingAutoStop(` /
  `router.push(\`/meetings/${meeting.id}\`)` が出現する
- 回帰ロック: bot-status-indicator.tsx に `新しいボットでもう一度試す` が
  出現する(受け口の存続確認。ファイルは変更しない)

## 段取り・衝突回避

- base は main `5cae3a0` から新規 worktree を切る。
- Codex 並行変更(P0-6 相当、未コミット4ファイル)とは変更ファイルが完全に
  非交差であることを確認済み。マージ待ち不要で即実装可。
- st9〜st14(PR #41〜#46)の対象ファイルにも触れない。共存可。
- コミット前に `git diff --name-only` で変更が上記2ファイルのみであることを
  機械確認する(検証契約 FP-002)。

## Why(実装者に渡さない)

- P0 テーマは「失敗を失敗と表示し、ユーザー自身でリカバリできる」こと。UI-5 は
  失敗表示(ST-2/P0-1)の対、つまり表示の次の一手。問い合わせ対応なしで社内
  パイロットを回すという Phase 0 の狙いに直結する。
- FT-7 の SRT/VTT は下流のAI要約層・字幕用途への受け渡しインターフェース強化。
  実装コストほぼゼロで tl;dv ギャップを1つ消せる。
- ST-21 を分離したのは、helm がローカルに無く検証が文字列契約に落ちるため。
  vitest で強く検証できる UI 配線と契約を混ぜると、レビューの焦点がぼける。
- retry で Zoom OAuth フローを省いたのは、社内パイロットの主戦場が Google Meet
  であり、join-form の OAuth 分岐を詳細ページへ複製するコストに見合わないため。

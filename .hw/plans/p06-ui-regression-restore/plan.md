---
generated_by: fable
task_id: p06-ui-regression-restore
base-commit: 96c07857ac68a5eaff538df702563d8d668b4d08
size: M
---

# P0-6: 会議カードUI退行の復元と一覧・詳細タイトル解決の統一

## ゴール

文字通りの依頼: 監査項目 UI-1〜4 の復元(参加者表示・会議コード補助表示・
相対日時の復元、タイトルクリック衝突の解消)と、詳細ページの calendar_title
未参照の解消。

再設計後のゴール(reframe): 退行の起点は a223194(カードレイアウト化)であり、
失われたのは 0437195(メモ.md対応本体)が持っていた表示要素群。差分精査の結果、
監査リストに漏れていた同一起源の退行が1件ある(メモインジケータ=FileText+
ツールチップ。メモ.md対応の中核要素)。これも同時に復元する。単純 revert は
不可(現行カードには browser_session 対応・created_at フォールバック・st12 の
getDetailedStatus 等の後続改善が載っている)ため、「失われた要素を現行のコンパクト
カード構造に移植する」方針を取る。あわせてタイトル解決ロジックを共通ヘルパー
`src/lib/meeting-title.ts` に抽出し、一覧・詳細・エクスポートファイル名・コピー
出力の全経路を統一する。退行防止として回帰テスト(ヘルパーの挙動ユニットテスト+
ソース文字列契約テスト)を追加する。

## 対象

- 新規: `services/dashboard/src/lib/meeting-title.ts`
- 変更: `services/dashboard/src/components/meetings/meeting-card.tsx`
- 変更: `services/dashboard/src/app/meetings/[id]/page.tsx`
  (base-commit 時点の行: 611-614, 682-683, 1223, 1329, 1336, 1647, 1652)
- 書換: `services/dashboard/tests/test_meeting_cards_ui.test.ts`
- 新規: `services/dashboard/tests/test_meeting_title.test.ts`
- 新規: `services/dashboard/tests/test_meeting_detail_title.test.ts`

## 前提(実装開始手順)

0. 作業は base-commit 96c0785 から切った worktree で行う(親セッションが用意済み)。
1. ベースライン(base-commit で実測済み): dashboard テストは
   **30 files / 224 tests 全パス**(`cd services/dashboard &&
   npm install --no-audit --no-fund && npm test`)。

## 設計判断(確定事項。実装者の裁量に委ねない)

### D1. タイトル解決ヘルパー `src/lib/meeting-title.ts`(全文指定)

```ts
import type { MeetingData } from "@/types/vexa";

export const UNTITLED_MEETING_LABEL = "無題の会議";

/** 手動編集名・カレンダー由来タイトルの解決。編集フォームの初期値にも使う。無ければ "" */
export function getCustomMeetingTitle(data?: MeetingData | null): string {
  return (
    data?.name || data?.title || data?.calendar_title || data?.calendar_event?.title || ""
  );
}

/** 一覧・詳細共通の表示タイトル解決 */
export function resolveMeetingTitle(
  data: MeetingData | null | undefined,
  platformSpecificId?: string | null,
): string {
  return getCustomMeetingTitle(data) || platformSpecificId || UNTITLED_MEETING_LABEL;
}
```

優先順位は現行カードの仕様を維持: name > title > calendar_title >
calendar_event.title > platform_specific_id > 「無題の会議」。空文字は falsy
なので自動的にスキップされる。

### D2. meeting-card.tsx の復元仕様

カードの現行構造(コンパクトカード、min-h-32、上部バッジ行/中央タイトル/下部
メタ行)は維持し、以下を変更する。

導出値(既存の rawTitle / calendarTitle / displayTitle 定義3行を置換):

```tsx
const customTitle = getCustomMeetingTitle(meeting.data);
const hasCustomTitle = customTitle !== "";
const displayTitle = resolveMeetingTitle(meeting.data, meeting.platform_specific_id);
const participants = meeting.data?.participants ?? [];
```

`timeSource = meeting.start_time || meeting.created_at` は現状維持(既存テスト
契約)。`handleStartEdit` 内の prefill は `setEditedTitle(customTitle)` に置換。

**(UI-3)クリック領域の分離**: 非編集時のタイトル領域を包む現行の
`<button type="button" ... onClick={handleStartEdit}>` を廃止し、次の構造にする
(タイトルクリックは Link にバブルして詳細遷移、編集は鉛筆ボタンのみ):

```tsx
<div className="flex items-start gap-2">
  <h3 className="line-clamp-2 flex-1 text-sm font-semibold leading-snug tracking-tight transition-colors group-hover:text-primary">
    {displayTitle}
  </h3>
  <Button
    type="button"
    size="icon"
    variant="ghost"
    className="h-6 w-6 shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
    aria-label="タイトルを編集"
    onClick={handleStartEdit}
  >
    <Pencil className="h-3.5 w-3.5" />
  </Button>
</div>
```

`handleStartEdit` は既存どおり preventDefault + stopPropagation を行うので
Link 遷移は発火しない。Link の `onClick={(event) => isEditingTitle &&
event.preventDefault()}` ガードは維持。カード内に素の `<button>` 要素を残さない
(契約テストが `not.toContain("<button")` で検査する)。

**(UI-1)会議コード補助表示**: h3 ブロック直下に追加。主見出しへの会議コード
昇格(タイトル無し時)は displayTitle のフォールバックで既に実現しているため、
復元するのは「タイトルあり時の補助降格表示」のみ:

```tsx
{hasCustomTitle && meeting.platform_specific_id && (
  <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
    {meeting.platform_specific_id}
  </p>
)}
```

**(UI-2)参加者表示**: コード補助行の直下に追加。最大3名をカンマ区切り、
4名以上は「ほかN名」、1行 truncate(0437195 と同仕様):

```tsx
{participants.length > 0 && (
  <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
    参加者: {participants.slice(0, 3).join(", ")}
    {participants.length > 3 && ` ほか${participants.length - 3}名`}
  </p>
)}
```

データ裏取り済み: `data.participants` は `string[]`(meeting-api 側で
participant_roster / transcript_speakers / manual の3系統が書き込み、
`participants_source` で管理。表示側は配列をそのまま使えばよい)。

**(UI-4)日付表示**: 下部メタ行の Calendar 表示を年付きに戻し
(`"M月d日"` → `"yyyy年M月d日"`)、その隣に相対日時を復元する。閾値は設けず
timeSource があれば常に併記(0437195 と同仕様)。既存の Calendar ツールチップ
(ローカル+UTC 表示)は変更しない(tests3 の TZ lock が
`toLocaleString` / `Intl.DateTimeFormat` を検査するため):

```tsx
<span>{format(parseUTCTimestamp(timeSource), "yyyy年M月d日", { locale: ja })}</span>
```

```tsx
{timeSource && (
  <div className="flex items-center gap-1.5 text-muted-foreground">
    <Clock className="h-3 w-3" />
    <span>{formatDistanceToNow(parseUTCTimestamp(timeSource), { addSuffix: true, locale: ja })}</span>
  </div>
)}
```

**(UI-5、監査未掲載・同一起源の退行)メモインジケータ**: メタ行の duration の後に
復元(0437195 と同仕様、先頭100文字プレビュー):

```tsx
{typeof meeting.data?.notes === "string" && meeting.data.notes.trim() && (
  <Tooltip>
    <TooltipTrigger asChild>
      <div className="flex cursor-help items-center gap-1.5 text-muted-foreground">
        <FileText className="h-3 w-3" />
        <span>メモ</span>
      </div>
    </TooltipTrigger>
    <TooltipContent side="top" className="max-w-xs">
      <div className="text-xs text-muted-foreground">
        {meeting.data.notes.length > 100
          ? `${meeting.data.notes.substring(0, 100)}...`
          : meeting.data.notes}
      </div>
    </TooltipContent>
  </Tooltip>
)}
```

import 追加: `Clock, FileText`(lucide-react)、
`getCustomMeetingTitle, resolveMeetingTitle`(`@/lib/meeting-title`)。
`duration !== null` 判定・browser_session 対応・st12 statusConfig・
ステータスツールチップは現状維持。

### D3. meetings/[id]/page.tsx のタイトル解決統一(7箇所)

import 追加後、base-commit 時点の以下を置換する。置換後、page.tsx に
`data?.name ||` 形のインライン解決を1つも残さない(契約テストで検査)。

| 行 | 現行 | 置換後 |
|---|---|---|
| 611-614 | `currentMeeting?.data?.name \|\| currentMeeting?.data?.title \|\| currentMeeting?.platform_specific_id \|\| "recording"` | `resolveMeetingTitle(currentMeeting?.data, currentMeeting?.platform_specific_id)`(後段のサニタイズと `\|\| "recording"` フォールバックは維持) |
| 682-683 | `if (meeting.data?.name \|\| meeting.data?.title) { output += ... }` | `const copyTitle = getCustomMeetingTitle(meeting.data); if (copyTitle) { output += \`タイトル: ${copyTitle}\n\`; }` |
| 1223 | ツールバー `currentMeeting.data?.name \|\| currentMeeting.platform_specific_id` | `resolveMeetingTitle(currentMeeting.data, currentMeeting.platform_specific_id)` |
| 1329 | h1 `data?.name \|\| data?.title \|\| platform_specific_id` | 同上 |
| 1336 | prefill `data?.name \|\| data?.title \|\| ""` | `getCustomMeetingTitle(currentMeeting.data)` |
| 1647 | prefill 同上 | `getCustomMeetingTitle(currentMeeting.data)` |
| 1652 | コンパクトヘッダ `data?.name \|\| data?.title \|\| platform_specific_id` | `resolveMeetingTitle(currentMeeting.data, currentMeeting.platform_specific_id)` |

これにより詳細ページも calendar_title / calendar_event.title を参照し、一覧と
表示が一致する(UI-1後段の解消)。p05 で追加された SRT/VTT エクスポート・
onRetry 配線には触れない。

### D4. 回帰テスト(退行防止の仕組み化)

既存様式に従う: vitest、ソース文字列契約方式(`readFileSync` + `toContain`)。
testing-library は devDependencies に存在しないため導入しない(挙動検証は
ヘルパーの純関数ユニットテストで担う)。

1. **`tests/test_meeting_title.test.ts`(新規、挙動ユニットテスト)**:
   `resolveMeetingTitle` / `getCustomMeetingTitle` を実際に呼び、
   name > title > calendar_title > calendar_event.title > platform_specific_id >
   「無題の会議」の優先順位、空文字スキップ、data 未定義時の挙動を検証
   (最低6ケース)。
2. **`tests/test_meeting_cards_ui.test.ts`(書換)**: 既存4テストのうち
   タイトル解決の literal 断言をヘルパー参照に更新し、以下を追加:
   - ヘルパー使用: `from "@/lib/meeting-title"`、
     `resolveMeetingTitle(meeting.data, meeting.platform_specific_id)`、
     `setEditedTitle(customTitle)`、`not.toContain("meeting.data?.name")`
   - 参加者: `participants.slice(0, 3).join(", ")` と
     `` ほか${participants.length - 3}名 ``
   - コード補助: `hasCustomTitle && meeting.platform_specific_id` と `font-mono`
   - 日時: `"yyyy年M月d日"` と
     `formatDistanceToNow(parseUTCTimestamp(timeSource), { addSuffix: true, locale: ja })`
   - クリック分離: `aria-label="タイトルを編集"` を含み、素の `<button` を
     含まない(`not.toContain("<button")`)
   - メモ: `<span>メモ</span>` と `meeting.data.notes.substring(0, 100)`
   - 既存維持: `meeting.start_time || meeting.created_at`、grid 断言、
     `<MeetingCard meeting={meeting} />` 断言、`not.toContain("participantsTitle")`
3. **`tests/test_meeting_detail_title.test.ts`(新規)**: page.tsx ソースに対し
   - `from "@/lib/meeting-title"` を含む
   - `resolveMeetingTitle(` の出現回数 >= 4、`getCustomMeetingTitle(` >= 3
   - `not.toContain("data?.name ||")`(インライン解決の全廃)
   - prefill が `setEditedTitle(getCustomMeetingTitle(currentMeeting.data))`

## 実装手順(how)

1. base-commit の worktree で作業する(親セッションが用意済み)。
2. D1 ヘルパーを新規作成。
3. D2 のとおり meeting-card.tsx を変更(import 追加を忘れない)。
4. D3 のとおり page.tsx の7箇所を置換(行番号は base-commit 時点。実装時は
   パターンで検索して全箇所を漏らさないこと。置換後
   `grep -n "data?.name ||" src/app/meetings/\[id\]/page.tsx` が0件であることを確認)。
5. D4 のテストを作成・書換。
6. `cd services/dashboard && npm test` 全パスを確認(検証契約の数値条件)。
7. `bash .hw/verify.sh` を通す(TZ lock を含む)。
8. commit 後、`python3 .hw/fable_review.py p06-ui-regression-restore`。

## Why(実装者に渡さない)

- 監査 P0-6 の本質は「メモ.md対応(0437195)がカード化(a223194)で退行した」
  こと。0437195 と 96c0785 の差分精査で、監査記載の UI-1〜4 に加えメモ
  インジケータの消失を確認した。メモ.md対応の中核要素であり、これを外すと
  「退行の復元」というゴール自体が欠ける。一方 statusConfig.description の
  常時表示行も消えているが、情報はツールチップに残っており監査も挙げていない
  ため advisory 扱いで復元しない(コンパクトカードの情報密度を守る)。
- タイトル解決をヘルパーに抽出するのは、今回の不一致(詳細ページの
  calendar_title 未参照)が「同じロジックの手書きコピー」から生じたため。
  文字列契約テストだけでは優先順位の挙動を守れないので、純関数化して挙動
  ユニットテストを付ける。
- クリック分離は 0437195 の「素の h3 + ホバー鉛筆ボタン」パターンへの回帰。
  カード全面が Link である現行構造では、タイトルを編集ボタンにすると詳細遷移の
  主要クリックターゲットを奪う(UI-3 の指摘そのもの)。
- 相対日時に閾値を設けないのは 0437195 仕様との一致を優先するため。閾値付き
  切替(例: 7日以内のみ相対)は情報改善だが、本タスクは「復元」であり契約を
  超えて作り込まない。

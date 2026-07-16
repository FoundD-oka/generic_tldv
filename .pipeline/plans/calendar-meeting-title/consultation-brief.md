# Fable Consultation Brief: calendar-meeting-title

Generated: 2026-07-16T13:46:51.764285+00:00
Provider target: claude-fable-cli
Mode: plan
Model: fable

## Safety And Boundaries

- You are an advisory reviewer, not the implementer.
- Use local file reads and read-only shell inspection only.
- Do not edit files, run write commands, commit, push, install dependencies, or change state.
- Keep the answer concise. Each finding should be at most two sentences plus evidence.
- Treat your answer as advisory review evidence. Local tests, source checks, and project evidence remain the source of truth.

## Required Context Summary

### 1. Original Task And Plan Step

Task id: calendar-meeting-title
Plan step or checkpoint: カレンダー会議タイトルを一覧へ反映

Relevant plan artifacts:

```text
## plan.md

# 実装計画: カレンダー会議タイトルの一覧表示

## 目的

カレンダーから起動した会議を一覧で人間が識別できるようにし、表示名の優先順位を「手動編集名 → カレンダータイトル → 会議コード」に統一する。

## 現状

- カレンダータイトルは `meeting.data.calendar_event.title` に保存済み。
- 会議一覧APIの軽量レスポンスは `calendar_event` を除外しているため、ダッシュボードへタイトルが届かない。
- カード表示は参加者由来タイトルを会議コードより優先している。

## 実装

1. 会議一覧APIの軽量データへ `calendar_title` を追加する。
2. 一覧検索でもカレンダータイトルを検索対象にする。
3. カード表示と編集開始時の値を「手動編集名 → カレンダータイトル → 会議コード」に変更する。
4. 参加者由来タイトルの表示優先を削除する。
5. Meeting APIとDashboardの回帰テストを更新する。

## 対象ファイル

- `services/meeting-api/meeting_api/meetings.py`
- `services/meeting-api/tests/test_meetings.py`
- `services/dashboard/src/types/vexa.ts`
- `services/dashboard/src/components/meetings/meeting-card.tsx`
- `services/dashboard/src/app/meetings/page.tsx`
- `services/dashboard/tests/test_meeting_cards_ui.test.ts`

## 調査判断

外部仕様・最新ライブラリ・法規制には依存せず、リポジトリ内に保存済みのカレンダーメタデータを既存一覧へ接続する変更のため、`research-brief.md` と `option-matrix.md` は省略する。

## 完了条件

- 手動編集名がある場合は常に最優先で表示される。
- 手動編集名がなく、カレンダータイトルがある場合はそのタイトルが表示される。
- どちらもない場合は会議コードが表示される。
- カレンダータイトルで一覧検索できる。
- 対象テスト、型検査、GitNexus変更検査が通る。


## verification-contract.md

# Verification Contract: calendar-meeting-title

- size: S
- external consultation required: no
- external consultation provider: claude-fable-cli

## Required Commands
- `PYTHONPATH=services/meeting-api:libs/admin-models python3 -m pytest services/meeting-api/tests/test_meetings.py -q`
- `/Users/bonginkan-3-gouki/project/generic_tldv/services/dashboard/node_modules/.bin/vitest run tests/test_meeting_cards_ui.test.ts`
- `/Users/bonginkan-3-gouki/project/generic_tldv/services/dashboard/node_modules/.bin/tsc --noEmit`
- `node /Users/bonginkan-3-gouki/project/generic_tldv/.gitnexus/run.cjs detect-changes --repo /Users/bonginkan-3-gouki/project/generic_tldv --scope compare --base-ref main`

## Evidence Rule
- Evidence Manifest must have no missing_evidence entries.

```

### 2. Approaches Tried And Failure Reasons

```text
[none recorded for this consultation]
```

### 3. Current Hypothesis

[not specified]

### 4. Questions To Decide

1. この計画に表示回帰またはAPI互換性のMUST_FIXがあるか

## Decision Or Result Under Review

手動編集名、カレンダータイトル、会議コードの順で表示する

## Context Coverage

```json
complete for configured brief limits
```

## Extra Context

```text
[no extra source file provided]
```

## Current Git Status

```text
 M services/dashboard/src/app/meetings/page.tsx
 M services/dashboard/src/components/meetings/meeting-card.tsx
 M services/dashboard/src/types/vexa.ts
 M services/dashboard/tests/test_meeting_cards_ui.test.ts
 M services/meeting-api/meeting_api/meetings.py
 M services/meeting-api/tests/test_meetings.py
?? .pipeline/gates/calendar-meeting-title/
?? .pipeline/plans/calendar-meeting-title/

```

## Current Diff Stat

```text
 services/dashboard/src/app/meetings/page.tsx       |  5 +--
 .../src/components/meetings/meeting-card.tsx       | 15 ++------
 services/dashboard/src/types/vexa.ts               |  1 +
 .../dashboard/tests/test_meeting_cards_ui.test.ts  | 11 +++---
 services/meeting-api/meeting_api/meetings.py       | 43 +++++++++++++---------
 services/meeting-api/tests/test_meetings.py        | 11 ++++++
 6 files changed, 49 insertions(+), 37 deletions(-)

```

## Current Diff Excerpt

```diff
diff --git a/services/dashboard/src/app/meetings/page.tsx b/services/dashboard/src/app/meetings/page.tsx
index ba93e17..026aacb 100644
--- a/services/dashboard/src/app/meetings/page.tsx
+++ b/services/dashboard/src/app/meetings/page.tsx
@@ -259,10 +259,7 @@ export default function MeetingsPage() {
                   className="animate-fade-in-up"
                   style={{ animationDelay: `${Math.min(index, 10) * 30}ms`, animationFillMode: "backwards" }}
                 >
-                  <MeetingCard
-                    meeting={meeting}
-                    participantsTitleTemplate={copy.participantsMeetingTitle}
-                  />
+                  <MeetingCard meeting={meeting} />
                 </div>
               ))}
             </div>
diff --git a/services/dashboard/src/components/meetings/meeting-card.tsx b/services/dashboard/src/components/meetings/meeting-card.tsx
index 29cc1f4..6dd3212 100644
--- a/services/dashboard/src/components/meetings/meeting-card.tsx
+++ b/services/dashboard/src/components/meetings/meeting-card.tsx
@@ -20,7 +20,6 @@ import { withBasePath } from "@/lib/base-path";

 interface MeetingCardProps {
   meeting: Meeting;
-  participantsTitleTemplate?: string;
 }

 // Platform icons using actual icon files from public folder
@@ -76,18 +75,12 @@ function PlatformIcon({ platform, className }: { platform: string; className?: s
   return <ZoomIcon className={className} />;
 }

-export function MeetingCard({
-  meeting,
-  participantsTitleTemplate = "{names}との会議",
-}: MeetingCardProps) {
+export function MeetingCard({ meeting }: MeetingCardProps) {
   const statusConfig = getDetailedStatus(meeting.status, meeting.data);
   const updateMeetingData = useMeetingsStore((state) => state.updateMeetingData);
-  const participants = meeting.data?.participants || [];
   const rawTitle = meeting.data?.name || meeting.data?.title;
-  const participantsTitle = participants.length > 0
-    ? participantsTitleTemplate.replace("{names}", participants.join(", "))
-    : null;
-  const displayTitle = rawTitle || participantsTitle || meeting.platform_specific_id || "無題の会議";
+  const calendarTitle = meeting.data?.calendar_title;
+  const displayTitle = rawTitle || calendarTitle || meeting.platform_specific_id || "無題の会議";
   const timeSource = meeting.start_time || meeting.created_at;
   const isActive = meeting.status === "active";

@@ -189,7 +182,7 @@ export function MeetingCard({
   const handleStartEdit = (e: React.MouseEvent) => {
     e.preventDefault();
     e.stopPropagation();
-    setEditedTitle(rawTitle || participantsTitle || "");
+    setEditedTitle(rawTitle || calendarTitle || "");
     setIsEditingTitle(true);
   };

diff --git a/services/dashboard/src/types/vexa.ts b/services/dashboard/src/types/vexa.ts
index 5c0ed56..84c34bf 100644
--- a/services/dashboard/src/types/vexa.ts
+++ b/services/dashboard/src/types/vexa.ts
@@ -40,6 +40,7 @@ export interface StatusTransition {
 export interface MeetingData {
   name?: string;
   title?: string;
+  calendar_title?: string;
   notes?: string;
   participants?: string[];
   languages?: string[];
diff --git a/services/dashboard/tests/test_meeting_cards_ui.test.ts b/services/dashboard/tests/test_meeting_cards_ui.test.ts
index d748b36..4c35147 100644
--- a/services/dashboard/tests/test_meeting_cards_ui.test.ts
+++ b/services/dashboard/tests/test_meeting_cards_ui.test.ts
@@ -5,11 +5,12 @@ const pageSource = readFileSync("src/app/meetings/page.tsx", "utf8");
 const cardSource = readFileSync("src/components/meetings/meeting-card.tsx", "utf8");

 describe("会議一覧カード", () => {
-  it("参加者タイトルの後に会議コードを識別用fallbackとして使う", () => {
+  it("手動編集名、カレンダータイトル、会議コードの順で表示する", () => {
     expect(cardSource).toContain(
-      'rawTitle || participantsTitle || meeting.platform_specific_id || "無題の会議"',
+      'rawTitle || calendarTitle || meeting.platform_specific_id || "無題の会議"',
     );
-    expect(cardSource).toContain('setEditedTitle(rawTitle || participantsTitle || "")');
+    expect(cardSource).toContain('setEditedTitle(rawTitle || calendarTitle || "")');
+    expect(cardSource).not.toContain("participantsTitle");
   });

   it("開始前の会議でも作成日時を表示する", () => {
@@ -23,7 +24,7 @@ describe("会議一覧カード", () => {
     );
   });

-  it("参加者由来タイトルのローカライズ済みtemplateをカードへ渡す", () => {
-    expect(pageSource).toContain("participantsTitleTemplate={copy.participantsMeetingTitle}");
+  it("一覧ページからカードへ会議データだけを渡す", () => {
+    expect(pageSource).toContain("<MeetingCard meeting={meeting} />");
   });
 });
diff --git a/services/meeting-api/meeting_api/meetings.py b/services/meeting-api/meeting_api/meetings.py
index e4c1505..cd50fc5 100644
--- a/services/meeting-api/meeting_api/meetings.py
+++ b/services/meeting-api/meeting_api/meetings.py
@@ -1375,6 +1375,30 @@ async def delete_browser_storage(user_id: int):
         raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")


+def _meeting_list_data_summary(d: Optional[dict]) -> dict:
+    d = d or {}
+    participants = d.get("participants") or []
+    notes = d.get("notes")
+    transitions = d.get("status_transition") or []
+    calendar_event = d.get("calendar_event")
+    calendar_title = (
+        calendar_event.get("title")
+        if isinstance(calendar_event, dict)
+        else None
+    )
+    return {
+        "name": d.get("name") or d.get("title"),
+        "calendar_title": calendar_title,
+        "completion_reason": d.get("completion_reason"),
+        "participants": participants[:3],
+        "participants_count": len(participants),
+        "notes_preview": (notes[:120] if isinstance(notes, str) else None),
+        "languages": d.get("languages"),
+        "last_transition": transitions[-1] if transitions else None,
+        "has_recording": bool(d.get("recordings")),
+    }
+
+
 @router.get(
     "/bots",
     summary="List recent meetings/bots for the authenticated user",
@@ -1399,6 +1423,7 @@ async def list_user_bots(
             (Meeting.platform_specific_id.ilike(q))
             | (Meeting.data["name"].astext.ilike(q))
             | (Meeting.data["title"].astext.ilike(q))
+            | (Meeting.data["calendar_event"]["title"].astext.ilike(q))
         )
     if status:
         stmt = stmt.where(Meeting.status == status)
@@ -1425,22 +1450,6 @@ async def list_user_bots(
     # opt-in for callers that genuinely need it. Default off.
     include_full_data = include == "data"

-    def _data_summary(d: dict) -> dict:
-        d = d or {}
-        participants = d.get("participants") or []
-        notes = d.get("notes")
-        transitions = d.get("status_transition") or []
-        return {
-            "name": d.get("name") or d.get("title"),
-            "completion_reason": d.get("completion_reason"),
-            "participants": participants[:3],
-            "participants_count": len(participants),
-            "notes_preview": (notes[:120] if isinstance(notes, str) else None),
-            "languages": d.get("languages"),
-            "last_transition": transitions[-1] if transitions else None,
-            "has_recording": bool(d.get("recordings")),
-        }
-
     return {
         "meetings": [
             {
@@ -1451,7 +1460,7 @@ async def list_user_bots(
                 "bot_container_id": m.bot_container_id,
                 "start_time": m.start_time.isoformat() if m.start_time else None,
                 "end_time": m.end_time.isoformat() if m.end_time else None,
-                "data": (m.data or {}) if include_full_data else _data_summary(m.data),
+                "data": (m.data or {}) if include_full_data else _meeting_list_data_summary(m.data),
                 "created_at": m.created_at.isoformat() if m.created_at else None,
                 "updated_at": m.updated_at.isoformat() if m.updated_at else None,
             }
diff --git a/services/meeting-api/tests/test_meetings.py b/services/meeting-api/tests/test_meetings.py
index 0f5caaa..dc79013 100644
--- a/services/meeting-api/tests/test_meetings.py
+++ b/services/meeting-api/tests/test_meetings.py
@@ -13,6 +13,7 @@ from unittest.mock import AsyncMock, MagicMock, patch
 import pytest

 from meeting_api.schemas import MeetingStatus, MeetingResponse, BotStatusResponse, Platform, MeetingCreate
+from meeting_api.meetings import _meeting_list_data_summary

 from .conftest import (
     TEST_USER_ID,
@@ -64,6 +65,16 @@ def test_meeting_create_defaults_voice_agent_enabled_true():
     assert req.voice_agent_enabled is True


+def test_meeting_list_summary_keeps_manual_and_calendar_titles_separate():
+    summary = _meeting_list_data_summary({
+        "name": "手動で変更した名前",
+        "calendar_event": {"title": "週次定例"},
+    })
+
+    assert summary["name"] == "手動で変更した名前"
+    assert summary["calendar_title"] == "週次定例"
+
+
 class TestCreateMeeting:

     @pytest.mark.asyncio

```

## Required JSON Output

Return only JSON matching this shape:

```json
{
  "type": "object",
  "additionalProperties": true,
  "required": [
    "verdict",
    "summary",
    "findings",
    "confidence"
  ],
  "properties": {
    "verdict": {
      "type": "string",
      "enum": [
        "MUST_FIX",
        "SHOULD_FIX",
        "SHIP"
      ]
    },
    "summary": {
      "type": "string"
    },
    "confidence": {
      "type": "string",
      "enum": [
        "low",
        "medium",
        "high"
      ]
    },
    "findings": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": true,
        "required": [
          "id",
          "severity",
          "title",
          "evidence",
          "recommendation"
        ],
        "properties": {
          "id": {
            "type": "string"
          },
          "severity": {
            "type": "string",
            "enum": [
              "MUST_FIX",
              "SHOULD_FIX",
              "NOTE"
            ]
          },
          "title": {
            "type": "string"
          },
          "evidence": {
            "type": "string"
          },
          "recommendation": {
            "type": "string"
          }
        }
      }
    },
    "local_verification": {
      "type": "array",
      "items": {
        "type": "string"
      }
    }
  }
}
```

# Fable Consultation Brief: participant-roster

Generated: 2026-07-14T06:02:09.025858+00:00
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

Task id: participant-roster
Plan step or checkpoint: 無言参加者ロスター計画の整合確認

Relevant plan artifacts:

```text
## request.md

# 依頼

発言者だけでなく、会議中に実際に参加していた無言参加者も参加者情報として取得し、ダッシュボードへ反映する。



## plan.md

# 実装プラン: 無言参加者を含む参加者ロスター

## 目的

Google Meetで発言しなかった参加者も、ボットが会議中に観測した実参加者として保存し、既存のダッシュボード `participants` 表示と参加者由来タイトルへ反映する。

## 実装

1. Google Meetブラウザ内に累積ロスターを追加する。参加者パネルの行を優先し、参加者タイルをフォールバックとして定期走査する。
2. ロスターには参加者ID、表示名、初回・最終観測時刻、取得元を保持し、ボット自身とUI文言・ジャンク名を除外する。
3. 終了時に `participant_roster` を読み、既存の統一コールバックへ任意フィールドとして追加する。未指定の通常コールバックは変更しない。
4. meeting-apiで入力を上限付きで正規化し、`meeting.data.participant_roster` と、人名配列の `meeting.data.participants` を保存する。
5. 後処理ではロスター由来 `participants` を上書きせず、ロスターがない旧会議だけ従来どおり文字起こし話者から補完する。

## 対象外

- Calendar招待者を実参加者とみなすこと。
- Zoom・Teamsのロスター自動保存（今回はGoogle Meetを先行）。
- Google Meet DOM変更に対する100%保証。
- 既存会議データの一括バックフィル。

## リスク対策

- 共通コールバックはCRITICAL影響のため、末尾の任意引数・任意JSONフィールドとして後方互換にする。
- ロスター件数、文字列長、時刻値をmeeting-api側でも制限し、JSONB肥大化と不正入力を防ぐ。
- 実参加者名を取得できない場合は、従来の話者由来参加者へフォールバックする。

## 承認

ユーザーの「実装お願いします」を、上記方針への実装承認として扱う。



## verification-contract.md

# 検証契約: participant-roster

- size: M
- external consultation required: no

## 必須条件

1. 無言参加者を含むロスターが会議中の複数スナップショットから累積され、途中退席者が残る。
2. ボット自身、空名、Google Meetのジャンク名、重複参加者を保存しない。
3. 終了コールバックだけが任意の `participant_roster` を送り、未指定時の既存ペイロード契約を壊さない。
4. meeting-apiがロスターを正規化して保存し、`participants` を実参加者名で更新する。
5. ロスターがない旧会議では、従来の文字起こし話者フォールバックが維持される。

## Required Commands

- `cd services/vexa-bot/core && ../node_modules/.bin/tsx src/services/participant-roster.test.ts`
- `cd services/vexa-bot/core && ../node_modules/.bin/tsx src/services/unified-callback.test.ts`
- `cd services/vexa-bot/core && npm run build`
- `docker run --rm --user root -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/src/services/meeting-api:/src/libs/admin-models -v "$PWD:/src:ro" -w /src/services/meeting-api vexaai/meeting-api:latest sh -lc 'python -m pip install --disable-pip-version-check --no-cache-dir -q pytest pytest-asyncio && python -m pytest -p no:cacheprovider tests/test_participant_roster.py tests/test_callbacks.py tests/test_post_meeting_idempotency.py tests/test_meetings.py -q'`
- `docker run --rm --user root -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/src/services/meeting-api:/src/libs/admin-models -v "$PWD:/src:ro" -w /src/services/meeting-api vexaai/meeting-api:latest sh -lc 'python -m pip install --disable-pip-version-check --no-cache-dir -q pytest pytest-asyncio && python -m pytest -p no:cacheprovider tests -q --ignore=tests/integration/test_transcription_dictionary_postgres.py'`
- `cd services/dashboard && npm test -- tests/test_meeting_cards_ui.test.ts tests/test_export_and_bot_defaults.test.ts`
- `node /Users/bonginkan-3-gouki/project/generic_tldv/.gitnexus/run.cjs detect-changes --repo generic_tldv-participant-roster --scope compare --base-ref main`
- `bash .claude/hooks/pr-ready-gate.sh participant-roster`
- `bash scripts/harness/outcome-judge.sh participant-roster`

## Evidence Rule

- `.pipeline/evidence/participant-roster/` にコマンド結果と変更範囲を保存する。
- Evidence Manifestに `missing_evidence` がないこと。


## option-matrix.md

# 選択肢比較: 参加者ロスター取得

| 案 | 無言参加者 | 途中退席者 | 大人数 | 変更量 | 判定 |
|---|---:|---:|---:|---:|---|
| 既存の話者一覧を継続 | 不可 | 発言時のみ | 発言時のみ | なし | 不採用 |
| 終了時に表示タイルを一度だけ取得 | 一部可 | 不可 | 弱い | 小 | 不採用 |
| 会議中に参加者パネル＋タイルを定期走査し累積 | 可 | 可 | 比較的強い | 中 | 採用 |
| Google Calendar招待者を参加者扱い | 招待者のみ | 実参加を判別不可 | 強い | 小 | 別データとして保持 |

## 採用理由

「招待された人」「実際に参加した人」「発言した人」を混同せず、実参加者を最も正確に残せるため。参加者パネル取得に失敗した場合は表示タイル、ロスター自体がない旧会議では既存の発言者集約へ段階的にフォールバックする。



## research-brief.md

# 調査概要: 無言参加者を含む参加者ロスター

## 問い

1. 現在の `participants` はどこから生成されるか。
2. Vexaボットが無言参加者を観測できるか。
3. 途中退席者や大人数会議を落とさず保存する最小構成は何か。

## 事前仮説

- 仮説A: 現在の参加者は文字起こしの話者から生成され、無言参加者は保存されない。
- 仮説B: Google Meetボットは参加者DOMを読めるが、話者推定以外へ伝搬していない。
- 反証条件: meeting-apiに別の自動ロスター保存経路、またはVexa公開APIに全参加者イベントが存在すること。

## 確認結果

- `post_meeting.aggregate_transcription()` は文字起こしセグメントの `speaker` を重複排除し、`participants` 未設定時だけ保存している。仮説Aを支持する。
- Google Meetの `recording.ts` は参加者タイルからID・表示名を取得し、`__vexaGetAllParticipantNames()` として公開している。ただし終了コールバックは `speaker_events` しか送っていない。仮説Bを支持する。
- 現行のタイル列挙は表示中DOMに依存するため、大人数会議では仮想化・ページングで漏れる。参加者パネルを優先し、タイルをフォールバックにする必要がある。
- 2026-07-14に[Vexa公式WebSocket仕様](https://docs.vexa.ai/websocket)と[Vexa公式リポジトリ](https://github.com/Vexa-ai/vexa)を確認した。公開WebSocket契約は文字起こし・会議状態・インタラクティブ操作を扱うが、参加者ロスターイベントは定義していない。既存の公開API呼び出しだけでは完結しない。

## 信頼度と覆る条件

- 信頼度: 高（現行ソースと公式仕様を照合済み）。
- 覆る条件: Google MeetのDOM構造が変更され参加者パネルを取得できない、または上流Vexaに安定したロスターAPIが追加された場合。

```

### 2. Approaches Tried And Failure Reasons

```text
[none recorded for this consultation]
```

### 3. Current Hypothesis

[not specified]

### 4. Questions To Decide

1. Does this plan faithfully capture the user's intent and unstated constraints?
2. What important alternative or edge case is missing before implementation starts?
3. Is the verification contract strong enough to prove the intended outcome?

## Decision Or Result Under Review

People panelとタイルを累積し、任意callbackでJSONBへ保存する

## Extra Context

```text
[no extra source file provided]
```

## Current Git Status

```text
 M .pipeline/current/task-id
 M docs/api/meetings.mdx
 M docs/speaker-identification.mdx
 M services/meeting-api/meeting_api/callbacks.py
 M services/meeting-api/meeting_api/collector/endpoints.py
 M services/meeting-api/meeting_api/post_meeting.py
 M services/meeting-api/tests/test_callbacks.py
 M services/meeting-api/tests/test_meetings.py
 M services/vexa-bot/core/package.json
 M services/vexa-bot/core/src/index.ts
 M services/vexa-bot/core/src/platforms/googlemeet/recording.ts
 M services/vexa-bot/core/src/platforms/googlemeet/selectors.ts
 M services/vexa-bot/core/src/services/unified-callback.test.ts
 M services/vexa-bot/core/src/services/unified-callback.ts
 M services/vexa-bot/docs/speaker-name-resolution.md
?? .pipeline/evidence/participant-roster/
?? .pipeline/plans/participant-roster/
?? services/meeting-api/meeting_api/participant_roster.py
?? services/meeting-api/tests/test_participant_roster.py
?? services/vexa-bot/core/src/services/participant-roster.test.ts
?? services/vexa-bot/core/src/services/participant-roster.ts

```

## Current Diff Stat

```text
 docs/api/meetings.mdx                              |  32 ++++
 docs/speaker-identification.mdx                    |   9 +
 services/meeting-api/meeting_api/callbacks.py      |  10 +-
 .../meeting-api/meeting_api/collector/endpoints.py |   5 +
 services/meeting-api/meeting_api/post_meeting.py   |  12 ++
 services/meeting-api/tests/test_callbacks.py       | 127 +++++++++++++
 services/meeting-api/tests/test_meetings.py        |  33 ++++
 services/vexa-bot/core/package.json                |   2 +-
 services/vexa-bot/core/src/index.ts                |  33 +++-
 .../core/src/platforms/googlemeet/recording.ts     | 204 ++++++++++++++++++---
 .../core/src/platforms/googlemeet/selectors.ts     |  12 +-
 .../core/src/services/unified-callback.test.ts     |  43 +++++
 .../vexa-bot/core/src/services/unified-callback.ts |  11 +-
 services/vexa-bot/docs/speaker-name-resolution.md  |   4 +-
 14 files changed, 503 insertions(+), 34 deletions(-)

```

## Current Diff Excerpt

```diff
diff --git a/docs/api/meetings.mdx b/docs/api/meetings.mdx
index 249f652..2155932 100644
--- a/docs/api/meetings.mdx
+++ b/docs/api/meetings.mdx
@@ -3,6 +3,36 @@ title: "Meetings API"
 ---
 Meetings are created/updated as bots run. You can list history, attach metadata (notes), and delete/anonymize artifacts.

+## 参加者データ（Google Meet）
+
+Google Meetでは、文字起こしの話者とは別に、ボットが会議中に観測した参加者ロスターを`data`へ保存します。これにより、発言しなかった参加者や、会議終了前に退出した参加者もダッシュボードの参加者一覧へ反映できます。
+
+| フィールド | 意味 |
+|---|---|
+| `participants` | ダッシュボード、会議タイトル、エクスポートで使う表示名の一覧 |
+| `observed_participants` | ボットが実際に観測した表示名の一覧。`participants`を手動編集しても観測結果はここに残る |
+| `participant_roster` | 表示名、初回・最終観測時刻、取得元を含む監査用ロスター |
+| `participants_source` | `participant_roster`、`transcript_speakers`、`manual`のいずれか |
+
+```json
+{
+  "participants": ["Alice", "Silent Bob"],
+  "observed_participants": ["Alice", "Silent Bob"],
+  "participants_source": "participant_roster",
+  "participant_roster": [
+    {
+      "participant_id": "participant:2a7ddf11e34b9b92",
+      "participant_name": "Silent Bob",
+      "first_seen_at_ms": 1784000000000,
+      "last_seen_at_ms": 1784000300000,
+      "source": "people_panel"
+    }
+  ]
+}
+```
+
+`participant_id`は会議単位で不透明化したIDで、Google Meet内部のIDは保存しません。ロスターは最大250件です。Google Meetの画面構造に依存するベストエフォート取得であり、同じ表示名の参加者は1名として統合されます。ロスターを取得できない旧ボットや他プラットフォームでは、従来どおり文字起こしの話者から`participants`を補完します。
+
 ## GET /meetings

 List meetings for the authenticated user.
@@ -115,6 +145,8 @@ curl -X PATCH "$API_BASE/meetings/zoom/12345678901" \

 Returns the updated meeting record.

+`participants`をPATCHした場合は手動指定が優先され、以後の自動ロスター更新では上書きされません。ボットが観測した値は`observed_participants`で確認できます。
+
 <Accordion title="Response (200)">

 ```json
diff --git a/docs/speaker-identification.mdx b/docs/speaker-identification.mdx
index 3e75fa6..ac77a71 100644
--- a/docs/speaker-identification.mdx
+++ b/docs/speaker-identification.mdx
@@ -5,6 +5,15 @@ description: "How Vexa identifies who said what in meetings"

 Vexa attributes transcript segments to individual speakers using platform-specific detection. This page explains how it works and what to expect.

+## 「話者」と「参加者」は別データ
+
+- **話者**は、音声と発話インジケーターを対応付けた「誰が話したか」の情報です。
+- **参加者ロスター**は、Google Meetの参加者パネルとタイルから会議中に観測した「誰が参加していたか」の情報です。
+
+無言の参加者は文字起こしの`speaker`には現れませんが、参加者ロスターへは保存され、会議終了後の`meeting.data.participants`へ反映されます。途中退出者も、会議中の累積観測に残ります。
+
+現在の参加者ロスター自動取得はGoogle Meetのみです。Google MeetのDOMに依存するためベストエフォートであり、大人数会議の仮想スクロールや同一表示名の参加者を完全には区別できません。ロスターを取得できない場合は、従来どおり文字起こし話者を参加者候補として使います。
+
 ## How It Works

 Vexa uses **DOM-based speaker correlation** — not audio-based diarization. The bot observes the meeting platform's UI to detect who is currently speaking, then correlates that with the audio transcription stream.
diff --git a/services/meeting-api/meeting_api/callbacks.py b/services/meeting-api/meeting_api/callbacks.py
index 89899cf..81d3156 100644
--- a/services/meeting-api/meeting_api/callbacks.py
+++ b/services/meeting-api/meeting_api/callbacks.py
@@ -34,6 +34,7 @@ from .meetings import (
 )
 from .post_meeting import run_all_tasks
 from .recording_finalizer import finalize_recording_master
+from .participant_roster import merge_participant_roster_data
 from .collector.auth import require_internal_secret

 logger = logging.getLogger("meeting_api.callbacks")
@@ -283,6 +284,7 @@ class BotStatusChangePayload(BaseModel):
     failure_stage: Optional[MeetingFailureStage] = Field(None)
     timestamp: Optional[str] = Field(None)
     speaker_events: Optional[List[Dict]] = Field(None)
+    participant_roster: Optional[List[Dict[str, Any]]] = Field(None)
     # v0.10.5.3 Pack O — last N structured-JSON log lines from bot stdout.
     # Sent only on terminal status (failed/completed). Persisted into
     # meetings.data.bot_logs JSONB after a 50 KB cap (apply at write-time
@@ -742,7 +744,7 @@ async def bot_status_change_callback(

     await db.refresh(meeting)

-    # v0.10.5.3 Pack O + Pack T: persist forensic fields on terminal transitions.
+    # Persist terminal-only bot artifacts before status-specific branches.
     # - bot_logs: last ~200 structured-JSON log lines from bot stdout (ring
     #   buffer via Pack O). Capped at 50 KB to bound JSONB row size.
     # - bot_resources: cgroup memory + CPU summary from Pack T's sampler.
@@ -752,7 +754,9 @@ async def bot_status_change_callback(
     # them. Future operators querying a failed meeting now have the bot's
     # last log lines + memory peak in the meeting row.
     if new_status in (MeetingStatus.FAILED, MeetingStatus.COMPLETED) and (
-        payload.bot_logs or payload.bot_resources
+        payload.bot_logs
+        or payload.bot_resources
+        or payload.participant_roster is not None
     ):
         if not meeting.data:
             meeting.data = {}
@@ -773,6 +777,8 @@ async def bot_status_change_callback(
             d["bot_logs_truncated"] = len(kept) < len(payload.bot_logs)
         if payload.bot_resources:
             d["bot_resources"] = payload.bot_resources
+        if payload.participant_roster is not None:
+            d = merge_participant_roster_data(d, payload.participant_roster)
         meeting.data = d
         attributes.flag_modified(meeting, "data")
         # Don't commit here — leaves it to the branch logic below to commit
diff --git a/services/meeting-api/meeting_api/collector/endpoints.py b/services/meeting-api/meeting_api/collector/endpoints.py
index 5d6e215..3ec6183 100644
--- a/services/meeting-api/meeting_api/collector/endpoints.py
+++ b/services/meeting-api/meeting_api/collector/endpoints.py
@@ -761,6 +761,11 @@ async def update_meeting_data(
     for key, value in update_data.items():
         if key in allowed_fields and value is not None:
             new_data[key] = value
+            if key == "participants":
+                # A user-authenticated PATCH is authoritative. Preserve it when
+                # later bot callbacks or post-meeting retries refresh the
+                # observed roster.
+                new_data["participants_source"] = "manual"
             updated_fields.append(f"{key}={value}")

     # Assign the new dict to ensure SQLAlchemy detects the change
diff --git a/services/meeting-api/meeting_api/post_meeting.py b/services/meeting-api/meeting_api/post_meeting.py
index 116c6aa..fb69703 100644
--- a/services/meeting-api/meeting_api/post_meeting.py
+++ b/services/meeting-api/meeting_api/post_meeting.py
@@ -20,6 +20,7 @@ from .webhook_delivery import deliver_with_result, build_envelope

 from .config import TRANSCRIPTION_COLLECTOR_URL, POST_MEETING_HOOKS
 from .webhooks import send_completion_webhook
+from .participant_roster import merge_participant_roster_data

 logger = logging.getLogger("meeting_api.post_meeting")

@@ -129,6 +130,16 @@ async def aggregate_transcription(meeting: Meeting, db: AsyncSession):
             return False

         segments = response.json()
+        existing_data = meeting.data or {}
+        projected_data = merge_participant_roster_data(
+            existing_data,
+            existing_data.get("participant_roster") if isinstance(existing_data, dict) else [],
+        )
+        roster_projected = projected_data != existing_data
+        if roster_projected:
+            meeting.data = projected_data
+            from sqlalchemy.orm.attributes import flag_modified
+            flag_modified(meeting, "data")
         if not segments:
             # Empty result is legitimate (zero-segment meeting); clear any
             # prior failure_class to indicate aggregation completed cleanly.
@@ -150,6 +161,7 @@ async def aggregate_transcription(meeting: Meeting, db: AsyncSession):
         changed = False
         if "participants" not in existing_data and unique_speakers:
             existing_data["participants"] = sorted(unique_speakers)
+            existing_data["participants_source"] = "transcript_speakers"
             changed = True
         if "languages" not in existing_data and unique_languages:
             existing_data["languages"] = sorted(unique_languages)
diff --git a/services/meeting-api/tests/test_callbacks.py b/services/meeting-api/tests/test_callbacks.py
index f76cb78..078ffbd 100644
--- a/services/meeting-api/tests/test_callbacks.py
+++ b/services/meeting-api/tests/test_callbacks.py
@@ -477,6 +477,133 @@ class TestAwaitingAdmissionCallback:

 class TestStatusChangeCallback:

+    @pytest.mark.asyncio
+    async def test_completed_status_persists_observed_participant_roster(
+        self, client, mock_db, mock_redis,
+    ):
+        """Silent participants are stored independently from speaker events."""
+        meeting = make_meeting(status=MeetingStatus.ACTIVE.value, data={})
+        roster = [
+            {
+                "participant_id": "participant:1111111111111111",
+                "participant_name": "Alice",
+                "first_seen_at_ms": 1000,
+                "last_seen_at_ms": 5000,
+                "source": "people_panel",
+            },
+            {
+                "participant_id": "participant:2222222222222222",
+                "participant_name": "Silent Bob",
+                "first_seen_at_ms": 1200,
+                "last_seen_at_ms": 4000,
+                "source": "people_panel",
+            },
+        ]
+
+        with _patch_find_meeting(meeting), _patch_flag_modified(), \
+             patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True), \
+             patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock), \
+             patch("meeting_api.callbacks.schedule_status_webhook_task", new_callable=AsyncMock), \
+             patch("meeting_api.callbacks.run_all_tasks", new_callable=AsyncMock):
+            resp = await client.post("/bots/internal/callback/status_change", json={
+                "connection_id": TEST_SESSION_UID,
+                "status": "completed",
+                "participant_roster": roster,
+            })

[truncated]
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

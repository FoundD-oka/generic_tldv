# Verification Contract — poc-manual-join-title-discord

前提: すべて commit 済み clean tree で実行。テスト削除・skip・期待値緩和は禁止。
`<base>` は `base-commit` の値。`.hw/plans/poc-manual-join-title-discord/**` はハーネス成果物として差分許容。

## Acceptance Tests

| ID | Requirement | Evidence |
|---|---|---|
| AT-001 | UIでタイトルをtrimし、空/空白のみはrequestに`meeting_title`キーを含めない | dashboard単体テスト + JoinModalソース検査 |
| AT-002 | UIで200文字可、201文字拒否、InputにmaxLengthがある | dashboard境界値テスト |
| AT-003 | `CreateBotRequest.meeting_title?: string` の任意追加のみ | 型diff + typecheck |
| AT-004 | APIはstrip、空/省略→None、200文字可、201文字と非文字列拒否 | schema pytest |
| AT-005 | request_botは指定時だけ`meeting.data.meeting_title={title,source:"manual_join"}`を保存 | meeting pytest |
| AT-006 | Driveタイトル優先順位はcalendar > manual_join > fallback | drive pytest |
| AT-007 | filename・Markdown見出し・hook payload titleがcalendar/manual各ケースで一致 | drive pytest |
| AT-008 | 不正manual entryは無視され、タイトル無しの既存fallbackはDrive内だけ維持 | drive pytest |
| AT-009 | DB calendar source/titleとpayload title一致時のみcalendar経路がposted | calendar-service pytest |
| AT-010 | DB manual_join titleとpayload title一致時にpostedし、通知記録にtitle_sourceを残す | manual投稿pytest |
| AT-011 | title欠落/空/DB信頼タイトル無し/calendar不一致/manual不一致/meeting ID fallbackはskip | 拒否pytest |
| AT-012 | payload calendar_eventは判定に使わず、DBに無ければskip | 偽装拒否pytest + grep |
| AT-013 | DBにcalendarとmanualが両方ある場合はcalendar優先 | 優先順位pytest |
| AT-014 | manual entryの非dict/source欠落/source違いは拒否 | 不正形pytest |
| AT-015 | skip時はHTTP client/guild/model/post/DB commitが全て0、discord_notify未作成 | 各拒否pytest |
| AT-016 | 判定順序はconfig→meeting→duplicate→title gateを維持 | 既存順序pytest |
| AT-017 | 許可後の閾値0.85/default fallback/403・404再投稿/配送失敗挙動は不変 | 既存投稿・fallback pytest |
| AT-018 | Zoom OAuth pending request経路は無改変で任意fieldを保持 | 対象2ファイルdiffなし |

## Failure Patterns

| ID | Must Not Regress | Evidence |
|---|---|---|
| FP-001 | 既存テスト関数削除なし | base/HEAD関数集合比較 |
| FP-002 | assert削減なし | base/HEAD件数比較 |
| FP-003 | skip/xfail/only新規追加なし | diff grep |
| FP-004 | 会議ID/fallbackタイトルがDiscord・Groqへ渡る経路なし | discord_notify.py grep + 拒否test |
| FP-005 | Drive出力はcalendar-serviceへ依存しない | meeting-api単体test |
| FP-006 | `.env*`, `deploy/**`, migrations, alembic, token設定変更なし | name-only diff |
| FP-007 | `join-form.tsx`, `use-meeting-actions.tsx`, `use-pending-meeting.ts`無改変 | diff |
| FP-008 | `meeting.data["title"]`/`["name"]`へ書かない | diff grep |
| FP-009 | 既存skip reasonとstatus/reason形状不変 | 既存test + grep |
| FP-010 | calendar-service受信route/HMAC挙動不変 | main.py diffなし + route test |

## Non-Functional Checks

| ID | Requirement | Command / Evidence |
|---|---|---|
| NFT-001 | 変更はplan記載許可集合と当該plan directoryのみ。依存追加なし | `git diff --name-only <base>..HEAD` |
| NFT-002 | dashboard typecheck + full tests | `cd services/dashboard && npx tsc --noEmit && npm test` |
| NFT-003 | meeting-api focused + full | focused pytest後に`pytest tests -q` |
| NFT-004 | calendar-service focused + full | focused pytest後に`PYTHONPATH=. pytest tests -q` |
| NFT-005 | hw機械検証 | `bash .hw/verify.sh`がknown baselineのみでok |
| NFT-006 | 編集前impactとcommit前detect_changesの範囲が計画内 | GitNexus出力 |
| NFT-007 | Fable契約レビュー | `python3 .hw/fable_review.py poc-manual-join-title-discord`がREADY |
| NFT-008 | PRゲート | `bash .hw/hooks/pr-ready-gate.sh poc-manual-join-title-discord` exit 0 |
| NFT-009 | 日英copyが既存スタイルに適合 | typecheck + 目視 |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes
- hash-bound approval required: yes
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no


import { describe, expect, it } from "vitest";
import {
  buildJoinBotRequest,
  extractFastApi422Messages,
} from "@/components/join/join-modal-helpers";
import {
  applyBotCreationDefaults,
  withPostMeetingAutoStop,
} from "@/lib/bot-create-defaults";
import type { CreateBotRequest } from "@/types/vexa";

/** Same composition the JoinModal submit path performs. */
function composeJoinRequest(meetingId: string) {
  return applyBotCreationDefaults(
    withPostMeetingAutoStop(
      buildJoinBotRequest({
        platform: "zoom",
        meetingId,
        wakeWordEnabled: true,
      }) as CreateBotRequest
    )
  );
}

describe("buildJoinBotRequest", () => {
  it("omits native_meeting_id entirely when the parser found no ID", () => {
    const request = composeJoinRequest("");

    expect("native_meeting_id" in request).toBe(false);
    expect(JSON.parse(JSON.stringify(request))).not.toHaveProperty("native_meeting_id");
    expect(request.platform).toBe("zoom");
    expect(request.voice_agent_enabled).toBe(true);
  });

  it("omits native_meeting_id for a whitespace-only ID", () => {
    const request = composeJoinRequest("   ");

    expect("native_meeting_id" in request).toBe(false);
  });

  it("keeps a trimmed native_meeting_id when the parser found one", () => {
    const request = composeJoinRequest("  96088138284  ");

    expect("native_meeting_id" in request).toBe(true);
    expect(request.native_meeting_id).toBe("96088138284");
    expect(JSON.parse(JSON.stringify(request)).native_meeting_id).toBe("96088138284");
  });

  it("passes the wake word toggle through to voice_agent_enabled", () => {
    const request = buildJoinBotRequest({
      platform: "google_meet",
      meetingId: "abc-defg-hij",
      wakeWordEnabled: false,
    });

    expect(request.voice_agent_enabled).toBe(false);
  });
});

describe("extractFastApi422Messages", () => {
  it("extracts msg strings from a FastAPI detail array", () => {
    const messages = extractFastApi422Messages({
      detail: [
        {
          loc: ["body", "native_meeting_id"],
          msg: "Value error, native_meeting_id cannot be empty",
          type: "value_error",
        },
        { loc: ["body", "passcode"], msg: "Passcode is required for Teams meetings." },
      ],
    });

    expect(messages).toEqual([
      "Value error, native_meeting_id cannot be empty",
      "Passcode is required for Teams meetings.",
    ]);
  });

  it("ignores blank msg entries", () => {
    expect(
      extractFastApi422Messages({ detail: [{ msg: "  " }, { msg: " real problem " }] })
    ).toEqual(["real problem"]);
  });

  it("returns null for malformed details", () => {
    expect(extractFastApi422Messages(null)).toBeNull();
    expect(extractFastApi422Messages(undefined)).toBeNull();
    expect(extractFastApi422Messages("Unprocessable Entity")).toBeNull();
    expect(extractFastApi422Messages({})).toBeNull();
    expect(extractFastApi422Messages({ detail: "Unprocessable Entity" })).toBeNull();
    expect(extractFastApi422Messages({ detail: [] })).toBeNull();
    expect(extractFastApi422Messages({ detail: [{ msg: "" }] })).toBeNull();
    expect(extractFastApi422Messages({ detail: [{ loc: ["body"] }] })).toBeNull();
    expect(extractFastApi422Messages({ detail: [{ msg: null }] })).toBeNull();
    expect(extractFastApi422Messages({ detail: [{ msg: 42 }] })).toBeNull();
    expect(extractFastApi422Messages({ detail: ["cannot be empty"] })).toBeNull();
    expect(extractFastApi422Messages({ detail: [null] })).toBeNull();
  });
});

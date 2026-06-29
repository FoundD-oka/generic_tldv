import { afterEach, describe, expect, it, vi } from "vitest";
import { vexaAPI } from "@/lib/api";

describe("vexaAPI.deleteMeeting", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("passes meeting_id when deleting a specific meeting attempt", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 200,
        ok: true,
        text: async () => "",
      })
    );

    await vexaAPI.deleteMeeting("google_meet", "abc-defg-hij", "77");

    expect(fetch).toHaveBeenCalledWith("/api/vexa/meetings/google_meet/abc-defg-hij?meeting_id=77", {
      method: "DELETE",
    });
  });
});

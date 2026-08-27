import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VexaAPIError, vexaAPI } from "@/lib/api";
import { useMeetingsStore } from "@/stores/meetings-store";
import type { Meeting } from "@/types/vexa";

function meeting(id: string): Meeting {
  return {
    id,
    platform: "google_meet",
    platform_specific_id: `abc-defg-${id}`,
    status: "completed",
    start_time: null,
    end_time: null,
    bot_container_id: null,
    data: {},
    created_at: "2026-07-16T00:00:00Z",
  };
}

describe("一覧取得の失敗時に既存の会議を保持する", () => {
  beforeEach(() => {
    useMeetingsStore.setState({
      meetings: [meeting("1"), meeting("2")],
      error: null,
      errorRetryable: false,
      isLoadingMeetings: false,
      _offset: 0,
      _filters: {},
    });
  });

  afterEach(() => vi.restoreAllMocks());

  it("502 は meetings を保持したまま errorRetryable=true", async () => {
    vi.spyOn(vexaAPI, "getMeetings").mockRejectedValue(
      new VexaAPIError("Bad gateway", 502)
    );

    await useMeetingsStore.getState().fetchMeetings();

    const state = useMeetingsStore.getState();
    expect(state.meetings).toHaveLength(2);
    expect(state.error).toBe("Bad gateway");
    expect(state.errorRetryable).toBe(true);
    expect(state.isLoadingMeetings).toBe(false);
  });

  it("404 は errorRetryable=false", async () => {
    vi.spyOn(vexaAPI, "getMeetings").mockRejectedValue(
      new VexaAPIError("Not Found", 404)
    );

    await useMeetingsStore.getState().fetchMeetings();

    const state = useMeetingsStore.getState();
    expect(state.meetings).toHaveLength(2);
    expect(state.error).toBe("Not Found");
    expect(state.errorRetryable).toBe(false);
  });

  it("silent 失敗では error も meetings も変えない", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.spyOn(vexaAPI, "getMeetings").mockRejectedValue(
      new VexaAPIError("Bad gateway", 502)
    );

    await useMeetingsStore.getState().fetchMeetings(undefined, { silent: true });

    const state = useMeetingsStore.getState();
    expect(state.meetings).toHaveLength(2);
    expect(state.error).toBeNull();
    expect(state.errorRetryable).toBe(false);
  });

  it("clearError は errorRetryable も戻す", () => {
    useMeetingsStore.setState({ error: "Bad gateway", errorRetryable: true });

    useMeetingsStore.getState().clearError();

    expect(useMeetingsStore.getState().error).toBeNull();
    expect(useMeetingsStore.getState().errorRetryable).toBe(false);
  });
});

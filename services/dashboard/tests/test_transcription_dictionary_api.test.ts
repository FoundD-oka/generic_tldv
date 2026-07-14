import { afterEach, describe, expect, it, vi } from "vitest";
import { vexaAPI } from "@/lib/api";

describe("transcription dictionary API", () => {
  afterEach(() => vi.restoreAllMocks());

  it("creates a term with lexical fields only", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({ id: 1, term: "Bonginkan", reading: "ボンギンカン", enabled: true }),
    }));
    await vexaAPI.createTranscriptionDictionaryTerm({ term: "Bonginkan", reading: "ボンギンカン" });
    expect(fetch).toHaveBeenCalledWith("/api/vexa/transcription-dictionary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ term: "Bonginkan", reading: "ボンギンカン" }),
    });
  });

  it("updates enabled and hard-deletes", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ id: 2, enabled: false }) })
      .mockResolvedValueOnce({ ok: true, status: 204 });
    vi.stubGlobal("fetch", fetchMock);
    await vexaAPI.updateTranscriptionDictionaryTerm(2, { enabled: false });
    await vexaAPI.deleteTranscriptionDictionaryTerm(2);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/vexa/transcription-dictionary/2");
    expect(fetchMock.mock.calls[1]).toEqual(["/api/vexa/transcription-dictionary/2", { method: "DELETE" }]);
  });
});

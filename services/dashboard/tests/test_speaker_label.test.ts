import { describe, expect, it } from "vitest";
import {
  buildSpeakerDisplayLabels,
  getSpeakerDisplayLabel,
  getSpeakerIdentityKey,
  isNeedsReviewSegment,
  isUnlabeledSoloLaneSegment,
  resolveSpeakerLabelByKey,
} from "@/lib/speaker-label";

describe("getSpeakerIdentityKey", () => {
  it("prefers speaker over speaker_cluster", () => {
    expect(getSpeakerIdentityKey({ speaker: "田中", speaker_cluster: "lane:abc:1" })).toBe("田中");
  });

  it("falls back to speaker_cluster when speaker is empty", () => {
    expect(getSpeakerIdentityKey({ speaker: "", speaker_cluster: "lane:abc:1" })).toBe("lane:abc:1");
  });

  it("falls back to empty string when neither is set", () => {
    expect(getSpeakerIdentityKey({ speaker: "" })).toBe("");
  });
});

describe("isNeedsReviewSegment", () => {
  it("trusts speaker_mapping_status when present", () => {
    expect(
      isNeedsReviewSegment({ speaker: "", speaker_cluster: "1", speaker_mapping_status: "needs_review" })
    ).toBe(true);
  });

  it("infers needs_review from an unnamed lane sub-cluster id", () => {
    expect(isNeedsReviewSegment({ speaker: "", speaker_cluster: "lane:abc123:1" })).toBe(true);
  });

  it("does not flag a solo lane label (no sub-cluster suffix)", () => {
    expect(isNeedsReviewSegment({ speaker: "山森", speaker_cluster: "lane:abc123" })).toBe(false);
    expect(isNeedsReviewSegment({ speaker: "", speaker_cluster: "lane:abc123" })).toBe(false);
  });

  it("does not flag a named segment even if the cluster looks like a sub-cluster", () => {
    expect(isNeedsReviewSegment({ speaker: "田中", speaker_cluster: "lane:abc123:1" })).toBe(false);
  });

  it("does not flag legacy clusterless/unclustered segments", () => {
    expect(isNeedsReviewSegment({ speaker: "" })).toBe(false);
    expect(isNeedsReviewSegment({ speaker: "", speaker_cluster: "3" })).toBe(false);
  });
});

describe("buildSpeakerDisplayLabels / getSpeakerDisplayLabel", () => {
  it("assigns distinct 要確認の話者A/B labels to two unnamed sub-clusters by first appearance", () => {
    const segments = [
      { speaker: "", speaker_cluster: "lane:abc:1", speaker_mapping_status: "needs_review" },
      { speaker: "", speaker_cluster: "lane:abc:2", speaker_mapping_status: "needs_review" },
      { speaker: "", speaker_cluster: "lane:abc:1", speaker_mapping_status: "needs_review" },
    ];
    const labels = buildSpeakerDisplayLabels(segments);

    expect(labels.get("lane:abc:1")).toBe("要確認の話者A");
    expect(labels.get("lane:abc:2")).toBe("要確認の話者B");

    // Distinct identities never collapse into the same label.
    expect(getSpeakerDisplayLabel(segments[0], labels)).not.toBe(getSpeakerDisplayLabel(segments[1], labels));
  });

  it("never renders the raw lane cluster id as a label", () => {
    const segments = [{ speaker: "", speaker_cluster: "lane:xyz987:2", speaker_mapping_status: "needs_review" }];
    const labels = buildSpeakerDisplayLabels(segments);
    const label = getSpeakerDisplayLabel(segments[0], labels);

    expect(label).not.toContain("lane:");
    expect(label).toBe("要確認の話者A");
  });

  it("leaves a named segment's label unchanged", () => {
    const segments = [
      { speaker: "田中", speaker_cluster: "lane:abc:1" },
      { speaker: "", speaker_cluster: "lane:abc:2", speaker_mapping_status: "needs_review" },
    ];
    const labels = buildSpeakerDisplayLabels(segments);

    expect(getSpeakerDisplayLabel(segments[0], labels)).toBe("田中");
    expect(getSpeakerDisplayLabel(segments[1], labels)).toBe("要確認の話者A");
  });

  it("falls back to empty string for legacy unnamed/unclustered segments (no regression)", () => {
    const segments = [{ speaker: "" }];
    const labels = buildSpeakerDisplayLabels(segments);
    expect(getSpeakerDisplayLabel(segments[0], labels)).toBe("");
  });
});

// F2 (Fable final-audit consultation): a solo lane with no lane_label and
// no DOM-voted name stays in the raw `lane:{key}` shape (one colon, not a
// sub-cluster) with speaker="" and no speaker_mapping_status — it is NOT
// flagged needs_review, so it used to fall through buildSpeakerDisplayLabels
// unregistered, and the filter dropdown/badge in transcript-viewer.tsx
// rendered the raw cluster id as a literal fallback. These tests lock in
// the fix: the helper now covers this shape, and the identity-key-only
// resolver used by the filter dropdown/badge never leaks a raw id either.
describe("isUnlabeledSoloLaneSegment", () => {
  it("flags an unnamed solo lane (no sub-cluster suffix, no speaker)", () => {
    expect(isUnlabeledSoloLaneSegment({ speaker: "", speaker_cluster: "lane:abc123" })).toBe(true);
  });

  it("does not flag a named solo lane", () => {
    expect(isUnlabeledSoloLaneSegment({ speaker: "山森", speaker_cluster: "lane:abc123" })).toBe(false);
  });

  it("does not flag a sub-cluster shape (that's isNeedsReviewSegment's job)", () => {
    expect(isUnlabeledSoloLaneSegment({ speaker: "", speaker_cluster: "lane:abc123:1" })).toBe(false);
  });

  it("does not flag legacy clusterless/unclustered segments", () => {
    expect(isUnlabeledSoloLaneSegment({ speaker: "" })).toBe(false);
    expect(isUnlabeledSoloLaneSegment({ speaker: "", speaker_cluster: "3" })).toBe(false);
  });
});

describe("buildSpeakerDisplayLabels — unlabeled solo lanes (F2)", () => {
  it("assigns distinct 未特定の話者A/B labels to two unnamed solo lanes by first appearance", () => {
    const segments = [
      { speaker: "", speaker_cluster: "lane:aaaaaaaaaa" },
      { speaker: "", speaker_cluster: "lane:bbbbbbbbbb" },
      { speaker: "", speaker_cluster: "lane:aaaaaaaaaa" },
    ];
    const labels = buildSpeakerDisplayLabels(segments);

    expect(labels.get("lane:aaaaaaaaaa")).toBe("未特定の話者A");
    expect(labels.get("lane:bbbbbbbbbb")).toBe("未特定の話者B");
  });

  it("never resolves an unnamed solo lane's raw cluster id via getSpeakerDisplayLabel", () => {
    const segments = [{ speaker: "", speaker_cluster: "lane:xyz9876543" }];
    const labels = buildSpeakerDisplayLabels(segments);
    const label = getSpeakerDisplayLabel(segments[0], labels);

    expect(label).not.toContain("lane:");
    expect(label).toBe("未特定の話者A");
  });

  it("does not conflate an unlabeled solo lane with a needs_review sub-cluster label", () => {
    const segments = [
      { speaker: "", speaker_cluster: "lane:aaaaaaaaaa" },
      { speaker: "", speaker_cluster: "lane:aaaaaaaaaa:1", speaker_mapping_status: "needs_review" },
    ];
    const labels = buildSpeakerDisplayLabels(segments);

    expect(labels.get("lane:aaaaaaaaaa")).toBe("未特定の話者A");
    expect(labels.get("lane:aaaaaaaaaa:1")).toBe("要確認の話者A");
  });
});

describe("resolveSpeakerLabelByKey (F2 — filter dropdown/badge display path)", () => {
  it("returns the resolved label when the key is registered", () => {
    const labels = new Map([["lane:abc123", "未特定の話者A"]]);
    expect(resolveSpeakerLabelByKey("lane:abc123", labels)).toBe("未特定の話者A");
  });

  it("never renders a raw solo-lane identity key even if buildSpeakerDisplayLabels missed it", () => {
    const labels = new Map<string, string>(); // simulates an unregistered key
    expect(resolveSpeakerLabelByKey("lane:abc123", labels)).not.toContain("lane:");
    expect(resolveSpeakerLabelByKey("lane:abc123", labels)).toBe("未特定の話者");
  });

  it("never renders a raw sub-cluster identity key either", () => {
    const labels = new Map<string, string>();
    expect(resolveSpeakerLabelByKey("lane:abc123:1", labels)).not.toContain("lane:");
  });

  it("falls back to the key itself for a genuine (non-lane) named speaker", () => {
    const labels = new Map<string, string>();
    expect(resolveSpeakerLabelByKey("田中", labels)).toBe("田中");
  });

  it("falls back to 不明 for an empty key", () => {
    const labels = new Map<string, string>();
    expect(resolveSpeakerLabelByKey("", labels)).toBe("不明");
  });

  it("end-to-end: a solo lane produced by buildSpeakerDisplayLabels resolves through resolveSpeakerLabelByKey without ever surfacing the raw id", () => {
    const segments = [{ speaker: "", speaker_cluster: "lane:ffffffffff" }];
    const labels = buildSpeakerDisplayLabels(segments);
    const displayed = resolveSpeakerLabelByKey("lane:ffffffffff", labels);
    expect(displayed).toBe("未特定の話者A");
    expect(displayed).not.toContain("lane:");
  });
});

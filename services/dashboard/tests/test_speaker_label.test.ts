import { describe, expect, it } from "vitest";
import {
  buildSpeakerDisplayLabels,
  getSpeakerDisplayLabel,
  getSpeakerIdentityKey,
  isNeedsReviewSegment,
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

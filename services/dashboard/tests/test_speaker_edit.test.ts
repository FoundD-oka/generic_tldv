import { describe, expect, it } from "vitest";
import {
  buildSegmentReassign,
  buildSpeakerMerge,
  buildSpeakerRename,
  describeSpeakerUpdate,
} from "@/lib/speaker-edit";

describe("buildSpeakerRename", () => {
  it("renames by cluster when the segment carries one", () => {
    expect(buildSpeakerRename({ speaker: "Unknown", speaker_cluster: "1" }, "田中")).toEqual({
      rename: [{ from_cluster: "1", to_name: "田中" }],
    });
  });

  it("falls back to label rename for legacy rows without cluster", () => {
    expect(buildSpeakerRename({ speaker: "Unknown" }, "田中")).toEqual({
      rename: [{ from_name: "Unknown", to_name: "田中" }],
    });
  });

  it("rejects empty names and unnamed segments", () => {
    expect(buildSpeakerRename({ speaker: "A", speaker_cluster: "1" }, "  ")).toBeNull();
    expect(buildSpeakerRename({ speaker: "" }, "田中")).toBeNull();
  });
});

describe("buildSegmentReassign", () => {
  it("targets only segments with stable ids", () => {
    expect(
      buildSegmentReassign(["deferred:42:5:10.000", undefined, null, "deferred:42:6:12.000"], "山田")
    ).toEqual({
      reassign: [{ segment_ids: ["deferred:42:5:10.000", "deferred:42:6:12.000"], to_name: "山田" }],
    });
  });

  it("includes to_cluster when provided", () => {
    expect(buildSegmentReassign(["s1"], "山田", "2")).toEqual({
      reassign: [{ segment_ids: ["s1"], to_name: "山田", to_cluster: "2" }],
    });
  });

  it("returns null without ids or name", () => {
    expect(buildSegmentReassign([], "山田")).toBeNull();
    expect(buildSegmentReassign(["s1"], " ")).toBeNull();
  });
});

describe("buildSpeakerMerge", () => {
  const segments = [
    { speaker: "Unknown", speaker_cluster: "1" },
    { speaker: "Unknown 2", speaker_cluster: "3" },
    { speaker: "レガシー", speaker_cluster: undefined },
    { speaker: "無関係", speaker_cluster: "9" },
  ];

  it("merges clusters of the selected speakers", () => {
    expect(buildSpeakerMerge(["Unknown", "Unknown 2"], segments, "佐藤")).toEqual({
      merge: [{ clusters: ["1", "3"], to_name: "佐藤" }],
    });
  });

  it("mixes cluster merge and legacy label renames", () => {
    expect(buildSpeakerMerge(["Unknown", "Unknown 2", "レガシー"], segments, "佐藤")).toEqual({
      merge: [{ clusters: ["1", "3"], to_name: "佐藤" }],
      rename: [{ from_name: "レガシー", to_name: "佐藤" }],
    });
  });

  it("uses rename when only one cluster is involved", () => {
    expect(buildSpeakerMerge(["Unknown"], segments, "佐藤")).toEqual({
      rename: [{ from_cluster: "1", to_name: "佐藤" }],
    });
  });

  it("skips renaming a legacy speaker onto its own name", () => {
    expect(buildSpeakerMerge(["レガシー"], segments, "レガシー")).toBeNull();
  });

  it("returns null for empty selection or name", () => {
    expect(buildSpeakerMerge([], segments, "佐藤")).toBeNull();
    expect(buildSpeakerMerge(["Unknown"], segments, "")).toBeNull();
  });

  it("BUG-002: matches needs_review lane sub-cluster segments by identity key (speaker || speaker_cluster), not by speaker alone", () => {
    // Needs_review segments carry an empty `speaker` and their identity is
    // the raw `speaker_cluster` — exactly what getSpeakerIdentityKey
    // returns and what the viewer's speaker filter selects by.
    const withNeedsReview = [
      { speaker: "", speaker_cluster: "lane:aaaaaaaaaa:spk0" },
      { speaker: "", speaker_cluster: "lane:aaaaaaaaaa:spk1" },
    ];
    expect(
      buildSpeakerMerge(
        ["lane:aaaaaaaaaa:spk0", "lane:aaaaaaaaaa:spk1"],
        withNeedsReview,
        "佐藤"
      )
    ).toEqual({
      merge: [{ clusters: ["lane:aaaaaaaaaa:spk0", "lane:aaaaaaaaaa:spk1"], to_name: "佐藤" }],
    });
  });

  it("BUG-002: mixing a needs_review sub-speaker with a named speaker covers both clusters (no silent drop)", () => {
    const mixed = [
      { speaker: "Unknown", speaker_cluster: "1" },
      { speaker: "", speaker_cluster: "lane:aaaaaaaaaa:spk0" },
    ];
    expect(
      buildSpeakerMerge(["Unknown", "lane:aaaaaaaaaa:spk0"], mixed, "佐藤")
    ).toEqual({
      merge: [{ clusters: ["1", "lane:aaaaaaaaaa:spk0"], to_name: "佐藤" }],
    });
  });
});

describe("describeSpeakerUpdate", () => {
  it("sums all operation counts", () => {
    expect(describeSpeakerUpdate({ rename: 3, merge: 2, reassign: 1 })).toBe(
      "6件の発話の話者を更新しました"
    );
  });
});

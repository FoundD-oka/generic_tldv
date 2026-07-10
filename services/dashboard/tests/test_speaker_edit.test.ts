import { describe, expect, it } from "vitest";
import {
  buildSegmentReassign,
  buildSpeakerMerge,
  buildSpeakerRename,
  describeSpeakerUpdate,
  getRenameEnrollCandidates,
  reconcileRejectedSuggestions,
  type RejectedSuggestionEntry,
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

// issue #27 Phase4 (ARC-5): accepting a voiceprint suggestion reuses the
// EXISTING cluster-rename path, called with the candidate name for that
// cluster — no new rename mechanism is introduced.
describe("voiceprint suggestion acceptance reuses buildSpeakerRename (issue #27 Phase4)", () => {
  it("triggers a rename payload for the right cluster with the candidate name", () => {
    expect(
      buildSpeakerRename({ speaker: "", speaker_cluster: "lane:hash123:1" }, "田中")
    ).toEqual({
      rename: [{ from_cluster: "lane:hash123:1", to_name: "田中" }],
    });
  });
});

// issue #27 Phase4 (ARC-6/NH-3): the implicit-enroll offer must only ever be
// shown for rename operations in the PATCH response's affected_clusters —
// merge/reassign can span multiple clusters and their target isn't unique.
describe("getRenameEnrollCandidates", () => {
  it("returns rename entries only, dropping merge and reassign", () => {
    expect(
      getRenameEnrollCandidates([
        { cluster_id: "1", display_name: "田中", operation: "rename" },
        { cluster_id: "2", display_name: "山田", operation: "merge" },
        { cluster_id: "3", display_name: "佐藤", operation: "reassign" },
        { cluster_id: "4", display_name: "鈴木", operation: "rename" },
      ])
    ).toEqual([
      { cluster_id: "1", display_name: "田中" },
      { cluster_id: "4", display_name: "鈴木" },
    ]);
  });

  it("returns an empty array when there are no rename entries", () => {
    expect(
      getRenameEnrollCandidates([{ cluster_id: "2", display_name: "山田", operation: "merge" }])
    ).toEqual([]);
  });

  it("returns an empty array for undefined affected_clusters", () => {
    expect(getRenameEnrollCandidates(undefined)).toEqual([]);
  });

  // BUG-004: accepting a voiceprint suggestion reuses the rename PATCH path,
  // so the response's affected_clusters entry is indistinguishable from a
  // manual rename unless the caller explicitly excludes the accepted cluster.
  it("BUG-004: excludes cluster ids passed via excludeClusterIds (suggestion acceptance)", () => {
    expect(
      getRenameEnrollCandidates(
        [
          { cluster_id: "1", display_name: "田中", operation: "rename" },
          { cluster_id: "4", display_name: "鈴木", operation: "rename" },
        ],
        ["1"]
      )
    ).toEqual([{ cluster_id: "4", display_name: "鈴木" }]);
  });

  it("BUG-004: manual renames (no excludeClusterIds) still offer enrollment", () => {
    expect(
      getRenameEnrollCandidates([
        { cluster_id: "1", display_name: "田中", operation: "rename" },
      ])
    ).toEqual([{ cluster_id: "1", display_name: "田中" }]);
  });

  it("BUG-004: an empty excludeClusterIds list behaves like no exclusion", () => {
    expect(
      getRenameEnrollCandidates(
        [{ cluster_id: "1", display_name: "田中", operation: "rename" }],
        []
      )
    ).toEqual([{ cluster_id: "1", display_name: "田中" }]);
  });
});

// issue #27 Phase4 (BUG-010): the optimistic reject set must reconcile against
// fresh transcript data instead of only ever growing for the component's
// lifetime, otherwise a genuinely new suggestion for a previously-rejected
// cluster (from a later matching run) stays hidden forever.
describe("reconcileRejectedSuggestions (issue #27 Phase4 BUG-010)", () => {
  const rejectedEntry: RejectedSuggestionEntry = {
    candidateDisplayName: "田中",
    similarity: 0.9,
  };

  it("clears an entry once fresh data no longer carries any suggestion for that cluster (rejection confirmed server-side)", () => {
    const rejected = new Map([["c1", rejectedEntry]]);
    const segments = [{ speaker_cluster: "c1", speaker_suggestion: undefined }];
    const result = reconcileRejectedSuggestions(rejected, segments);
    expect(result.has("c1")).toBe(false);
  });

  it("keeps hiding while fresh data still shows the exact same rejected suggestion (server hasn't reflected the reject yet)", () => {
    const rejected = new Map([["c1", rejectedEntry]]);
    const segments = [
      {
        speaker_cluster: "c1",
        speaker_suggestion: {
          candidate_display_name: "田中",
          similarity: 0.9,
          status: "suggested" as const,
        },
      },
    ];
    const result = reconcileRejectedSuggestions(rejected, segments);
    expect(result.has("c1")).toBe(true);
  });

  it("un-hides once fresh data carries a NEW suggestion for the same cluster from a later matching run", () => {
    const rejected = new Map([["c1", rejectedEntry]]);
    const segments = [
      {
        speaker_cluster: "c1",
        speaker_suggestion: {
          candidate_display_name: "山田",
          similarity: 0.95,
          status: "suggested" as const,
        },
      },
    ];
    const result = reconcileRejectedSuggestions(rejected, segments);
    expect(result.has("c1")).toBe(false);
  });

  it("un-hides when the same candidate name reappears with a different similarity score (treated as a new match run)", () => {
    const rejected = new Map([["c1", rejectedEntry]]);
    const segments = [
      {
        speaker_cluster: "c1",
        speaker_suggestion: {
          candidate_display_name: "田中",
          similarity: 0.99,
          status: "suggested" as const,
        },
      },
    ];
    const result = reconcileRejectedSuggestions(rejected, segments);
    expect(result.has("c1")).toBe(false);
  });

  it("leaves unrelated rejected clusters untouched", () => {
    const rejected = new Map([
      ["c1", rejectedEntry],
      ["c2", { candidateDisplayName: "佐藤", similarity: 0.8 }],
    ]);
    const segments = [
      { speaker_cluster: "c1", speaker_suggestion: undefined },
      {
        speaker_cluster: "c2",
        speaker_suggestion: {
          candidate_display_name: "佐藤",
          similarity: 0.8,
          status: "suggested" as const,
        },
      },
    ];
    const result = reconcileRejectedSuggestions(rejected, segments);
    expect(result.has("c1")).toBe(false);
    expect(result.has("c2")).toBe(true);
  });

  it("returns the same Map reference when there is nothing to reconcile (avoids unnecessary re-renders)", () => {
    const empty = new Map<string, RejectedSuggestionEntry>();
    expect(reconcileRejectedSuggestions(empty, [])).toBe(empty);

    const rejected = new Map([["c1", rejectedEntry]]);
    const segments = [
      {
        speaker_cluster: "c1",
        speaker_suggestion: {
          candidate_display_name: "田中",
          similarity: 0.9,
          status: "suggested" as const,
        },
      },
    ];
    expect(reconcileRejectedSuggestions(rejected, segments)).toBe(rejected);
  });
});

import type { SpeakerUpdatePayload, TranscriptSegment } from "@/types/vexa";
import { getSpeakerIdentityKey } from "@/lib/speaker-label";

/**
 * 話者修正ペイロード構築ヘルパー（issue #24 Phase 1c）。
 * UIコンポーネントから分離してvitestで検証する。
 */

/** クラスタがあればクラスタ一括rename、なければ現在ラベルでのrename。 */
export function buildSpeakerRename(
  segment: Pick<TranscriptSegment, "speaker" | "speaker_cluster">,
  toName: string
): SpeakerUpdatePayload | null {
  const name = toName.trim();
  if (!name) return null;
  if (segment.speaker_cluster) {
    return { rename: [{ from_cluster: segment.speaker_cluster, to_name: name }] };
  }
  if (segment.speaker) {
    return { rename: [{ from_name: segment.speaker, to_name: name }] };
  }
  return null;
}

/** 指定segment_id群のみを別話者へ付け替える。 */
export function buildSegmentReassign(
  segmentIds: Array<string | undefined | null>,
  toName: string,
  toCluster?: string
): SpeakerUpdatePayload | null {
  const name = toName.trim();
  const ids = segmentIds.filter((id): id is string => !!id);
  if (!name || ids.length === 0) return null;
  return {
    reassign: [{ segment_ids: ids, to_name: name, ...(toCluster ? { to_cluster: toCluster } : {}) }],
  };
}

/**
 * 選択した話者名の集合を1名に統合する。
 * クラスタを持つ話者はクラスタmergeに、クラスタなし（レガシー行）の話者は
 * ラベルrenameにまとめる。
 */
export function buildSpeakerMerge(
  selectedSpeakers: string[],
  segments: Array<Pick<TranscriptSegment, "speaker" | "speaker_cluster">>,
  toName: string
): SpeakerUpdatePayload | null {
  const name = toName.trim();
  if (!name || selectedSpeakers.length === 0) return null;

  const selected = new Set(selectedSpeakers);
  const clusters: string[] = [];
  const clusterlessNames = new Set<string>();
  for (const seg of segments) {
    // BUG-002 — `selectedSpeakers` comes from the viewer's speaker filter,
    // which is keyed by identity (getSpeakerIdentityKey: speaker ||
    // speaker_cluster), not by `speaker` alone. A needs_review lane
    // sub-cluster segment has an empty `speaker`, so filtering on
    // `seg.speaker` here would always skip it even when its cluster (its
    // real identity key) is in `selected`. Match by the same identity key
    // the filter UI uses instead.
    const identity = getSpeakerIdentityKey(seg);
    if (!identity || !selected.has(identity)) continue;
    if (seg.speaker_cluster) {
      if (!clusters.includes(seg.speaker_cluster)) clusters.push(seg.speaker_cluster);
    } else {
      clusterlessNames.add(seg.speaker);
    }
  }

  const payload: SpeakerUpdatePayload = {};
  if (clusters.length >= 2) {
    payload.merge = [{ clusters, to_name: name }];
  } else if (clusters.length === 1) {
    payload.rename = [{ from_cluster: clusters[0], to_name: name }];
  }
  if (clusterlessNames.size > 0) {
    payload.rename = [
      ...(payload.rename || []),
      ...[...clusterlessNames]
        .filter((n) => n !== name)
        .map((n) => ({ from_name: n, to_name: name })),
    ];
  }
  if (!payload.merge && !(payload.rename && payload.rename.length > 0)) return null;
  return payload;
}

/** 更新結果のトースト説明文。 */
export function describeSpeakerUpdate(updated: Record<string, number>): string {
  const total = (updated.rename || 0) + (updated.merge || 0) + (updated.reassign || 0);
  return `${total}件の発話の話者を更新しました`;
}

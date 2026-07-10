import type { AffectedCluster, SpeakerSuggestion, SpeakerUpdatePayload, TranscriptSegment } from "@/types/vexa";
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

/**
 * PATCH応答の`affected_clusters`から、暗黙登録オファー対象（issue #27
 * Phase4, ARC-6/NH-3）のrename結果のみを抽出する。merge/reassignは対象
 * クラスタの一意性が保証できないため、本フェーズではオファーを出さない。
 *
 * `excludeClusterIds`（BUG-004）: 声紋照合候補を「承認」した場合のrenameも
 * update_meeting_speakers上は通常のrenameと区別されず`affected_clusters`に
 * 載る。しかし承認は既に登録済みの声紋との一致確認であり、暗黙登録オファーを
 * 出す意味がない（"この声を登録しますか？"と聞くのは手動renameの場合のみで
 * よい）。呼び出し側（承認ハンドラ）が対象クラスタIDを渡して除外する。
 */
export function getRenameEnrollCandidates(
  affectedClusters: AffectedCluster[] | undefined,
  excludeClusterIds?: Iterable<string>
): Array<{ cluster_id: string; display_name: string }> {
  const excluded = excludeClusterIds ? new Set(excludeClusterIds) : undefined;
  return (affectedClusters || [])
    .filter((cluster) => cluster.operation === "rename" && !excluded?.has(cluster.cluster_id))
    .map(({ cluster_id, display_name }) => ({ cluster_id, display_name }));
}

/** BUG-010: 却下済み候補の追跡エントリ（クラスタごとに1件）。 */
export interface RejectedSuggestionEntry {
  candidateDisplayName: string;
  similarity: number;
}

/**
 * 文字起こしデータの再取得時に、却下フラグ（`rejectedSuggestionClusters`）を
 * 最新の`speaker_suggestion`と突き合わせて再検証する（issue #27 Phase4
 * BUG-010）。
 *
 * `speaker_suggestion`のpayloadには照合実行を識別するrun idが含まれない
 * （`SpeakerSuggestion`型参照）ため、run idでの厳密な区別はできない。
 * 代わりに候補の内容（表示名+類似度）で「同じ候補がまだ残っているだけ」
 * （サーバ側の却下反映待ちのレース）と「新しい照合実行で出た別候補」を
 * 区別する:
 * - 対象クラスタにもう`speaker_suggestion`が無い → 却下がサーバ側に反映
 *   済みなので追跡終了。
 * - 内容が却下時と同一 → まだサーバ反映前の同じ候補なので却下表示を維持。
 * - 内容が異なる → 新しい候補なので却下フラグを外し再表示する。
 */
export function reconcileRejectedSuggestions(
  rejected: ReadonlyMap<string, RejectedSuggestionEntry>,
  segments: Array<Pick<TranscriptSegment, "speaker_cluster" | "speaker_suggestion">>
): Map<string, RejectedSuggestionEntry> {
  if (rejected.size === 0) return rejected as Map<string, RejectedSuggestionEntry>;

  const currentByCluster = new Map<string, SpeakerSuggestion>();
  for (const seg of segments) {
    if (seg.speaker_cluster && seg.speaker_suggestion) {
      currentByCluster.set(seg.speaker_cluster, seg.speaker_suggestion);
    }
  }

  let changed = false;
  const next = new Map(rejected);
  for (const [clusterId, rejectedEntry] of rejected) {
    const current = currentByCluster.get(clusterId);
    if (!current) {
      next.delete(clusterId);
      changed = true;
      continue;
    }
    const isSameSuggestion =
      current.candidate_display_name === rejectedEntry.candidateDisplayName &&
      current.similarity === rejectedEntry.similarity;
    if (!isSameSuggestion) {
      next.delete(clusterId);
      changed = true;
    }
  }
  return changed ? next : (rejected as Map<string, RejectedSuggestionEntry>);
}

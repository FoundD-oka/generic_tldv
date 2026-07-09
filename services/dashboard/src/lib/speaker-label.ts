import type { TranscriptSegment } from "@/types/vexa";

/**
 * 話者identityと表示ラベルの分離（issue #26 Phase 3, B-4/FC-10）。
 *
 * レーン内共有マイクの未命名サブクラスタは `speaker_cluster` が
 * `lane:{key}:{sub}` 形式のまま残り、`speaker` は空/未設定になる
 * （サーバ側で `speaker_mapping_status="needs_review"` が付く）。
 * これを画面のidentityキー（grouping/filter/色/連続header判定に使う）
 * としてそのまま使うと、生のクラスタidがユーザーに見えてしまう。
 * ここではidentityキーの算出と、ユーザー向け表示ラベルの算出を分離する。
 */

type SpeakerLabelInput = Pick<
  TranscriptSegment,
  "speaker" | "speaker_cluster" | "speaker_mapping_status"
>;

/** サブクラスタ形式 `lane:{key}:{sub}` にマッチ（サーバ側の namespace 規則と同じ）。 */
const LANE_SUBCLUSTER_RE = /^lane:[^:]+:.+$/;

/**
 * grouping/filter/色/連続speaker header判定など、identityが問題になる
 * 箇所すべてで使うフォールバックキー。表示には使わない。
 */
export function getSpeakerIdentityKey(segment: SpeakerLabelInput): string {
  return segment.speaker || segment.speaker_cluster || "";
}

/**
 * 「要確認」（命名されていないレーン内サブクラスタ）かどうかを判定する。
 * サーバが `speaker_mapping_status` を付けていればそれを信頼し、フォール
 * バックとして `speaker` が空かつ `speaker_cluster` がサブクラスタ形式の
 * 場合も要確認とみなす。
 */
export function isNeedsReviewSegment(segment: SpeakerLabelInput): boolean {
  if (segment.speaker_mapping_status === "needs_review") return true;
  return !segment.speaker && !!segment.speaker_cluster && LANE_SUBCLUSTER_RE.test(segment.speaker_cluster);
}

// 0 -> "A", 25 -> "Z", 26 -> "AA", ... （会議内で滅多に26クラスタを超えないが安全側に倒す）
function indexToLetters(index: number): string {
  let n = index;
  let label = "";
  do {
    label = String.fromCharCode(65 + (n % 26)) + label;
    n = Math.floor(n / 26) - 1;
  } while (n >= 0);
  return label;
}

/**
 * 会議全体のsegment列（時系列順）から、identityキー→表示ラベルのmapを
 * 一度だけ構築する。
 * - 命名済み（`speaker`が入っている）segmentはその名前をラベルにする。
 * - 未命名の要確認サブクラスタは、そのサブクラスタidの初出順に
 *   「要確認の話者A」「要確認の話者B」…を割り当てる。
 * - それ以外（クラスタも名前もない従来の空segment）はmapに入れず、
 *   呼び出し側で従来通り空文字にフォールバックさせる。
 */
export function buildSpeakerDisplayLabels(segments: SpeakerLabelInput[]): Map<string, string> {
  const labels = new Map<string, string>();
  let nextReviewIndex = 0;
  for (const seg of segments) {
    const key = getSpeakerIdentityKey(seg);
    if (!key || labels.has(key)) continue;
    if (seg.speaker) {
      labels.set(key, seg.speaker);
    } else if (isNeedsReviewSegment(seg)) {
      labels.set(key, `要確認の話者${indexToLetters(nextReviewIndex)}`);
      nextReviewIndex += 1;
    }
  }
  return labels;
}

/**
 * 1件のsegment（または合成segment）の表示ラベルを解決する。
 * 生のcluster id（`lane:{key}:{sub}`）は絶対に返さない。
 */
export function getSpeakerDisplayLabel(
  segment: SpeakerLabelInput,
  labels: Map<string, string>
): string {
  const key = getSpeakerIdentityKey(segment);
  if (key && labels.has(key)) return labels.get(key)!;
  return segment.speaker || "";
}

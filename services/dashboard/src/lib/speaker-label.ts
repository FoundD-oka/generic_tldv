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
// `g:` は通常のGemini匿名話者、`x:` はチャンク境界の位相を一意に
// 証明できず、安全側に重複保持した話者。どちらも画面上では匿名identity
// として扱うが、`x:` はサーバ側の声紋自動照合対象には入れない。
const GEMINI_CLUSTER_RE = /^(?:g|x):[0-9a-f]{8}:s[1-9][0-9]*$/;

function isUnconfirmedSpeakerName(speaker: string | null | undefined): boolean {
  const normalized = (speaker || "").trim();
  return !normalized || normalized.toLowerCase() === "unknown";
}

/**
 * ソロレーン形式`lane:{key}`（コロン1つだけ、サブクラスタではない）に
 * マッチする。F2（Fable final-audit consultation）: lane_labelが無く
 * DOM投票名も無いソロレーンはこの形のまま残り、`speaker_mapping_status`
 * も付かない（needs_reviewではない）ため`isNeedsReviewSegment`だけでは
 * 検出できない。ラベル未解決のまま生idを表示させないための専用判定。
 */
const LANE_SOLO_RE = /^lane:[^:]+$/;

/**
 * grouping/filter/色/連続speaker header判定など、identityが問題になる
 * 箇所すべてで使うフォールバックキー。表示には使わない。
 */
export function getSpeakerIdentityKey(segment: SpeakerLabelInput): string {
  if (
    segment.speaker_cluster
    && GEMINI_CLUSTER_RE.test(segment.speaker_cluster)
    && isUnconfirmedSpeakerName(segment.speaker)
  ) {
    return segment.speaker_cluster;
  }
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
  if (
    segment.speaker_cluster
    && GEMINI_CLUSTER_RE.test(segment.speaker_cluster)
    && isUnconfirmedSpeakerName(segment.speaker)
  ) {
    return true;
  }
  return !segment.speaker && !!segment.speaker_cluster && LANE_SUBCLUSTER_RE.test(segment.speaker_cluster);
}

/**
 * F2（Fable final-audit consultation）: 名前もラベルも付いていない
 * ソロレーン（サブクラスタ形式ではない、`lane:{key}`ちょうどコロン1つ）
 * かどうかを判定する。`isNeedsReviewSegment`はサブクラスタ形式のみを見る
 * ため、この形は素通りしてしまう —未解決のまま生cluster idが表示に漏れる
 * のを防ぐための専用判定。
 */
export function isUnlabeledSoloLaneSegment(segment: SpeakerLabelInput): boolean {
  return !segment.speaker && !!segment.speaker_cluster && LANE_SOLO_RE.test(segment.speaker_cluster);
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
 * - 未命名のソロレーン（F2: lane_label無し・DOM名無しで`lane:{key}`の
 *   ままのもの）は、そのレーンidの初出順に「未特定の話者A」「未特定の
 *   話者B」…を割り当てる（要確認＝複数話者の切り分けが必要、とは意味が
 *   異なるため別ラベル）。
 * - それ以外（クラスタも名前もない従来の空segment）はmapに入れず、
 *   呼び出し側で従来通り空文字にフォールバックさせる。
 */
export function buildSpeakerDisplayLabels(segments: SpeakerLabelInput[]): Map<string, string> {
  const labels = new Map<string, string>();
  let nextReviewIndex = 0;
  let nextUnlabeledIndex = 0;
  for (const seg of segments) {
    const key = getSpeakerIdentityKey(seg);
    if (!key || labels.has(key)) continue;
    if (isNeedsReviewSegment(seg)) {
      labels.set(key, `要確認の話者${indexToLetters(nextReviewIndex)}`);
      nextReviewIndex += 1;
    } else if (seg.speaker) {
      labels.set(key, seg.speaker);
    } else if (isUnlabeledSoloLaneSegment(seg)) {
      labels.set(key, `未特定の話者${indexToLetters(nextUnlabeledIndex)}`);
      nextUnlabeledIndex += 1;
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

/**
 * F2（Fable final-audit consultation）: 話者フィルタdropdown/バッジなど、
 * segment全体ではなくidentityキーだけを持っている描画箇所向けの表示ラベル
 * 解決。`labels`に無いキーであっても、生の`lane:{key}`（サブクラスタ形式
 * `lane:{key}:{sub}`を含む）を絶対に返さない — `buildSpeakerDisplayLabels`
 * の対象漏れがあっても二重の防御になる。
 */
export function resolveSpeakerLabelByKey(key: string, labels: Map<string, string>): string {
  if (key && labels.has(key)) return labels.get(key)!;
  if (key.startsWith("lane:") || GEMINI_CLUSTER_RE.test(key)) return "未特定の話者";
  return key || "不明";
}

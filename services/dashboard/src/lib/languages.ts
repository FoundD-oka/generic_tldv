/**
 * All languages supported by Whisper/faster-whisper (matches backend ACCEPTED_LANGUAGE_CODES).
 * Used for searchable language picker with recent choices on top.
 */
export const WHISPER_LANGUAGE_NAMES: Record<string, string> = {
  af: "アフリカーンス語",
  am: "アムハラ語",
  ar: "アラビア語",
  as: "アッサム語",
  az: "アゼルバイジャン語",
  ba: "バシキール語",
  be: "ベラルーシ語",
  bg: "ブルガリア語",
  bn: "ベンガル語",
  bo: "チベット語",
  br: "ブルトン語",
  bs: "ボスニア語",
  ca: "カタルーニャ語",
  cs: "チェコ語",
  cy: "ウェールズ語",
  da: "デンマーク語",
  de: "ドイツ語",
  el: "ギリシャ語",
  en: "英語",
  es: "スペイン語",
  et: "エストニア語",
  eu: "バスク語",
  fa: "ペルシア語",
  fi: "フィンランド語",
  fo: "フェロー語",
  fr: "フランス語",
  gl: "ガリシア語",
  gu: "グジャラート語",
  ha: "ハウサ語",
  haw: "ハワイ語",
  he: "ヘブライ語",
  hi: "ヒンディー語",
  hr: "クロアチア語",
  ht: "ハイチ語",
  hu: "ハンガリー語",
  hy: "アルメニア語",
  id: "インドネシア語",
  is: "アイスランド語",
  it: "イタリア語",
  ja: "日本語",
  jw: "ジャワ語",
  ka: "ジョージア語",
  kk: "カザフ語",
  km: "クメール語",
  kn: "カンナダ語",
  ko: "韓国語",
  la: "ラテン語",
  lb: "ルクセンブルク語",
  ln: "リンガラ語",
  lo: "ラオ語",
  lt: "リトアニア語",
  lv: "ラトビア語",
  mg: "マダガスカル語",
  mi: "マオリ語",
  mk: "マケドニア語",
  ml: "マラヤーラム語",
  mn: "モンゴル語",
  mr: "マラーティー語",
  ms: "マレー語",
  mt: "マルタ語",
  my: "ミャンマー語",
  ne: "ネパール語",
  nl: "オランダ語",
  nn: "ノルウェー語（ニーノシュク）",
  no: "ノルウェー語",
  oc: "オック語",
  pa: "パンジャーブ語",
  pl: "ポーランド語",
  ps: "パシュトー語",
  pt: "ポルトガル語",
  ro: "ルーマニア語",
  ru: "ロシア語",
  sa: "サンスクリット語",
  sd: "シンド語",
  si: "シンハラ語",
  sk: "スロバキア語",
  sl: "スロベニア語",
  sn: "ショナ語",
  so: "ソマリ語",
  sq: "アルバニア語",
  sr: "セルビア語",
  su: "スンダ語",
  sv: "スウェーデン語",
  sw: "スワヒリ語",
  ta: "タミル語",
  te: "テルグ語",
  tg: "タジク語",
  th: "タイ語",
  tk: "トルクメン語",
  tl: "タガログ語",
  tr: "トルコ語",
  tt: "タタール語",
  uk: "ウクライナ語",
  ur: "ウルドゥー語",
  uz: "ウズベク語",
  vi: "ベトナム語",
  yi: "イディッシュ語",
  yo: "ヨルバ語",
  zh: "中国語",
  yue: "広東語",
};

/**
 * Rough popularity order for display (most used first). Not comprehensive;
 * codes not listed appear after, sorted by name.
 */
const POPULARITY_ORDER: string[] = [
  "en", "es", "zh", "hi", "ar", "pt", "bn", "ru", "ja", "pa", "de", "jw", "ko", "fr",
  "te", "mr", "tr", "vi", "ta", "ur", "id", "pl", "nl", "it", "uk", "th", "gu", "fa",
  "sw", "ro", "ml", "kn", "my", "yo", "ha", "am", "ne", "si", "sv", "cs", "el", "hu",
  "fi", "da", "he", "sk", "bg", "no", "hr", "sr", "ca", "lt", "sl", "et", "lv", "tl",
  "af", "sq", "hy", "az", "eu", "gl", "mk", "ka", "lo", "km", "ps", "sd", "uz", "kk",
  "mn", "tg", "tk", "so", "sn", "mg", "oc", "br", "cy", "yi", "la", "bo", "sa", "fo",
  "lb", "ln", "tt", "ba", "su", "haw", "mi", "yue",
];

/** All Whisper language codes (no "auto"). Sorted by popularity then by name. */
export const WHISPER_LANGUAGE_CODES = (() => {
  const byName = (a: string, b: string) =>
    WHISPER_LANGUAGE_NAMES[a].localeCompare(WHISPER_LANGUAGE_NAMES[b]);
  const rank = (code: string) => {
    const i = POPULARITY_ORDER.indexOf(code);
    return i === -1 ? 1e4 : i;
  };
  return Object.keys(WHISPER_LANGUAGE_NAMES).sort((a, b) => {
    const r = rank(a) - rank(b);
    return r !== 0 ? r : byName(a, b);
  });
})();

export const RECENT_LANGUAGES_KEY = "vexa-recent-transcription-languages";
const RECENT_MAX = 10;

export function getRecentLanguageCodes(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(RECENT_LANGUAGES_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((c): c is string => typeof c === "string" && WHISPER_LANGUAGE_NAMES[c] != null).slice(0, RECENT_MAX);
  } catch {
    return [];
  }
}

export function saveRecentLanguage(code: string): void {
  if (code === "auto" || !WHISPER_LANGUAGE_NAMES[code]) return;
  const recent = getRecentLanguageCodes().filter((c) => c !== code);
  recent.unshift(code);
  try {
    localStorage.setItem(RECENT_LANGUAGES_KEY, JSON.stringify(recent.slice(0, RECENT_MAX)));
  } catch {
    // ignore
  }
}

export function getLanguageDisplayName(code: string): string {
  if (code === "auto") return "自動判定";
  return WHISPER_LANGUAGE_NAMES[code] ?? code.toUpperCase();
}

"""Text normalization, wake detection, and TTS cleanup."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


_PUNCT_RE = re.compile(r"[\s、。,.!?！？「」『』（）()【】\[\]〈〉<>:：;；]+")
_MARKDOWN_RE = re.compile(r"[*#`>\-•]+")
_LINK_RE = re.compile(r"\[(.*?)\]\(.*?\)")
_CHAT_WAKE_RE = re.compile(r"@?(?:カボス|かぼす|kabosu)", re.IGNORECASE)
_SECRET_PATTERNS = (
    (
        re.compile(r"([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@", re.IGNORECASE),
        r"\1[REDACTED]@",
    ),
    (re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE), "Bearer [REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9_-]+"), "[REDACTED_API_KEY]"),
    (
        re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|pwd)\b\s*([:=])\s*([^\s,;]+)"),
        r"\1\2[REDACTED]",
    ),
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
            re.IGNORECASE,
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (re.compile(r"\b[A-Za-z0-9_-]{48,}\b"), "[REDACTED_TOKEN]"),
)
_KABOSU_VARIANT_RE = (
    r"(?:(?:カ|か|コ|こ)[ーｰ]?(?:ボ|ぼ|ポ|ぽ|ホ|ほ|バ|ば)[ーｰ]?(?:ス|す|ズ|ず|酢)"
    r"|株酢|ｶﾎﾞｽ|カボちゃん|かぼちゃん|カーブス|かーぶす|kabosu|kabos|kavos|koposu|kopos|qabo?s)"
    r"(?:さん|ちゃん)?"
)
_WAKE_PREFIX_RE = r"(?:ねえ|ねぇ|はい|あ|えっと|えーと|あの|じゃあ|では|ok|okay|オーケー|お願い)"
_SENTENCE_END_RE = re.compile(r"[。！？!?]")
_SEED_LEADING_RE = re.compile(r"^[\s、。,.!?！？「」『』（）()【】\[\]〈〉<>:：;；]+")
_SEED_TRAILING_RE = re.compile(r"[\s、,.，「」『』（）()【】\[\]〈〉<>:：;；]+$")


@dataclass(frozen=True)
class WakeMatch:
    wake: str
    seed: str


def normalize_ja(text: str) -> str:
    """Normalize Japanese transcript text for wake/echo matching."""

    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = normalized.replace("ｶﾎﾞｽ", "カボス")
    normalized = re.sub(_KABOSU_VARIANT_RE, "kabosu", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"(ヴェクサ|ベクサー|ベクサ|vexa)", "vexa", normalized, flags=re.IGNORECASE)
    normalized = _PUNCT_RE.sub("", normalized)
    return normalized


def normalize_chat_text(text: str) -> str:
    """Normalize typed chat text without ASR-only Kabosu fuzziness."""

    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def contains_chat_wake(text: str) -> bool:
    """Return true when typed chat explicitly mentions Kabosu."""

    return bool(_CHAT_WAKE_RE.search(normalize_chat_text(text)))


def strip_chat_wake(text: str) -> str:
    """Remove explicit Kabosu wake mentions from a chat request."""

    stripped = _CHAT_WAKE_RE.sub("", normalize_chat_text(text))
    stripped = re.sub(r"^[\s、。,.!?！？:：;；]+", "", stripped)
    stripped = re.sub(r"[\s、。,.!?！？:：;；]+$", "", stripped)
    return re.sub(r"\s+", " ", stripped).strip()


def redact_secrets(text: str) -> str:
    """Mask obvious credentials before chat/transcript context leaves the service."""

    redacted = text
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def is_wake_only_text(text: str) -> bool:
    """Return true when text contains only one or more Kabosu wake calls."""

    return bool(re.fullmatch(r"(?:kabosu)+", normalize_ja(text)))


def _seed_is_negative(seed: str, negative_patterns: list[str] | None) -> bool:
    seed_norm = normalize_ja(seed)
    if not seed_norm:
        return False

    if re.match(r"^(って|の|を使って|という|というoss)", seed_norm):
        return True
    if re.search(r"ai(みたい|ツール|機能)", seed_norm):
        return True

    for pattern in negative_patterns or []:
        pattern_norm = normalize_ja(pattern)
        if not pattern_norm:
            continue
        if pattern_norm.startswith("kabosu"):
            tail = pattern_norm.removeprefix("kabosu")
            if tail and seed_norm.startswith(tail):
                return True
        elif pattern_norm in seed_norm:
            return True
    return False


def _strip_wake_prefix(text: str, wake_word: str) -> str | None:
    escaped = _KABOSU_VARIANT_RE if normalize_ja(wake_word) == "kabosu" else re.escape(wake_word)
    pattern = re.compile(
        rf"(?:^|[。!?！？\n\r])\s*(?:{_WAKE_PREFIX_RE})?[、。,.!?！？\s]*{escaped}"
        rf"(?:[、。,.!?！？\s]+(.*)|([\u3040-\u30ff\u3400-\u9fff].*)|$)",
        re.IGNORECASE,
    )
    match = pattern.search(text)
    if match:
        return (match.group(1) or match.group(2) or "").strip()

    return None


def _wake_pattern(wake_words: list[str]) -> re.Pattern[str]:
    patterns: list[str] = []
    for wake_word in wake_words or ["カボス"]:
        wake_norm = normalize_ja(wake_word)
        if not wake_norm:
            continue
        if "kabosu" in wake_norm:
            patterns.append(_KABOSU_VARIANT_RE)
        else:
            patterns.append(re.escape(wake_word))
    if not patterns:
        patterns.append(_KABOSU_VARIANT_RE)
    return re.compile("|".join(f"(?:{pattern})" for pattern in patterns), re.IGNORECASE)


def _strip_seed_edges(text: str) -> str:
    stripped = _SEED_LEADING_RE.sub("", text)
    stripped = _SEED_TRAILING_RE.sub("", stripped)
    return stripped.strip()


def _seed_from_any_wake(text: str, match: re.Match[str]) -> str:
    before = _strip_seed_edges(text[: match.start()])
    after = _strip_seed_edges(text[match.end() :])
    if before and re.fullmatch(_WAKE_PREFIX_RE, before, re.IGNORECASE):
        before = ""
    before_tail_is_sentence = bool(re.search(r"[。！？!?\n\r]\s*$", text[: match.start()]))

    if before and after:
        seed = after if before_tail_is_sentence else f"{before} {after}"
    elif before and before_tail_is_sentence:
        seed = ""
    else:
        seed = after or before

    seed = re.sub(_KABOSU_VARIANT_RE, "", seed, flags=re.IGNORECASE)
    seed = _strip_seed_edges(seed)
    if is_wake_only_text(seed):
        return ""
    return seed


def detect_wake(
    text: str,
    wake_words: list[str],
    negative_patterns: list[str] | None = None,
) -> WakeMatch | None:
    """Detect Kabosu-style wake calls anywhere in an utterance.

    The default words are intentionally product-facing ("カボス"), while Vexa
    remains the underlying meeting engine.
    """

    normalized = normalize_ja(text)
    if not normalized:
        return None

    matches = list(_wake_pattern(wake_words).finditer(text))
    if not matches:
        return None

    match = matches[-1]
    seed = _seed_from_any_wake(text, match)
    return WakeMatch(wake=match.group(0), seed=seed)


def clean_for_tts(text: str, max_chars: int = 220) -> str:
    """Make LLM output safe and readable for speech synthesis."""

    cleaned = _LINK_RE.sub(r"\1", text)
    cleaned = _MARKDOWN_RE.sub("", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= max_chars:
        return cleaned

    clipped = cleaned[:max_chars].rstrip()
    sentence_ends = list(_SENTENCE_END_RE.finditer(clipped))
    if sentence_ends:
        boundary = sentence_ends[-1].end()
        if boundary >= max(5, int(max_chars * 0.2)):
            return clipped[:boundary].strip()

    return clipped.rstrip("、,.，") + "。"


def add_safe_ssml_breaks(text: str) -> str:
    """Insert only short, known-safe SSML breaks."""

    return (
        text.replace("。", '。<break time="0.18s"/>')
        .replace("？", '？<break time="0.18s"/>')
        .replace("！", '！<break time="0.18s"/>')
    )


def is_echo_of_bot(text: str, recent_bot_texts: list[str]) -> bool:
    normalized = normalize_ja(text)
    if not normalized:
        return False

    for bot_text in recent_bot_texts:
        bot = normalize_ja(bot_text)
        if not bot:
            continue
        prefix = bot[:20]
        text_prefix = normalized[:20]
        if prefix and prefix in normalized:
            return True
        if text_prefix and text_prefix in bot:
            return True
    return False

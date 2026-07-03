const SECRET_PATTERNS: Array<[RegExp, string]> = [
  [/([a-z][a-z0-9+.-]*:\/\/)[^/\s:@]+:[^/\s@]+@/gi, "$1[REDACTED]@"],
  [/Bearer\s+[A-Za-z0-9._~+/=-]+/gi, "Bearer [REDACTED]"],
  [/sk-[A-Za-z0-9_-]+/g, "[REDACTED_API_KEY]"],
  [/\b(api[_-]?key|token|secret|password|passwd|pwd)\b\s*([:=])\s*([^\s,;]+)/gi, "$1$2[REDACTED]"],
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/gi, "[REDACTED_PRIVATE_KEY]"],
  [/\b[A-Za-z0-9_-]{48,}\b/g, "[REDACTED_TOKEN]"],
];

export function redactSecrets(text: string): string {
  return SECRET_PATTERNS.reduce(
    (redacted, [pattern, replacement]) => redacted.replace(pattern, replacement),
    text
  );
}

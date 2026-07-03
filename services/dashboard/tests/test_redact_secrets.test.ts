import { describe, expect, it } from "vitest";
import cases from "../../../packages/redaction-tests/secret-redaction-cases.json";
import { redactSecrets } from "@/lib/redact-secrets";

describe("redactSecrets", () => {
  it("matches the shared redaction contract", () => {
    for (const item of cases) {
      expect(redactSecrets(item.input), item.name).toBe(item.expected);
    }
  });
});

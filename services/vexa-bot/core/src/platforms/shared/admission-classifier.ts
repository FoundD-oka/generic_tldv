export type AdmissionErrorDecision = {
  admitted: false;
  rejected: boolean;
  reason: "admission_rejected_by_admin" | "admission_timeout" | "join_failure";
};

/**
 * waitForAdmission の reject を AdmissionDecision に分類する。
 * 1) error.outcome(googlemeet AdmissionError)を最優先で使う。
 * 2) outcome が無い場合(Teams/Zoom の素の Error)は従来の message 照合に
 *    フォールバックし、既存挙動を完全維持する。
 */
export function classifyAdmissionError(error: unknown): AdmissionErrorDecision {
  const outcome = (error as any)?.outcome;
  if (outcome === "denial") {
    return { admitted: false, rejected: true, reason: "admission_rejected_by_admin" };
  }
  if (outcome === "lobby_timeout") {
    return { admitted: false, rejected: false, reason: "admission_timeout" };
  }
  if (outcome === "join_failure") {
    return { admitted: false, rejected: false, reason: "join_failure" };
  }
  const msg: string = (error as any)?.message || String(error);
  if (msg.includes("rejected by meeting admin")) {
    return { admitted: false, rejected: true, reason: "admission_rejected_by_admin" };
  }
  return { admitted: false, rejected: false, reason: "admission_timeout" };
}

// src/identity.ts
function keyPart(value) {
  if (value === void 0 || value === null || value === "") return "unknown";
  return String(value);
}
function streamId(seg) {
  return keyPart(
    seg.track_id ?? seg.speaker_track_id ?? seg.speakerTrackId ?? seg.speakerSessionUid ?? seg.session_uid
  );
}
function meetingId(seg) {
  return keyPart(seg.meeting_id ?? seg.meetingInstanceId);
}
function getSegmentIdentityKey(seg) {
  const scope = `${meetingId(seg)}|${streamId(seg)}`;
  if (seg.segment_id) return `segment|${scope}|${seg.segment_id}`;
  return `time|${scope}|${seg.absolute_start_time}`;
}
function updatedAtMs(value) {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}
function shouldReplaceSegment(existing, incoming) {
  if (!existing) return true;
  const existingUpdated = updatedAtMs(existing.updated_at);
  const incomingUpdated = updatedAtMs(incoming.updated_at);
  if (existingUpdated !== null && incomingUpdated !== null) {
    return incomingUpdated >= existingUpdated;
  }
  return true;
}

// src/timestamps.ts
function parseUTCTimestamp(timestamp) {
  const hasZone = /[zZ]$/.test(timestamp) || /[+-]\d{2}:\d{2}$/.test(timestamp);
  return new Date(hasZone ? timestamp : `${timestamp}Z`);
}

// src/dedup.ts
function normalizeText(t) {
  return (t || "").trim().toLowerCase().replace(/[.,!?;:]+$/g, "").replace(/\s+/g, " ");
}
function deduplicateSegments(segments) {
  if (segments.length === 0) return segments;
  const deduped = [];
  for (const seg of segments) {
    if (deduped.length === 0) {
      deduped.push(seg);
      continue;
    }
    const last = deduped[deduped.length - 1];
    if ((seg.speaker || "") !== (last.speaker || "")) {
      deduped.push(seg);
      continue;
    }
    const segStart = parseUTCTimestamp(seg.absolute_start_time).getTime();
    const segEnd = parseUTCTimestamp(seg.absolute_end_time).getTime();
    const lastStart = parseUTCTimestamp(last.absolute_start_time).getTime();
    const lastEnd = parseUTCTimestamp(last.absolute_end_time).getTime();
    const segStartSec = segStart / 1e3;
    const segEndSec = segEnd / 1e3;
    const lastStartSec = lastStart / 1e3;
    const lastEndSec = lastEnd / 1e3;
    const sameText = (seg.text || "").trim() === (last.text || "").trim();
    const overlaps = Math.max(segStartSec, lastStartSec) < Math.min(segEndSec, lastEndSec);
    const gapSec = (segStart - lastEnd) / 1e3;
    if (!overlaps && sameText && gapSec >= 0 && gapSec <= 1) {
      if (preferSeg(seg, last)) {
        deduped[deduped.length - 1] = seg;
      }
      continue;
    }
    if (overlaps) {
      const segFullyInsideLast = segStartSec >= lastStartSec && segEndSec <= lastEndSec;
      const lastFullyInsideSeg = lastStartSec >= segStartSec && lastEndSec <= segEndSec;
      if (sameText) {
        if (preferSeg(seg, last)) {
          deduped[deduped.length - 1] = seg;
        }
        continue;
      }
      if (segFullyInsideLast) {
        if (seg.completed && !last.completed) {
          deduped[deduped.length - 1] = seg;
        }
        continue;
      }
      if (lastFullyInsideSeg) {
        if (last.completed && !seg.completed) {
          continue;
        }
        deduped[deduped.length - 1] = seg;
        continue;
      }
      const segTextClean = normalizeText(seg.text || "");
      const lastTextClean = normalizeText(last.text || "");
      const segDuration = segEndSec - segStartSec;
      const lastDuration = lastEndSec - lastStartSec;
      const overlapStart = Math.max(segStartSec, lastStartSec);
      const overlapEnd = Math.min(segEndSec, lastEndSec);
      const overlapDuration = overlapEnd - overlapStart;
      const overlapRatioSeg = segDuration > 0 ? overlapDuration / segDuration : 0;
      const overlapRatioLast = lastDuration > 0 ? overlapDuration / lastDuration : 0;
      const segExpandsLast = Boolean(lastTextClean) && Boolean(segTextClean) && segTextClean.includes(lastTextClean) && segTextClean.length > lastTextClean.length;
      if (segExpandsLast && overlapRatioLast >= 0.5 && (seg.completed || !last.completed)) {
        deduped[deduped.length - 1] = seg;
        continue;
      }
      const segIsTailRepeat = Boolean(segTextClean) && Boolean(lastTextClean) && lastTextClean.includes(segTextClean);
      if (segIsTailRepeat) {
        const segWordCount = segTextClean.split(/\s+/).filter((w) => w.length > 0).length;
        if (segDuration <= 1.5 && segWordCount <= 2 && overlapRatioSeg >= 0.25) {
          continue;
        }
      }
    }
    deduped.push(seg);
  }
  return deduped;
}
function preferSeg(seg, last) {
  if (seg.completed && !last.completed) return true;
  if (!seg.completed && last.completed) return false;
  const segDur = parseUTCTimestamp(seg.absolute_end_time).getTime() - parseUTCTimestamp(seg.absolute_start_time).getTime();
  const lastDur = parseUTCTimestamp(last.absolute_end_time).getTime() - parseUTCTimestamp(last.absolute_start_time).getTime();
  return segDur > lastDur;
}
function upsertSegments(existing, incoming) {
  for (const seg of incoming) {
    if (!seg.absolute_start_time || !(seg.text || "").trim()) continue;
    const key = getSegmentIdentityKey(seg);
    const legacyKey = seg.segment_id || seg.absolute_start_time;
    const storedKey = existing.has(key) ? key : existing.has(legacyKey) ? legacyKey : key;
    const prev = existing.get(storedKey);
    if (seg.completed && seg.speaker) {
      for (const [k, v] of existing.entries()) {
        if (k === storedKey) continue;
        if (!v.completed && v.speaker === seg.speaker && k.includes(":draft:")) {
          existing.delete(k);
        }
      }
    }
    if (prev && !shouldReplaceSegment(prev, seg)) {
      continue;
    }
    existing.set(storedKey, { ...prev, ...seg });
  }
  const textIndex = /* @__PURE__ */ new Map();
  for (const [key, seg] of existing.entries()) {
    const textKey = `${seg.speaker || ""}:${(seg.text || "").trim()}`;
    const existingKey = textIndex.get(textKey);
    if (existingKey && existingKey !== key) {
      const prev = existing.get(existingKey);
      if (prev) {
        if (seg.completed && !prev.completed) {
          existing.delete(existingKey);
        } else if (!seg.completed && prev.completed) {
          existing.delete(key);
          continue;
        }
      }
    }
    textIndex.set(textKey, key);
  }
  return existing;
}
function sortSegments(segments) {
  return [...segments].sort(
    (a, b) => a.absolute_start_time.localeCompare(b.absolute_start_time)
  );
}
function sortByStartTime(segments) {
  return [...segments].sort((a, b) => {
    const aStart = a.start_time ?? 0;
    const bStart = b.start_time ?? 0;
    if (aStart !== bStart) return aStart - bStart;
    return a.absolute_start_time.localeCompare(b.absolute_start_time);
  });
}
function deduplicateByIdentity(segments) {
  const seen = /* @__PURE__ */ new Map();
  for (const seg of segments) {
    const key = getSegmentIdentityKey(seg);
    const existing = seen.get(key);
    if (shouldReplaceSegment(existing, seg)) {
      seen.set(key, { ...existing, ...seg });
    }
  }
  return Array.from(seen.values());
}

// src/grouping.ts
var DEFAULT_MAX_CHARS = 512;
function defaultGetGroupKey(segment) {
  return segment.speaker || "Unknown";
}
function groupSegments(segments, options = {}) {
  if (!segments || segments.length === 0) return [];
  const getGroupKey = options.getGroupKey ?? defaultGetGroupKey;
  const maxChars = options.maxCharsPerGroup ?? DEFAULT_MAX_CHARS;
  const sorted = [...segments].sort(
    (a, b) => a.absolute_start_time.localeCompare(b.absolute_start_time)
  );
  const rawGroups = [];
  let current = null;
  for (const seg of sorted) {
    const text = (seg.text || "").trim();
    if (!text) continue;
    const key = getGroupKey(seg);
    if (current && current.key === key) {
      current.segments.push(seg);
    } else {
      if (current) rawGroups.push(current);
      current = { key, segments: [seg] };
    }
  }
  if (current) rawGroups.push(current);
  const groups = [];
  for (const raw of rawGroups) {
    if (raw.segments.length === 0) continue;
    let chunkSegments = [];
    let chunkText = "";
    const flushChunk = () => {
      if (chunkSegments.length === 0) return;
      const first = chunkSegments[0];
      const last = chunkSegments[chunkSegments.length - 1];
      groups.push({
        key: raw.key,
        startTime: first.absolute_start_time,
        endTime: last.absolute_end_time || last.absolute_start_time,
        startTimeSeconds: first.start_time ?? 0,
        endTimeSeconds: last.end_time ?? 0,
        combinedText: chunkText.trim(),
        segments: chunkSegments
      });
      chunkSegments = [];
      chunkText = "";
    };
    for (const seg of raw.segments) {
      const segText = (seg.text || "").trim();
      if (!segText) continue;
      const candidate = chunkText ? `${chunkText} ${segText}` : segText;
      if (chunkSegments.length > 0 && candidate.length > maxChars) {
        flushChunk();
      }
      chunkSegments.push(seg);
      chunkText = chunkText ? `${chunkText} ${segText}` : segText;
    }
    flushChunk();
  }
  return groups;
}

// src/state.ts
function createTranscriptState() {
  return { confirmed: /* @__PURE__ */ new Map(), pendingBySpeaker: /* @__PURE__ */ new Map() };
}
function bootstrapConfirmed(state, segments) {
  state.confirmed.clear();
  state.pendingBySpeaker.clear();
  for (const seg of segments) {
    if (!seg.absolute_start_time || !(seg.text || "").trim()) continue;
    const key = getSegmentIdentityKey(seg);
    const existing = state.confirmed.get(key);
    if (shouldReplaceSegment(existing, seg)) {
      state.confirmed.set(key, { ...existing, ...seg });
    }
  }
  return recomputeTranscripts(state);
}
function applyTranscriptTick(state, confirmed, pending, speaker) {
  let changed = false;
  for (const seg of confirmed) {
    if (!seg.absolute_start_time || !(seg.text || "").trim()) continue;
    const key = getSegmentIdentityKey(seg);
    const existing = state.confirmed.get(key);
    if (shouldReplaceSegment(existing, seg)) {
      state.confirmed.set(key, { ...existing, ...seg });
      changed = true;
    }
  }
  if (speaker !== void 0 && speaker !== null) {
    const validPending = (pending || []).filter(
      (s) => s.absolute_start_time && (s.text || "").trim()
    );
    if (validPending.length > 0) {
      state.pendingBySpeaker.set(speaker, validPending);
    } else {
      state.pendingBySpeaker.delete(speaker);
    }
    changed = true;
  }
  if (!changed) return null;
  return recomputeTranscripts(state);
}
function recomputeTranscripts(state) {
  const confirmedBySpeaker = /* @__PURE__ */ new Map();
  for (const seg of state.confirmed.values()) {
    const speaker = seg.speaker || "";
    if (!confirmedBySpeaker.has(speaker)) confirmedBySpeaker.set(speaker, /* @__PURE__ */ new Set());
    confirmedBySpeaker.get(speaker).add((seg.text || "").trim());
  }
  const all = [...state.confirmed.values()];
  for (const [speaker, segs] of state.pendingBySpeaker) {
    const confirmedTexts = confirmedBySpeaker.get(speaker);
    for (const seg of segs) {
      const pt = (seg.text || "").trim();
      let isStale = false;
      if (confirmedTexts) {
        for (const ct of confirmedTexts) {
          if (pt === ct || pt.startsWith(ct) || ct.startsWith(pt)) {
            isStale = true;
            break;
          }
        }
      }
      if (isStale) continue;
      all.push(seg);
    }
  }
  all.sort((a, b) => a.absolute_start_time.localeCompare(b.absolute_start_time));
  return all;
}
function addSegment(segments, segment) {
  const key = getSegmentIdentityKey(segment);
  const existingIndex = segments.findIndex((t) => getSegmentIdentityKey(t) === key);
  let updated;
  if (existingIndex !== -1) {
    const existing = segments[existingIndex];
    if (!shouldReplaceSegment(existing, segment)) return [...segments];
    updated = [...segments];
    updated[existingIndex] = { ...existing, ...segment };
  } else {
    updated = [...segments, segment];
    if (segment.completed && segment.speaker) {
      const segStart = segment.start_time ?? 0;
      const segEnd = segment.end_time ?? segStart;
      updated = updated.filter((t) => {
        if (t === segment) return true;
        if (t.completed) return true;
        if (t.speaker !== segment.speaker) return true;
        const tStart = t.start_time ?? 0;
        const tEnd = t.end_time ?? tStart;
        const overlaps = tStart < segEnd && tEnd > segStart;
        return !overlaps;
      });
    }
  }
  return updated;
}
function bootstrapSegments(segments) {
  const valid = segments.filter(
    (seg) => seg.absolute_start_time && (seg.text || "").trim()
  );
  const map = /* @__PURE__ */ new Map();
  for (const seg of valid) {
    const key = getSegmentIdentityKey(seg);
    const existing = map.get(key);
    if (shouldReplaceSegment(existing, seg)) {
      map.set(key, { ...existing, ...seg });
    }
  }
  return Array.from(map.values());
}

// src/manager.ts
function createTranscriptManager() {
  let state = createTranscriptState();
  function finalize(segments) {
    return sortByStartTime(deduplicateSegments(sortSegments(deduplicateByIdentity(segments))));
  }
  return {
    bootstrap(segments) {
      return finalize(bootstrapConfirmed(state, segments));
    },
    handleMessage(message) {
      if (message.type !== "transcript") return null;
      const confirmed = message.confirmed || [];
      const pending = message.pending || [];
      const speaker = message.speaker ?? void 0;
      const result = applyTranscriptTick(state, confirmed, pending, speaker);
      return result ? finalize(result) : null;
    },
    getSegments() {
      return finalize(recomputeTranscripts(state));
    },
    getState() {
      return state;
    },
    clear() {
      state = createTranscriptState();
    }
  };
}
export {
  addSegment,
  applyTranscriptTick,
  bootstrapConfirmed,
  bootstrapSegments,
  createTranscriptManager,
  createTranscriptState,
  deduplicateByIdentity,
  deduplicateSegments,
  getSegmentIdentityKey,
  groupSegments,
  parseUTCTimestamp,
  recomputeTranscripts,
  shouldReplaceSegment,
  sortByStartTime,
  sortSegments,
  upsertSegments
};

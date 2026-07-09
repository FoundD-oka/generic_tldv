/**
 * Browser context utilities and services
 * These classes run inside page.evaluate() browser context
 */

/**
 * Generate UUID for browser context
 */
export function generateBrowserUUID(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  } else {
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(
      /[xy]/g,
      function (c) {
        var r = (Math.random() * 16) | 0,
          v = c == "x" ? r : (r & 0x3) | 0x8;
        return v.toString(16);
      }
    );
  }
}

/**
 * Browser-compatible AudioService for browser context
 */
export class BrowserAudioService {
  private config: any;
  private processor: any = null;
  private audioContext: AudioContext | null = null;
  private destinationNode: MediaStreamAudioDestinationNode | null = null;

  constructor(config: any) {
    this.config = config;
  }

  async findMediaElements(retries: number = 10, delay: number = 3000): Promise<HTMLMediaElement[]> {
    for (let i = 0; i < retries; i++) {
      // Get all media elements
      const allMediaElements = Array.from(document.querySelectorAll("audio, video")) as HTMLMediaElement[];
      (window as any).logBot(`[Audio] Attempt ${i + 1}/${retries}: Found ${allMediaElements.length} total media elements in DOM`);
      
      // Filter for active media elements with proper checks
      const mediaElements = allMediaElements.filter((el: any) => {
        // Check if element has srcObject
        if (!el.srcObject) {
          return false;
        }
        
        // Check if srcObject is a MediaStream
        if (!(el.srcObject instanceof MediaStream)) {
          return false;
        }
        
        // Check if MediaStream has audio tracks
        const audioTracks = el.srcObject.getAudioTracks();
        if (audioTracks.length === 0) {
          return false;
        }
        
        // Check if element is not paused (like Node.js version)
        if (el.paused) {
          (window as any).logBot(`[Audio] Element found but is paused (readyState: ${el.readyState})`);
          return false;
        }
        
        // Check readyState - prefer elements that have loaded metadata or more
        // 0 = HAVE_NOTHING, 1 = HAVE_METADATA, 2 = HAVE_CURRENT_DATA, 3 = HAVE_FUTURE_DATA, 4 = HAVE_ENOUGH_DATA
        if (el.readyState < 1) {
          (window as any).logBot(`[Audio] Element found but readyState is ${el.readyState} (HAVE_NOTHING)`);
          return false;
        }
        
        // Check if audio tracks are enabled.
        // Note: track.muted is a read-only WebRTC property set by the remote end.
        // Teams initially delivers tracks with muted=true until someone speaks.
        // We accept tracks that are enabled (even if muted) — the ScriptProcessor
        // will get silence until actual audio flows, which is fine.
        const hasEnabledTracks = audioTracks.some((track: MediaStreamTrack) => track.enabled);
        if (!hasEnabledTracks) {
          (window as any).logBot(`[Audio] Element found but all audio tracks are disabled`);
          return false;
        }
        
        return true;
      });

      if (mediaElements.length > 0) {
        (window as any).logBot(`✅ Found ${mediaElements.length} active media elements with audio tracks after ${i + 1} attempt(s).`);
        // Log details about found elements
        mediaElements.forEach((el: any, idx: number) => {
          const tracks = el.srcObject.getAudioTracks();
          (window as any).logBot(`  Element ${idx + 1}: paused=${el.paused}, readyState=${el.readyState}, tracks=${tracks.length}, enabled=${tracks.filter((t: MediaStreamTrack) => t.enabled).length}`);
        });
        return mediaElements;
      }
      
      // Enhanced diagnostic logging
      if (allMediaElements.length > 0) {
        (window as any).logBot(`[Audio] Found ${allMediaElements.length} media elements but none are active. Details:`);
        allMediaElements.forEach((el: any, idx: number) => {
          const hasSrcObject = !!el.srcObject;
          const isMediaStream = el.srcObject instanceof MediaStream;
          const audioTracks = isMediaStream ? el.srcObject.getAudioTracks().length : 0;
          (window as any).logBot(`  Element ${idx + 1}: paused=${el.paused}, readyState=${el.readyState}, hasSrcObject=${hasSrcObject}, isMediaStream=${isMediaStream}, audioTracks=${audioTracks}`);
        });
      } else {
        (window as any).logBot(`[Audio] No media elements found in DOM at all`);
      }
      
      (window as any).logBot(`[Audio] Retrying in ${delay}ms... (Attempt ${i + 2}/${retries})`);
      await new Promise(resolve => setTimeout(resolve, delay));
    }
    
    (window as any).logBot(`❌ No active media elements found after ${retries} attempts`);
    return [];
  }

  async createCombinedAudioStream(mediaElements: HTMLMediaElement[]): Promise<MediaStream> {
    if (mediaElements.length === 0) {
      throw new Error("No media elements provided for audio stream creation");
    }

    (window as any).logBot(`Found ${mediaElements.length} active media elements.`);
    if (!this.audioContext) {
      this.audioContext = new AudioContext();
    }
    if (!this.destinationNode) {
      this.destinationNode = this.audioContext.createMediaStreamDestination();
    }
    let sourcesConnected = 0;

    // Connect all media elements to the destination node
    mediaElements.forEach((element: any, index: number) => {
      try {
        // Ensure element is actually audible
        if (typeof element.muted === "boolean") element.muted = false;
        if (typeof element.volume === "number") element.volume = 1.0;
        if (typeof element.play === "function") {
          element.play().catch(() => {});
        }

        const elementStream =
          element.srcObject ||
          (element.captureStream && element.captureStream()) ||
          (element.mozCaptureStream && element.mozCaptureStream());

        // Debug audio tracks and unmute them
        if (elementStream instanceof MediaStream) {
          const audioTracks = elementStream.getAudioTracks();
          (window as any).logBot(`Element ${index + 1}: Found ${audioTracks.length} audio tracks`);
          audioTracks.forEach((track, trackIndex) => {
            (window as any).logBot(`  Track ${trackIndex}: enabled=${track.enabled}, muted=${track.muted}, label=${track.label}`);
            
            // Unmute muted audio tracks
            if (track.muted) {
              track.enabled = true;
              // Force unmute by setting muted to false
              try {
                (track as any).muted = false;
                (window as any).logBot(`  Unmuted track ${trackIndex} (enabled=${track.enabled}, muted=${track.muted})`);
              } catch (e: unknown) {
                const message = e instanceof Error ? e.message : String(e);
                (window as any).logBot(`  Could not unmute track ${trackIndex}: ${message}`);
              }
            }
          });
        }

        if (
          elementStream instanceof MediaStream &&
          elementStream.getAudioTracks().length > 0
        ) {
          // Connect regardless of the read-only muted flag; WebAudio can still pull samples
          const sourceNode = this.audioContext!.createMediaStreamSource(elementStream);
          sourceNode.connect(this.destinationNode!);
          sourcesConnected++;
          (window as any).logBot(`Connected audio stream from element ${index + 1}/${mediaElements.length}. Tracks=${elementStream.getAudioTracks().length}`);
        } else {
          (window as any).logBot(`Skipping element ${index + 1}: No audio tracks found`);
        }
      } catch (error: any) {
        (window as any).logBot(`Could not connect element ${index + 1}: ${error.message}`);
      }
    });

    if (sourcesConnected === 0) {
      throw new Error("Could not connect any audio streams. Check media permissions.");
    }

    (window as any).logBot(`Successfully combined ${sourcesConnected} audio streams.`);
    return this.destinationNode!.stream;
  }

  async initializeAudioProcessor(combinedStream: MediaStream): Promise<any> {
    // Reuse existing context if available
    if (!this.audioContext) {
      this.audioContext = new AudioContext();
    }
    if (!this.destinationNode) {
      this.destinationNode = this.audioContext.createMediaStreamDestination();
    }

    const mediaStream = this.audioContext.createMediaStreamSource(combinedStream);
    const recorder = this.audioContext.createScriptProcessor(
      this.config.bufferSize,
      this.config.inputChannels,
      this.config.outputChannels
    );
    const gainNode = this.audioContext.createGain();
    gainNode.gain.value = 0; // Silent playback

    // Connect the audio processing pipeline
    mediaStream.connect(recorder);
    recorder.connect(gainNode);
    gainNode.connect(this.audioContext.destination);

    this.processor = {
      audioContext: this.audioContext,
      destinationNode: this.destinationNode,
      recorder,
      mediaStream,
      gainNode,
      sessionAudioStartTimeMs: null
    };

    try { await this.audioContext.resume(); } catch {}
    (window as any).logBot("Audio processing pipeline connected and ready.");
    return this.processor;
  }

  setupAudioDataProcessor(onAudioData: (audioData: Float32Array, sessionStartTime: number | null) => void): void {
    if (!this.processor) {
      throw new Error("Audio processor not initialized");
    }

    this.processor.recorder.onaudioprocess = async (event: any) => {
      // Set session start time on first audio chunk
      if (this.processor!.sessionAudioStartTimeMs === null) {
        this.processor!.sessionAudioStartTimeMs = Date.now();
        (window as any).logBot(`[Audio] Session audio start time set: ${this.processor!.sessionAudioStartTimeMs}`);
      }

      const inputData = event.inputBuffer.getChannelData(0);
      const resampledData = this.resampleAudioData(inputData, this.processor!.audioContext.sampleRate);
      
      onAudioData(resampledData, this.processor!.sessionAudioStartTimeMs);
    };
  }

  private resampleAudioData(inputData: Float32Array, sourceSampleRate: number): Float32Array {
    const targetLength = Math.round(
      inputData.length * (this.config.targetSampleRate / sourceSampleRate)
    );
    const resampledData = new Float32Array(targetLength);
    const springFactor = (inputData.length - 1) / (targetLength - 1);
    
    resampledData[0] = inputData[0];
    resampledData[targetLength - 1] = inputData[inputData.length - 1];
    
    for (let i = 1; i < targetLength - 1; i++) {
      const index = i * springFactor;
      const leftIndex = Math.floor(index);
      const rightIndex = Math.ceil(index);
      const fraction = index - leftIndex;
      resampledData[i] =
        inputData[leftIndex] +
        (inputData[rightIndex] - inputData[leftIndex]) * fraction;
    }
    
    return resampledData;
  }

  getSessionAudioStartTime(): number | null {
    return this.processor?.sessionAudioStartTimeMs || null;
  }

  resetSessionStartTime(): void {
    if (this.processor) {
      const oldTime = this.processor.sessionAudioStartTimeMs;
      this.processor.sessionAudioStartTimeMs = null;
      (window as any).logBot(`[Audio] Reset session audio start time: ${oldTime} -> null (will be set on next audio chunk)`);
    }
  }

  disconnect(): void {
    if (this.processor) {
      try {
        this.processor.recorder.disconnect();
        this.processor.mediaStream.disconnect();
        this.processor.gainNode.disconnect();
        this.processor.audioContext.close();
        (window as any).logBot("Audio processing pipeline disconnected.");
      } catch (error: any) {
        (window as any).logBot(`Error disconnecting audio pipeline: ${error.message}`);
      }
      this.processor = null;
    }
  }
}

/**
 * BrowserMediaRecorderPipeline — Pack U.2 (v0.10.6) shared MediaRecorder driver.
 *
 * Runs in browser context. Wraps a MediaRecorder over a combined audio
 * MediaStream, encodes each chunk to base64, and pushes it to the Node-side
 * `__vexaSaveRecordingChunk` callback exposed by services/audio-pipeline.ts:
 * MediaRecorderCapture. Replaces the inline ondataavailable + chunk-buffer
 * boilerplate that previously lived in googlemeet/recording.ts and
 * msteams/recording.ts.
 *
 * Pack M discipline survives here: the in-flight chunk buffer is short-lived
 * (we splice on callback resolution; the cap is purely defensive). No bot-side
 * master assembly — the master is built server-side from the chunk_seq
 * sequence by recording_finalizer.py.
 *
 * Pack P / develop stage rule (no fallbacks): if the chunkCallback throws or
 * returns false, we splice the chunk anyway and log; the server-side
 * reconciler covers re-fetch via the chunk_seq contract. If MediaRecorder
 * cannot be constructed (no supported mimeType), we log and refuse to start —
 * no silent fallback to a different recording mechanism.
 *
 * Lifecycle:
 *   const pipeline = new BrowserMediaRecorderPipeline({ stream, timesliceMs, chunkCallback });
 *   await pipeline.start();   // creates MediaRecorder, calls .start(timesliceMs)
 *   // ... meeting runs, chunks flow on each timeslice ...
 *   await pipeline.stop();    // resolves AFTER the final chunk callback completes
 */
export interface BrowserMediaRecorderPipelineOptions {
  /** Combined audio stream (typically from BrowserAudioService.createCombinedAudioStream). */
  stream: MediaStream;
  /** MediaRecorder timeslice in ms. */
  timesliceMs: number;
  /**
   * Bridge to the Node-side __vexaSaveRecordingChunk callback. Returns the
   * Node side's success indicator (Boolean — true if uploaded, false if the
   * sink rejected). We splice the chunk regardless of return value because
   * the server-side reconciler covers re-fetch.
   */
  chunkCallback: (payload: {
    base64: string;
    chunkSeq: number;
    isFinal: boolean;
    mimeType: string;
  }) => Promise<boolean>;
}

export class BrowserMediaRecorderPipeline {
  private opts: BrowserMediaRecorderPipelineOptions;
  private recorder: MediaRecorder | null = null;
  private chunkSeq: number = 0;
  private pending: Blob[] = [];
  private static readonly BUFFER_CAP = 10;
  private finalChunkPromise: Promise<void> | null = null;
  private resolveFinalChunk: (() => void) | null = null;
  private mimeType: string = "audio/webm";
  /**
   * BUG-005 fix: ondataavailable's async work (arrayBuffer + base64 encode +
   * chunkCallback) can still be in flight when onstop fires — track every
   * in-flight chunk task here so onstop can await them before resolving
   * finalChunkPromise. Without this, stop() can resolve while the last data
   * chunk is still on its way to the Node side.
   */
  private pendingChunkTasks: Set<Promise<void>> = new Set();

  constructor(opts: BrowserMediaRecorderPipelineOptions) {
    this.opts = opts;
  }

  /** Returns the underlying MediaRecorder (null until start()). */
  getMediaRecorder(): MediaRecorder | null {
    return this.recorder;
  }

  async start(): Promise<void> {
    if (this.recorder) {
      (window as any).logBot?.("[BrowserMediaRecorderPipeline] start() called twice — ignoring");
      return;
    }

    // Pick the best supported MediaRecorder mimeType. No fallback beyond the
    // candidate list — if none of these work, we log and refuse.
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
      "audio/ogg",
    ];
    let chosen = "";
    for (const mime of candidates) {
      try {
        if ((window as any).MediaRecorder?.isTypeSupported?.(mime)) {
          chosen = mime;
          break;
        }
      } catch {}
    }

    let recorder: MediaRecorder;
    try {
      recorder = chosen
        ? new MediaRecorder(this.opts.stream, { mimeType: chosen })
        : new MediaRecorder(this.opts.stream);
    } catch (err: any) {
      (window as any).logBot?.(
        `[BrowserMediaRecorderPipeline] Failed to construct MediaRecorder: ${err?.message || err}`
      );
      return;
    }

    this.recorder = recorder;
    this.mimeType = recorder.mimeType || chosen || "audio/webm";

    recorder.ondataavailable = (event: BlobEvent) => {
      if (!(event.data && event.data.size > 0)) {
        (window as any).logBot?.("[BrowserMediaRecorderPipeline] dataavailable fired with empty data (skipping)");
        return;
      }

      // Push to defensive buffer + cap-check. The cap should never trip in
      // normal operation because successful uploads splice. If it does, we
      // drop the oldest and log — server-side reconciler covers re-fetch
      // from S3 via the chunk_seq contract.
      this.pending.push(event.data);
      if (this.pending.length > BrowserMediaRecorderPipeline.BUFFER_CAP) {
        const dropped = this.pending.shift();
        (window as any).logBot?.(
          `[BrowserMediaRecorderPipeline] WARN buffer exceeded cap ${BrowserMediaRecorderPipeline.BUFFER_CAP}, dropped oldest (${dropped?.size ?? 0} bytes); reconciler will re-fetch from S3`
        );
      }

      const seq = this.chunkSeq;
      this.chunkSeq = seq + 1;
      const data = event.data;

      // BUG-005 fix: this async work (arrayBuffer + base64 encode + the
      // chunkCallback round-trip) is registered in pendingChunkTasks so
      // onstop can await it before resolving finalChunkPromise — otherwise
      // stop() can resolve while this chunk is still in flight to Node.
      const task = (async () => {
        try {
          const arrBuffer = await data.arrayBuffer();
          const bytes = new Uint8Array(arrBuffer);
          let binary = "";
          const encodeChunkSize = 0x8000;
          for (let i = 0; i < bytes.length; i += encodeChunkSize) {
            binary += String.fromCharCode(...bytes.subarray(i, i + encodeChunkSize));
          }
          const base64 = btoa(binary);

          (window as any).logBot?.(
            `[BrowserMediaRecorderPipeline] Uploading chunk ${seq} (${bytes.length} bytes)`
          );

          try {
            const ok = await this.opts.chunkCallback({
              base64,
              chunkSeq: seq,
              isFinal: false,
              mimeType: this.mimeType,
            });
            if (!ok) {
              (window as any).logBot?.(
                `[BrowserMediaRecorderPipeline] Chunk ${seq} callback returned false — sink rejected; reconciler will re-fetch`
              );
            }
          } catch (cbErr: any) {
            (window as any).logBot?.(
              `[BrowserMediaRecorderPipeline] Chunk ${seq} callback threw: ${cbErr?.message || cbErr}; reconciler will re-fetch`
            );
          } finally {
            // Splice the chunk regardless of callback outcome — see Pack M.
            const idx = this.pending.indexOf(data);
            if (idx >= 0) this.pending.splice(idx, 1);
          }
        } catch (err: any) {
          // Splice on encode failure too — chunk is unrecoverable.
          const idx = this.pending.indexOf(data);
          if (idx >= 0) this.pending.splice(idx, 1);
          (window as any).logBot?.(
            `[BrowserMediaRecorderPipeline] Chunk ${seq} encode FAILED: ${err?.message || err}; spliced from buffer`
          );
        }
      })();
      this.pendingChunkTasks.add(task);
      task.finally(() => {
        this.pendingChunkTasks.delete(task);
      });
    };

    recorder.onstop = async () => {
      // BUG-005 fix: wait for every in-flight ondataavailable task (chunks
      // still encoding/uploading) before emitting the final chunk, so
      // stop() cannot resolve while the last real data chunk is still on
      // its way to the Node side.
      if (this.pendingChunkTasks.size > 0) {
        (window as any).logBot?.(
          `[BrowserMediaRecorderPipeline] onstop waiting for ${this.pendingChunkTasks.size} in-flight chunk task(s)`
        );
        await Promise.allSettled(Array.from(this.pendingChunkTasks));
      }
      // Emit a final chunk (empty body OK — server treats isFinal=true as the
      // signal to flip Recording.status, regardless of payload size).
      try {
        const finalSeq = this.chunkSeq;
        this.chunkSeq = finalSeq + 1;
        await this.opts.chunkCallback({
          base64: "",
          chunkSeq: finalSeq,
          isFinal: true,
          mimeType: this.mimeType,
        });
        (window as any).logBot?.(
          `[BrowserMediaRecorderPipeline] Final chunk emitted (seq=${finalSeq})`
        );
      } catch (err: any) {
        (window as any).logBot?.(
          `[BrowserMediaRecorderPipeline] Final chunk callback failed: ${err?.message || err}`
        );
      } finally {
        if (this.resolveFinalChunk) {
          this.resolveFinalChunk();
          this.resolveFinalChunk = null;
        }
      }
    };

    recorder.start(this.opts.timesliceMs);
    (window as any).logBot?.(
      `[BrowserMediaRecorderPipeline] MediaRecorder started (${this.mimeType}, timeslice=${this.opts.timesliceMs}ms)`
    );
  }

  async stop(): Promise<void> {
    if (!this.recorder) {
      (window as any).logBot?.("[BrowserMediaRecorderPipeline] stop() called before start() — ignoring");
      return;
    }
    if (this.recorder.state === "inactive") {
      (window as any).logBot?.("[BrowserMediaRecorderPipeline] recorder already inactive");
      return;
    }

    this.finalChunkPromise = new Promise<void>((resolve) => {
      this.resolveFinalChunk = resolve;
      // Safety timeout — onstop must fire within 10s of stop(), else we
      // resolve so the bot can exit.
      setTimeout(() => {
        if (this.resolveFinalChunk) {
          (window as any).logBot?.("[BrowserMediaRecorderPipeline] final chunk timeout — resolving");
          this.resolveFinalChunk();
          this.resolveFinalChunk = null;
        }
      }, 10000);
    });

    try {
      this.recorder.stop();
    } catch (err: any) {
      (window as any).logBot?.(
        `[BrowserMediaRecorderPipeline] recorder.stop() threw: ${err?.message || err}`
      );
    }

    await this.finalChunkPromise;
  }
}



// ---------------------------------------------------------------------------
// BrowserLaneRecorderManager — Issue #25 (Phase 2 audio lanes)
//
// Records each participant tile's media element to its OWN MediaRecorder
// pipeline, in parallel with the combined (mixed-master) pipeline above.
// Lane identity is keyed by MediaStreamTrack.id — NOT the DOM tile — so a
// tile disappearing during screen-share/presentation does not kill the lane
// while its track is still alive (track 'ended' is the stop signal).
//
// Lane chunks flow to the Node side via the laneChunkCallback bridge
// (__vexaSaveLaneChunk) with per-lane identity; the server stores them under
// media_type "lane-{laneKey}" — structurally outside the mixed master's
// /audio/ prefix.
// ---------------------------------------------------------------------------

export interface LaneChunkPayload {
  laneKey: string;
  laneId: string;
  laneLabel: string | null;
  laneIdSource: string;
  /**
   * BUG-002 fix: ms between the mixed recording's start (recordingStartedAtMs)
   * and this lane's own recorder start. null when recordingStartedAtMs was not
   * supplied (caller cannot align lane-relative timestamps in that case).
   */
  laneStartOffsetMs: number | null;
  base64: string;
  chunkSeq: number;
  isFinal: boolean;
  mimeType: string;
}

export interface BrowserLaneRecorderManagerOptions {
  timesliceMs: number;
  /** Hard cap on concurrently recorded lanes (cost control). */
  maxLanes: number;
  /** Bridge to Node's __vexaSaveLaneChunk. */
  laneChunkCallback: (payload: LaneChunkPayload) => Promise<boolean>;
  /** Rescan cadence for late joiners / recreated elements. */
  rescanIntervalMs?: number;
  /**
   * BUG-002 fix: Date.now() captured right after the combined (mixed-master)
   * pipeline starts. Each lane computes its start offset from this value so
   * lane-relative segment timestamps can be re-aligned to the mixed master's
   * timeline server-side.
   */
  recordingStartedAtMs?: number;
}

interface LaneEntry {
  laneKey: string;
  pipeline: BrowserMediaRecorderPipeline;
  track: MediaStreamTrack;
  /**
   * BUG-020: mutable so a rescan can resolve/attach a label that was null
   * at lane start (label is re-checked on every rescan while still null).
   */
  label: string | null;
  labelSource: string;
}

async function sha1Hex(input: string): Promise<string> {
  const data = new TextEncoder().encode(input);
  const digest = await crypto.subtle.digest("SHA-1", data);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export class BrowserLaneRecorderManager {
  private opts: BrowserLaneRecorderManagerOptions;
  /** track.id → lane */
  private lanes = new Map<string, LaneEntry>();
  private skippedOverCap = new Set<string>();
  private rescanTimer: number | null = null;
  private stopped = false;
  /**
   * BUG-008 fix: stopAll() must not snapshot this.lanes while a scan is
   * still in flight (a scan can be suspended at an await and later add a
   * lane stopAll never sees). Tracks the currently-running scan so stopAll
   * can await it before taking its snapshot.
   */
  private scanInFlight: Promise<void> | null = null;
  /**
   * BUG-007 fix: elements we've already called captureStream() on. Per the
   * MediaElement capture spec, captureStream() returns a NEW MediaStream
   * (fresh track ids) on every call, so calling it again on every rescan
   * for a srcObject-less element would start a brand-new lane every
   * rescanIntervalMs until maxLanes is exhausted. We call captureStream()
   * at most once per element; this WeakSet gates ONLY that fallback path —
   * it must never gate the srcObject scan path (BUG-020(b): a srcObject
   * identity change on an already-claimed element must still be able to
   * start a lane for the new track).
   */
  private capturedElements = new WeakSet<HTMLMediaElement>();

  constructor(opts: BrowserLaneRecorderManagerOptions) {
    this.opts = opts;
  }

  /** Number of currently recording lanes. */
  laneCount(): number {
    return this.lanes.size;
  }

  async start(initialElements: HTMLMediaElement[]): Promise<void> {
    await this.runScan(initialElements);
    const interval = this.opts.rescanIntervalMs ?? 15000;
    this.rescanTimer = window.setInterval(() => {
      if (this.stopped) return;
      const els = Array.from(
        document.querySelectorAll("audio, video")
      ) as HTMLMediaElement[];
      this.runScan(els).catch((err: any) => {
        (window as any).logBot?.(
          `[LaneRecorder] rescan failed: ${err?.message || err}`
        );
      });
    }, interval);
  }

  /** Wraps scan() so stopAll() can await any in-flight pass (BUG-008). */
  private runScan(elements: HTMLMediaElement[]): Promise<void> {
    const p = this.scan(elements);
    this.scanInFlight = p;
    const clear = () => {
      if (this.scanInFlight === p) this.scanInFlight = null;
    };
    p.then(clear, clear);
    return p;
  }

  private laneLabelForElement(el: HTMLMediaElement): { label: string | null; source: string } {
    // Best-effort: the audio element may live inside (or near) a participant
    // tile. When it does not, the lane still records — the deferred stage
    // simply cannot auto-confirm it from a DOM name.
    //
    // BUG-020 known limitation: in Google Meet, remote audio is commonly
    // played through detached <audio> elements that are NOT descendants of
    // a participant tile (the combined pipeline's findMediaElements() scans
    // ALL media elements for srcObject with no tile-ancestry requirement,
    // for the same reason). When that holds, this returns label=null for
    // most/all lanes and the deferred stage's solo-lane auto-confirm never
    // fires — the lane falls back to the DOM-vote name instead. Full
    // track→participant correlation (matching Meet's internal participant
    // stream mapping) is out of scope here; see BUG-020 fix_suggestion /
    // Phase 3 for that work. scan() re-resolves this on every rescan while
    // the lane's stored label is still null, so a label becoming
    // available later (tile mounts, DOM settles) is not permanently missed.
    const tile = el.closest?.("[data-participant-id]") as HTMLElement | null;
    if (tile) {
      const name = tile.querySelector?.("span.notranslate")?.textContent?.trim();
      if (name) return { label: name, source: "participant-id" };
      const aria = tile.getAttribute("aria-label")?.trim();
      if (aria) return { label: aria, source: "participant-id" };
    }
    return { label: null, source: "stream" };
  }

  private async scan(elements: HTMLMediaElement[]): Promise<void> {
    if (this.stopped) return;
    for (const el of elements) {
      if (this.stopped) return; // BUG-008: bail mid-pass once stopAll() ran

      let stream: MediaStream | null = null;
      try {
        if (el.srcObject instanceof MediaStream) {
          stream = el.srcObject;
        } else if (!this.capturedElements.has(el) && (el as any).captureStream) {
          // BUG-007: captureStream() fallback runs AT MOST ONCE per element
          // (see capturedElements doc above) — never on a rescan.
          this.capturedElements.add(el);
          stream = (el as any).captureStream() || null;
        }
      } catch {
        stream = null;
      }
      if (!(stream instanceof MediaStream)) continue;

      for (const track of stream.getAudioTracks()) {
        if (this.stopped) return; // BUG-008
        if (track.readyState === "ended") continue;

        const existing = this.lanes.get(track.id);
        if (existing) {
          // BUG-020: re-resolve the label on each rescan while it's still
          // null so a lane that started before its tile/name was available
          // eventually picks one up; later chunks carry the updated label
          // (the server's metadata-inherit handles persistence).
          if (existing.label === null) {
            const resolved = this.laneLabelForElement(el);
            if (resolved.label) {
              existing.label = resolved.label;
              existing.labelSource = resolved.source;
              (window as any).logBot?.(
                `[LaneRecorder] resolved label on rescan laneKey=${existing.laneKey} label=${resolved.label}`
              );
            }
          }
          continue;
        }

        if (this.lanes.size >= this.opts.maxLanes) {
          if (!this.skippedOverCap.has(track.id)) {
            this.skippedOverCap.add(track.id);
            (window as any).logBot?.(
              `[LaneRecorder] WARN max lanes (${this.opts.maxLanes}) reached — ` +
              `not recording track ${track.id}`
            );
          }
          continue;
        }

        // BUG-021: a single track's lane failing to start must not abort
        // the rest of this scan pass — log and continue with the remaining
        // elements/tracks.
        try {
          await this.startLane(el, stream, track);
        } catch (err: any) {
          (window as any).logBot?.(
            `[LaneRecorder] startLane failed track=${track.id}: ${err?.message || err}`
          );
        }
      }
    }
  }

  private async startLane(
    el: HTMLMediaElement,
    stream: MediaStream,
    track: MediaStreamTrack
  ): Promise<void> {
    if (this.stopped) return; // BUG-008: bail before constructing anything

    const laneKey = (await sha1Hex(track.id)).slice(0, 10);
    const { label, source } = this.laneLabelForElement(el);
    const laneStream = new MediaStream([track]);
    // BUG-002: fixed offset captured once at this lane's recorder start —
    // ms between the mixed recording's start and this lane starting. The
    // server uses it to re-align this lane's lane-relative segment
    // timestamps onto the mixed master's timeline.
    const laneStartOffsetMs =
      typeof this.opts.recordingStartedAtMs === "number"
        ? Date.now() - this.opts.recordingStartedAtMs
        : null;

    const pipeline = new BrowserMediaRecorderPipeline({
      stream: laneStream,
      timesliceMs: this.opts.timesliceMs,
      chunkCallback: (p) => {
        // Read the current (possibly rescan-updated, BUG-020) label from the
        // lane entry rather than the label captured at start time.
        const entry = this.lanes.get(track.id);
        return this.opts.laneChunkCallback({
          laneKey,
          laneId: track.id,
          laneLabel: entry ? entry.label : label,
          laneIdSource: entry ? entry.labelSource : source,
          laneStartOffsetMs,
          base64: p.base64,
          chunkSeq: p.chunkSeq,
          isFinal: p.isFinal,
          mimeType: p.mimeType,
        });
      },
    });

    this.lanes.set(track.id, { laneKey, pipeline, track, label, labelSource: source });
    (window as any).logBot?.(
      `[LaneRecorder] lane started laneKey=${laneKey} track=${track.id} ` +
      `label=${label ?? "(none)"} offsetMs=${laneStartOffsetMs ?? "(none)"} ` +
      `lanes=${this.lanes.size}/${this.opts.maxLanes}`
    );

    // Track death (participant left / stream torn down) ends the lane and
    // flushes its final chunk. New participantId/track ⇒ new lane by design.
    track.addEventListener("ended", () => {
      const lane = this.lanes.get(track.id);
      if (!lane) return;
      this.lanes.delete(track.id);
      lane.pipeline.stop().catch((err: any) => {
        (window as any).logBot?.(
          `[LaneRecorder] lane stop on track-ended failed laneKey=${laneKey}: ${err?.message || err}`
        );
      });
      (window as any).logBot?.(
        `[LaneRecorder] lane ended laneKey=${laneKey} (track ended)`
      );
    });

    // BUG-021: BrowserMediaRecorderPipeline.start() swallows MediaRecorder
    // construction failures (logs + returns, no throw), and
    // recorder.start() itself can throw outside that guard. Either way,
    // treat "no recorder after start()" as a start failure so the zombie
    // lane entry is removed and the track can be retried on the next scan.
    try {
      await pipeline.start();
      if (!pipeline.getMediaRecorder()) {
        throw new Error("pipeline.start() did not produce a MediaRecorder");
      }
    } catch (err: any) {
      this.lanes.delete(track.id);
      throw err;
    }
  }

  /** Stop every lane and flush final chunks. Called from stopBrowserCapture. */
  async stopAll(): Promise<void> {
    this.stopped = true;
    if (this.rescanTimer !== null) {
      window.clearInterval(this.rescanTimer);
      this.rescanTimer = null;
    }
    // BUG-008: wait for any in-flight scan to finish before snapshotting —
    // otherwise a scan suspended mid-pass can add a lane after we've
    // already cleared the map, and that lane is never stopped.
    if (this.scanInFlight) {
      await this.scanInFlight.catch(() => {});
    }
    const entries = Array.from(this.lanes.values());
    this.lanes.clear();
    await Promise.all(
      entries.map((lane) =>
        lane.pipeline.stop().catch((err: any) => {
          (window as any).logBot?.(
            `[LaneRecorder] lane stop failed laneKey=${lane.laneKey}: ${err?.message || err}`
          );
        })
      )
    );
  }
}

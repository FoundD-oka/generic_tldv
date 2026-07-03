# Kabosu Wake Orchestrator

Watches existing Vexa live transcript events and answers when meeting
participants call Kabosu anywhere in an utterance.

## Flow

```text
Vexa /bots/status
  -> discover dashboard-created running bots
  -> Vexa /ws transcript
  -> wake word: カボス
  -> Groq openai/gpt-oss-20b
  -> Aivis Cloud TTS
  -> Vexa /speak audio_base64
  -> meeting bot speaks
```

## Required Environment

`VEXA_API_KEY` must belong to the same dashboard user who creates the meeting
bots. Bot discovery uses the user-scoped `/bots/status` endpoint, so a key from
another user cannot see those bots.

```env
VEXA_API_URL=http://localhost:8056
VEXA_API_KEY=replace_me
VEXA_PLATFORM=google_meet
WAKE_AUTO_DISCOVER_BOTS=true
WAKE_DISCOVERY_INTERVAL_SECONDS=5
# Optional: set only when you want to pin Wake to one meeting.
VEXA_NATIVE_MEETING_ID=

GROQ_API_KEY=replace_me
GROQ_MODEL=openai/gpt-oss-20b
GROQ_MAX_COMPLETION_TOKENS=768
GROQ_RETRY_MAX_COMPLETION_TOKENS=1536

AIVIS_API_KEY=replace_me
AIVIS_MODEL_UUID=18972473-ca36-4e06-a33a-5cc14adba0c4
AIVIS_OUTPUT_FORMAT=wav
AIVIS_OUTPUT_SAMPLING_RATE=24000
AIVIS_LEADING_SILENCE_SECONDS=0.05
AIVIS_TRAILING_SILENCE_SECONDS=0.7
AIVIS_LINE_BREAK_SILENCE_SECONDS=0.2
WAKE_COOLDOWN_MS=0
WAKE_SAME_SPEAKER_DEDUPE_MS=0
WAKE_INPUT_SETTLE_MS=800
WAKE_MAX_INPUT_MS=2500
WAKE_STABILIZED_DUPLICATE_MS=20000
WAKE_ACK_ENABLED=true
WAKE_ACK_TEXT=うん！
WAKE_RESPONSE_PLAYBACK_GUARD_MS=1000
WAKE_SPEECH_EVENT_POLL_MS=1000
WAKE_USE_PENDING_TRANSCRIPTS=true
WAKE_USE_CONFIRMED_TRANSCRIPTS=false
WAKE_CHAT_ENABLED=true
WAKE_CHAT_BOOTSTRAP_ENABLED=true
WAKE_CHAT_RECENT_LIMIT=30
WAKE_CHAT_RECENT_MINUTES=15
WAKE_CHAT_SAME_TEXT_DEDUPE_MS=3000
WAKE_CHAT_MAX_MESSAGE_CHARS=1000
WAKE_CHAT_BOT_SENDER_NAMES=カボス,Kabosu,kabosu,Vexa Bot
WAKE_CHAT_EMPTY_PROMPT=呼ばれたよ。要約、整理、チャット内容の確認、次アクション出しができます。
BOT_ECHO_COOLDOWN_MS=2000
```

Default wake words:

```env
WAKE_WORDS=カボス,ねえカボス,カボスさん,かぼす,カボちゃん,カーブス
```

Meet chat is also a first-class input channel. REST chat bootstrap is kept as
context only, while real-time `chat.received` messages trigger Kabosu whenever
typed text contains `カボス`, `かぼす`, or `kabosu`. Bot/self messages,
duplicates, and short repeated same-text chat wakes are ignored to avoid loops.

The orchestrator sends Aivis audio to Vexa as `audio_base64`, not text, so the
meeting hears the configured Aivis voice. WAV is the default because it is more
stable for short live playback through the bot microphone path.

Wake detection uses mutable/pending transcript updates by default. Confirmed
segments remain a transcript/logging concern and are not used as wake triggers
unless `WAKE_USE_CONFIRMED_TRANSCRIPTS=true` is explicitly set.

When Kabosu is detected in a pending transcript, the orchestrator immediately
plays the bundled recorded `うん！` WAV acknowledgement, keeps accepting pending
updates for a short settle window, then sends the latest stabilized utterance to
Groq. The reply is synthesized with Aivis Cloud and sent to Vexa as
`audio_base64`.

While the acknowledgement, LLM request, synthesis, or answer playback is in
progress, new transcript events are ignored. Answer playback is guarded by the
bot's `speak.completed` / `speak.error` / `speak.interrupted` event for the
reply request id; the estimated audio duration is only used as a fail-safe
timeout if those events never arrive.

Duplicate suppression is intentionally narrow: the orchestrator ignores the
same transcript segment/time range and short-lived stabilized duplicates from
the same pending wake turn, but it does not use broad multi-minute text
deduplication across different wake attempts.

Dashboard-created bots must be created with `voice_agent_enabled: true` so Vexa
has a playback microphone path for `/speak`.

## Optional Low-Latency Lane

By default the orchestrator reads the existing Vexa transcript WebSocket. To use
the separate wake-stt lane, set `WAKE_STT_URL=http://wake-stt:8058`, configure
the wake-stt transcription URL/token, and start compose with both profiles:

```bash
docker compose --profile wake --profile wake-stt up -d wake-stt wake-orchestrator
```

Piper `tts-service` is not needed for the Kabosu wake path because replies are
synthesized with Aivis Cloud and sent as `audio_base64`. Enable the compose
profile `tts` only when you explicitly need text `/speak` through Piper.

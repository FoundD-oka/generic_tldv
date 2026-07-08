# golden-2-diarization — request

Deferred (post-meeting) transcription request routed to the Soniox async
adapter by model name:

```bash
curl -X POST "$TRANSCRIPTION_SERVICE_URL" \
  -H "Authorization: Bearer $TRANSCRIPTION_SERVICE_TOKEN" \
  -H "X-Transcription-Tier: deferred" \
  -F "file=@recording.wav" \
  -F "model=stt-async-v5" \
  -F "language=ja" \
  -F "transcription_tier=deferred"
```

Adapter behavior (soniox_adapter.py): upload → create transcription with
`enable_speaker_diarization: true` → poll → fetch token-level transcript
(`golden-2-diarization.soniox-tokens.json`) → fold tokens into verbose_json
segments carrying a string `speaker` cluster id
(`golden-2-diarization.response.json`).

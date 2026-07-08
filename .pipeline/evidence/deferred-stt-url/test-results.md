# Test Evidence — deferred-stt-url (S)

Date: 2026-07-08.

- `pytest tests/test_final_transcription.py tests/test_speaker_clusters.py` → **24 passed**
  (incl. new `test_deferred_transcription_endpoint_override`: override wins,
  independent token fallback, blank strings ignored)
- `python3 -m compileall meeting_api` → OK
- GitNexus impact `_call_transcription_service` upstream → LOW (4)
- Deploy smoke: transcription-worker-1-cpu healthy on 127.0.0.1:8092
  (`{"status":"healthy","model":"tiny","device":"cpu"}`); Dockerfile fix
  required (soniox_adapter.py was not COPYed — found at first container start).

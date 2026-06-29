import asyncio
import base64
import struct
import unittest

import httpx

from app.config import Settings
from app.service import AudioIngest, BroadcastHub, WakeSttService, f32le_to_wav


def _audio_base64(samples: int = 11200) -> str:
    audio = struct.pack(f"<{samples}f", *([0.1] * samples))
    return base64.b64encode(audio).decode("ascii")


class FakeHub(BroadcastHub):
    def __init__(self):
        super().__init__()
        self.messages = []

    async def publish(self, message):
        self.messages.append(message)


class WakeSttServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_ingest_transcribes_short_window_and_publishes_event(self):
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "text": "カボス、自己紹介して",
                    "language": "ja",
                    "segments": [{"text": "カボス、自己紹介して", "start": 0, "end": 0.7}],
                },
            )

        hub = FakeHub()
        service = WakeSttService(
            Settings(
                transcription_url="http://transcription.test/v1/audio/transcriptions",
                min_window_ms=600,
                submit_interval_ms=0,
                turn_silence_ms=10_000,
            ),
            hub,
            httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        try:
            result = await service.ingest(
                AudioIngest(
                    platform="google_meet",
                    native_meeting_id="abc-defg-hij",
                    speaker_id="speaker-1",
                    speaker="Alice",
                    sample_rate=16000,
                    audio_format="f32le",
                    audio_base64=_audio_base64(),
                    captured_at_ms=1_700_000_000_000,
                    wake_trace_id="trace-1",
                    bot_audio_received_ts_ms=1_700_000_000_010,
                    audio_chunk_sent_to_stt_ts_ms=1_700_000_000_020,
                )
            )
            self.assertTrue(result["queued"])

            for _ in range(20):
                if hub.messages:
                    break
                await asyncio.sleep(0.01)

            self.assertEqual(len(requests), 1)
            self.assertEqual(hub.messages[0]["type"], "transcript")
            self.assertEqual(hub.messages[0]["event"], "transcript.partial")
            self.assertEqual(hub.messages[0]["source"], "wake-stt")
            self.assertEqual(hub.messages[0]["wake_trace_id"], "trace-1")
            self.assertEqual(hub.messages[0]["bot_audio_received_ts_ms"], 1_700_000_000_010)
            self.assertEqual(hub.messages[0]["audio_chunk_sent_to_stt_ts_ms"], 1_700_000_000_020)
            self.assertIsInstance(hub.messages[0]["wake_stt_ingest_ts_ms"], int)
            self.assertIsInstance(hub.messages[0]["stt_request_start_ts_ms"], int)
            self.assertIsInstance(hub.messages[0]["stt_response_ts_ms"], int)
            self.assertIsInstance(hub.messages[0]["transcript_mutable_publish_ts_ms"], int)
            self.assertEqual(hub.messages[0]["pending"][0]["text"], "カボス、自己紹介して")
            self.assertEqual(hub.messages[0]["pending"][0]["wake_trace_id"], "trace-1")
            self.assertEqual(hub.messages[0]["_wake_meeting"]["native_id"], "abc-defg-hij")
        finally:
            await service.close()

    async def test_ingest_publishes_command_final_after_wake_turn_silence(self):
        requests = []
        texts = ["カボス", "カボスは天気予報わかる?"]

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            text = texts[min(len(requests) - 1, len(texts) - 1)]
            return httpx.Response(
                200,
                json={
                    "text": text,
                    "language": "ja",
                    "segments": [{"text": text, "start": 0, "end": 0.7}],
                },
            )

        hub = FakeHub()
        service = WakeSttService(
            Settings(
                transcription_url="http://transcription.test/v1/audio/transcriptions",
                min_window_ms=600,
                submit_interval_ms=0,
                turn_silence_ms=10,
                turn_max_ms=200,
            ),
            hub,
            httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        try:
            await service.ingest(
                AudioIngest(
                    platform="google_meet",
                    native_meeting_id="abc-defg-hij",
                    speaker_id="speaker-1",
                    speaker="Alice",
                    sample_rate=16000,
                    audio_format="f32le",
                    audio_base64=_audio_base64(),
                    captured_at_ms=1_700_000_000_000,
                    wake_trace_id="trace-1",
                )
            )

            for _ in range(50):
                if any(message.get("type") == "command.final" for message in hub.messages):
                    break
                await asyncio.sleep(0.01)

            self.assertEqual(len(requests), 2)
            final = [message for message in hub.messages if message.get("type") == "command.final"][0]
            self.assertEqual(final["event"], "command.final")
            self.assertTrue(final["session_id"].startswith("wake-turn-"))
            self.assertEqual(final["wake"], "カボス")
            self.assertEqual(final["text"], "カボスは天気予報わかる?")
            self.assertEqual(final["command_text"], "天気予報わかる?")
            self.assertEqual(final["_wake_meeting"]["native_id"], "abc-defg-hij")
        finally:
            await service.close()

    async def test_ingest_publishes_command_partial_after_stable_strong_command(self):
        requests = []
        texts = ["カボス", "カボス天気予報を教えて"]

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            text = texts[min(len(requests) - 1, len(texts) - 1)]
            return httpx.Response(200, json={"text": text, "language": "ja", "segments": []})

        hub = FakeHub()
        service = WakeSttService(
            Settings(
                transcription_url="http://transcription.test/v1/audio/transcriptions",
                min_window_ms=600,
                submit_interval_ms=0,
                turn_silence_ms=10_000,
                fast_command_stability_ms=10,
            ),
            hub,
            httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        try:
            await service.ingest(
                AudioIngest(
                    platform="google_meet",
                    native_meeting_id="abc-defg-hij",
                    speaker_id="speaker-1",
                    speaker="Alice",
                    sample_rate=16000,
                    audio_format="f32le",
                    audio_base64=_audio_base64(),
                    captured_at_ms=1_700_000_000_000,
                    wake_trace_id="trace-1",
                )
            )
            for _ in range(50):
                if len(requests) >= 1:
                    break
                await asyncio.sleep(0.01)

            await service.ingest(
                AudioIngest(
                    platform="google_meet",
                    native_meeting_id="abc-defg-hij",
                    speaker_id="speaker-1",
                    speaker="Alice",
                    sample_rate=16000,
                    audio_format="f32le",
                    audio_base64=_audio_base64(),
                    captured_at_ms=1_700_000_000_700,
                    wake_trace_id="trace-2",
                )
            )

            for _ in range(50):
                if any(message.get("type") == "command.partial" for message in hub.messages):
                    break
                await asyncio.sleep(0.01)

            partial = [message for message in hub.messages if message.get("type") == "command.partial"][0]
            self.assertEqual(partial["event"], "command.partial")
            self.assertTrue(partial["session_id"].startswith("wake-turn-"))
            self.assertEqual(partial["wake"], "カボス")
            self.assertEqual(partial["text"], "カボス天気予報を教えて")
            self.assertEqual(partial["command_text"], "天気予報を教えて")
        finally:
            await service.close()

    async def test_command_final_drops_particle_only_command(self):
        requests = []
        texts = ["カバス", "カバスを"]

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            text = texts[min(len(requests) - 1, len(texts) - 1)]
            return httpx.Response(200, json={"text": text, "language": "ja", "segments": []})

        hub = FakeHub()
        service = WakeSttService(
            Settings(
                transcription_url="http://transcription.test/v1/audio/transcriptions",
                min_window_ms=600,
                submit_interval_ms=0,
                turn_silence_ms=10,
                turn_max_ms=200,
            ),
            hub,
            httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        try:
            await service.ingest(
                AudioIngest(
                    platform="google_meet",
                    native_meeting_id="abc-defg-hij",
                    speaker_id="speaker-1",
                    speaker="Alice",
                    sample_rate=16000,
                    audio_format="f32le",
                    audio_base64=_audio_base64(),
                    captured_at_ms=1_700_000_000_000,
                )
            )

            for _ in range(50):
                if any(message.get("type") == "command.final" for message in hub.messages):
                    break
                await asyncio.sleep(0.01)

            final = [message for message in hub.messages if message.get("type") == "command.final"][0]
            self.assertEqual(final["text"], "カバスを")
            self.assertEqual(final["command_text"], "")
        finally:
            await service.close()

    async def test_command_final_accepts_common_curve_variant(self):
        requests = []
        texts = ["カーブス", "カーブスは天気予報わかる?"]

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            text = texts[min(len(requests) - 1, len(texts) - 1)]
            return httpx.Response(200, json={"text": text, "language": "ja", "segments": []})

        hub = FakeHub()
        service = WakeSttService(
            Settings(
                transcription_url="http://transcription.test/v1/audio/transcriptions",
                min_window_ms=600,
                submit_interval_ms=0,
                turn_silence_ms=10,
                turn_max_ms=200,
            ),
            hub,
            httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        try:
            await service.ingest(
                AudioIngest(
                    platform="google_meet",
                    native_meeting_id="abc-defg-hij",
                    speaker_id="speaker-1",
                    speaker="Alice",
                    sample_rate=16000,
                    audio_format="f32le",
                    audio_base64=_audio_base64(),
                    captured_at_ms=1_700_000_000_000,
                    wake_trace_id="trace-1",
                )
            )

            for _ in range(50):
                if any(message.get("type") == "command.final" for message in hub.messages):
                    break
                await asyncio.sleep(0.01)

            final = [message for message in hub.messages if message.get("type") == "command.final"][0]
            self.assertEqual(final["wake"], "カーブス")
            self.assertEqual(final["command_text"], "天気予報わかる?")
        finally:
            await service.close()

    def test_f32le_to_wav_writes_wav_header(self):
        wav = f32le_to_wav(struct.pack("<3f", -1.0, 0.0, 1.0), 16000)
        self.assertTrue(wav.startswith(b"RIFF"))
        self.assertIn(b"WAVE", wav[:16])


if __name__ == "__main__":
    unittest.main()

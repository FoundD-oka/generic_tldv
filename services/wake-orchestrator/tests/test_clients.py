import json
import unittest

import httpx

from app.clients import (
    AivisCloudClient,
    GroqClient,
    MeetingRef,
    SpeakAudioResponse,
    TtsResult,
    VexaClient,
    VexaTranscriptSubscriber,
    WakeSttTranscriptSubscriber,
    _wake_stt_ws_url,
)
from app.config import Settings


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_groq_client_uses_groq_gpt_oss_and_hidden_reasoning(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.assertEqual(str(request.url), "https://api.groq.com/openai/v1/chat/completions")
            self.assertEqual(body["model"], "openai/gpt-oss-20b")
            self.assertEqual(body["reasoning_format"], "hidden")
            self.assertEqual(body["max_completion_tokens"], 768)
            self.assertIn("カボス", body["messages"][0]["content"])
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": "現時点では、論点は一つです。"}, "finish_reason": "stop"}
                    ]
                },
            )

        settings = Settings(groq_api_key="groq-key")
        client = GroqClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        self.assertEqual(
            await client.generate_reply("A: テスト", "カボス、まとめて"),
            "現時点では、論点は一つです。",
        )

    async def test_groq_client_retries_when_completion_hits_length_limit(self):
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            requests.append(body)
            if len(requests) == 1:
                self.assertEqual(body["max_completion_tokens"], 180)
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {"message": {"content": "ご質問があれば"}, "finish_reason": "length"}
                        ]
                    },
                )

            self.assertEqual(body["max_completion_tokens"], 768)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": "ご質問があればお知らせください。"}, "finish_reason": "stop"}
                    ]
                },
            )

        settings = Settings(
            groq_api_key="groq-key",
            groq_max_completion_tokens=180,
            groq_retry_max_completion_tokens=768,
        )
        client = GroqClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))

        self.assertEqual(
            await client.generate_reply("A: テスト", "カボス、自己紹介して"),
            "ご質問があればお知らせください。",
        )
        self.assertEqual(len(requests), 2)

    async def test_aivis_client_uses_configured_model_and_wav_payload(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.assertEqual(str(request.url), "https://api.aivis-project.com/v1/tts/synthesize")
            self.assertEqual(request.headers["Authorization"], "Bearer aivis-key")
            self.assertEqual(body["model_uuid"], "18972473-ca36-4e06-a33a-5cc14adba0c4")
            self.assertEqual(body["output_format"], "wav")
            self.assertEqual(body["style_id"], 0)
            self.assertEqual(body["output_sampling_rate"], 24000)
            self.assertEqual(body["leading_silence_seconds"], 0.05)
            self.assertEqual(body["trailing_silence_seconds"], 0.7)
            self.assertEqual(body["line_break_silence_seconds"], 0.2)
            self.assertNotIn("output_bitrate", body)
            return httpx.Response(200, content=b"wav", headers={"X-Aivis-Credits-Remaining": "10"})

        settings = Settings(aivis_api_key="aivis-key")
        client = AivisCloudClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        result = await client.synthesize("こんにちは。")
        self.assertEqual(result.audio, b"wav")
        self.assertEqual(result.format, "wav")
        self.assertEqual(result.headers["x-aivis-credits-remaining"], "10")

    async def test_vexa_client_sends_aivis_audio_base64(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.assertEqual(str(request.url), "http://vexa.test/bots/google_meet/abc-defg-hij/speak")
            self.assertEqual(request.headers["X-API-Key"], "vexa-key")
            self.assertEqual(
                body,
                {
                    "audio_base64": "bXAz",
                    "format": "mp3",
                    "sample_rate": 24000,
                    "request_id": "req-1",
                },
            )
            return httpx.Response(202, json={"meeting_id": 42, "request_id": "req-1"})

        settings = Settings(
            vexa_api_url="http://vexa.test",
            vexa_api_key="vexa-key",
            vexa_native_meeting_id="abc-defg-hij",
        )
        client = VexaClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        result = await client.speak_audio(
            TtsResult(audio=b"mp3", format="mp3", sample_rate=24000, headers={}),
            request_id="req-1",
        )
        self.assertEqual(result, SpeakAudioResponse(meeting_id=42, request_id="req-1"))

    async def test_vexa_client_can_speak_to_discovered_meeting(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(str(request.url), "http://vexa.test/bots/zoom/123456789/speak")
            return httpx.Response(202, json={"ok": True})

        settings = Settings(vexa_api_url="http://vexa.test", vexa_api_key="vexa-key")
        client = VexaClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        await client.speak_audio(
            TtsResult(audio=b"mp3", format="mp3", sample_rate=24000, headers={}),
            MeetingRef(platform="zoom", native_id="123456789", meeting_id=42),
        )

    async def test_vexa_client_waits_for_matching_speech_completion_event(self):
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            self.assertEqual(request.method, "GET")
            self.assertEqual(str(request.url), "http://vexa.test/bots/google_meet/abc-defg-hij/events?limit=100")
            calls += 1
            if calls == 1:
                return httpx.Response(
                    200,
                    json={
                        "events": [
                            {"event": "speak.completed", "request_id": "other"},
                            {"event": "speak.started", "request_id": "req-1"},
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={"events": [{"event": "speak.completed", "request_id": "req-1"}]},
            )

        settings = Settings(
            vexa_api_url="http://vexa.test",
            vexa_api_key="vexa-key",
            vexa_native_meeting_id="abc-defg-hij",
        )
        client = VexaClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))

        event = await client.wait_for_speech_to_finish(
            request_id="req-1",
            timeout_seconds=1.0,
            poll_interval_seconds=0.01,
        )

        self.assertEqual(event, "speak.completed")
        self.assertEqual(calls, 2)

    async def test_vexa_client_can_stop_current_speech(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "DELETE")
            self.assertEqual(str(request.url), "http://vexa.test/bots/google_meet/abc-defg-hij/speak")
            self.assertEqual(request.headers["X-API-Key"], "vexa-key")
            return httpx.Response(202, json={"ok": True})

        settings = Settings(
            vexa_api_url="http://vexa.test",
            vexa_api_key="vexa-key",
            vexa_native_meeting_id="abc-defg-hij",
        )
        client = VexaClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        await client.stop_speech()

    async def test_vexa_client_reads_chat_messages(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "GET")
            self.assertEqual(str(request.url), "http://vexa.test/bots/google_meet/abc-defg-hij/chat")
            self.assertEqual(request.headers["X-API-Key"], "vexa-key")
            return httpx.Response(
                200,
                json={
                    "messages": [
                        {
                            "sender": "Alice",
                            "text": "カボス、このURL見て",
                            "timestamp": 1760000000000,
                            "is_from_bot": False,
                        }
                    ],
                    "meeting_id": 42,
                },
            )

        settings = Settings(
            vexa_api_url="http://vexa.test",
            vexa_api_key="vexa-key",
            vexa_native_meeting_id="abc-defg-hij",
        )
        client = VexaClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))

        self.assertEqual(
            await client.chat_messages(),
            [
                {
                    "sender": "Alice",
                    "text": "カボス、このURL見て",
                    "timestamp": 1760000000000,
                    "is_from_bot": False,
                }
            ],
        )

    async def test_vexa_client_sends_chat_message(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.assertEqual(request.method, "POST")
            self.assertEqual(str(request.url), "http://vexa.test/bots/google_meet/abc-defg-hij/chat")
            self.assertEqual(request.headers["X-API-Key"], "vexa-key")
            self.assertEqual(body, {"text": "カボス:\n了解です。"})
            return httpx.Response(202, json={"meeting_id": 42})

        settings = Settings(
            vexa_api_url="http://vexa.test",
            vexa_api_key="vexa-key",
            vexa_native_meeting_id="abc-defg-hij",
        )
        client = VexaClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))

        self.assertEqual(await client.send_chat("カボス:\n了解です。"), 42)

    async def test_vexa_client_lists_running_bots_as_meeting_refs(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(str(request.url), "http://vexa.test/bots/status")
            self.assertEqual(request.headers["X-API-Key"], "vexa-key")
            return httpx.Response(
                200,
                json={
                    "running_bots": [
                        {
                            "platform": "google_meet",
                            "native_meeting_id": "abc-defg-hij",
                            "meeting_id_from_name": "42",
                        },
                        {"platform": "zoom", "native_meeting_id": "123456789", "meeting_id": 43},
                    ]
                },
            )

        settings = Settings(vexa_api_url="http://vexa.test", vexa_api_key="vexa-key")
        client = VexaClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))

        self.assertEqual(
            await client.list_running_bots(),
            [
                MeetingRef(platform="google_meet", native_id="abc-defg-hij", meeting_id=42),
                MeetingRef(platform="zoom", native_id="123456789", meeting_id=43),
            ],
        )

    async def test_transcript_subscriber_resubscribes_when_meeting_id_changes(self):
        class FakeVexa:
            def __init__(self):
                self.responses = [
                    [MeetingRef(platform="google_meet", native_id="abc-defg-hij", meeting_id=13)],
                    [MeetingRef(platform="google_meet", native_id="abc-defg-hij", meeting_id=14)],
                ]

            async def list_running_bots(self):
                return self.responses.pop(0)

        class FakeWebSocket:
            def __init__(self):
                self.messages = []

            async def send(self, message):
                self.messages.append(json.loads(message))

        settings = Settings(vexa_api_key="vexa-key", wake_auto_discover_bots=True)
        subscriber = VexaTranscriptSubscriber(settings, FakeVexa())
        ws = FakeWebSocket()
        subscribed_keys = set()

        await subscriber._subscribe_new(ws, subscribed_keys)
        await subscriber._subscribe_new(ws, subscribed_keys)

        self.assertEqual(ws.messages[0]["action"], "subscribe")
        self.assertEqual(ws.messages[1]["action"], "unsubscribe")
        self.assertEqual(ws.messages[2]["action"], "subscribe")
        self.assertEqual(ws.messages[1]["meetings"], [{"platform": "google_meet", "native_id": "abc-defg-hij"}])
        self.assertEqual(ws.messages[2]["meetings"], [{"platform": "google_meet", "native_id": "abc-defg-hij"}])
        self.assertEqual(subscriber._meeting_refs_by_id[14].key, "google_meet:abc-defg-hij")
        self.assertNotIn(13, subscriber._meeting_refs_by_id)

    def test_wake_stt_ws_url_uses_ws_path_and_token(self):
        self.assertEqual(
            _wake_stt_ws_url("http://wake-stt:8058", "secret"),
            "ws://wake-stt:8058/ws?token=secret",
        )
        self.assertEqual(
            _wake_stt_ws_url("https://wake.example"),
            "wss://wake.example/ws",
        )

    def test_wake_stt_subscriber_requires_url(self):
        with self.assertRaises(ValueError):
            WakeSttTranscriptSubscriber(Settings())

        subscriber = WakeSttTranscriptSubscriber(Settings(wake_stt_url="http://wake-stt:8058"))
        self.assertIsInstance(subscriber, WakeSttTranscriptSubscriber)

    def test_missing_required_allows_discovery_without_fixed_meeting_id(self):
        settings = Settings(
            vexa_api_key="vexa-key",
            groq_api_key="groq-key",
            aivis_api_key="aivis-key",
            vexa_native_meeting_id=None,
            wake_auto_discover_bots=True,
        )
        self.assertEqual(settings.missing_required(), [])

    def test_missing_required_requires_llm_and_tts_keys(self):
        settings = Settings(
            vexa_api_key="vexa-key",
            vexa_native_meeting_id=None,
            wake_auto_discover_bots=True,
        )
        self.assertEqual(settings.missing_required(), ["GROQ_API_KEY", "AIVIS_API_KEY"])

    def test_missing_required_requires_fixed_meeting_id_when_discovery_disabled(self):
        settings = Settings(
            vexa_api_key="vexa-key",
            groq_api_key="groq-key",
            aivis_api_key="aivis-key",
            vexa_native_meeting_id=None,
            wake_auto_discover_bots=False,
        )
        self.assertEqual(settings.missing_required(), ["VEXA_NATIVE_MEETING_ID"])


if __name__ == "__main__":
    unittest.main()

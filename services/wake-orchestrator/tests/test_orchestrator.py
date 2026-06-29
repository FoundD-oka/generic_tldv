import asyncio
import importlib.resources
import io
import tempfile
import unittest
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.clients import MeetingRef, TtsResult
from app.config import Settings
from app.orchestrator import TranscriptSegment, WakeOrchestrator, WakeState


def _wav_bytes(duration_seconds: float = 0.01, sample_rate: int = 24000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * max(1, int(sample_rate * duration_seconds)))
    return buffer.getvalue()


class FakeGroq:
    def __init__(self, reply="現時点では、論点は一つです。"):
        self.calls = []
        self.reply = reply

    async def generate_reply(self, recent_transcript, wake_utterance):
        self.calls.append((recent_transcript, wake_utterance))
        return self.reply


class FakeAivis:
    def __init__(self, audio=None):
        self.calls = []
        self.audio = audio or _wav_bytes()

    async def synthesize(self, text):
        self.calls.append(text)
        return TtsResult(audio=self.audio, format="wav", sample_rate=24000, headers={})


class BlockingAivis(FakeAivis):
    def __init__(self, started: asyncio.Event, release: asyncio.Event):
        super().__init__()
        self.started = started
        self.release = release

    async def synthesize(self, text):
        self.calls.append(text)
        self.started.set()
        await self.release.wait()
        return TtsResult(audio=self.audio, format="wav", sample_rate=24000, headers={})


class FakeVexa:
    def __init__(self):
        self.calls = []
        self.wait_calls = []
        self.terminal_event = "speak.completed"

    async def speak_audio(self, result, meeting=None, request_id=None):
        self.calls.append((result, meeting, request_id))

    async def wait_for_speech_to_finish(
        self,
        request_id,
        meeting=None,
        timeout_seconds=60.0,
        poll_interval_seconds=0.25,
    ):
        self.wait_calls.append((request_id, meeting, timeout_seconds, poll_interval_seconds))
        return self.terminal_event

    async def stop_speech(self, meeting=None):
        raise AssertionError("stop_speech must not be called")


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_orchestrator_plays_recorded_wav_ack_then_llm_answer(self):
        audio_bytes = b"recorded-wav"
        with tempfile.TemporaryDirectory() as tmpdir:
            ack_path = Path(tmpdir) / "wake_ack.wav"
            ack_path.write_bytes(audio_bytes)
            settings = Settings(
                wake_cooldown_ms=1,
                wake_response_playback_guard_ms=0,
                wake_ack_audio_path=str(ack_path),
                bot_echo_cooldown_ms=1,
            )
            groq = FakeGroq()
            aivis = FakeAivis()
            vexa = FakeVexa()
            orchestrator = WakeOrchestrator(settings, groq, aivis, vexa)

            await orchestrator.handle_segment(
                TranscriptSegment(
                    text="カボス、自己紹介して。",
                    speaker="Alice",
                    segment_id="seg-1",
                    completed=True,
                )
            )

        self.assertEqual(len(vexa.calls), 2)
        ack_result = vexa.calls[0][0]
        self.assertEqual(
            ack_result,
            TtsResult(
                audio=audio_bytes,
                format="wav",
                sample_rate=24000,
                headers={"purpose": "wake_ack", "source": "recorded_wav"},
            ),
        )
        answer_result = vexa.calls[1][0]
        self.assertEqual(answer_result.audio, aivis.audio)
        self.assertTrue(vexa.calls[0][2].startswith("wake-ack-"))
        self.assertTrue(vexa.calls[1][2].startswith("wake-reply-"))
        self.assertEqual(len(vexa.wait_calls), 1)
        self.assertEqual(vexa.wait_calls[0][0], vexa.calls[1][2])
        self.assertEqual(groq.calls[0][1], "自己紹介して。")
        self.assertIn("Alice: カボス、自己紹介して。", groq.calls[0][0])
        self.assertEqual(aivis.calls, ["現時点では、論点は一つです。"])
        self.assertEqual(orchestrator.state, WakeState.IDLE)

    async def test_orchestrator_loads_packaged_ack_wav_by_default(self):
        settings = Settings(
            wake_cooldown_ms=1,
            wake_response_playback_guard_ms=0,
            bot_echo_cooldown_ms=1,
        )
        vexa = FakeVexa()
        orchestrator = WakeOrchestrator(settings, FakeGroq(), FakeAivis(), vexa)

        await orchestrator.handle_segment(
            TranscriptSegment(
                text="カボス",
                speaker="Alice",
                segment_id="seg-1",
                completed=True,
            )
        )

        self.assertEqual(len(vexa.calls), 2)
        result = vexa.calls[0][0]
        expected_audio = (
            importlib.resources.files("app")
            .joinpath("assets")
            .joinpath("wake_ack_un_bang.wav")
            .read_bytes()
        )
        self.assertEqual(result.audio, expected_audio)
        self.assertTrue(result.audio.startswith(b"RIFF"))
        self.assertEqual(result.format, "wav")
        self.assertEqual(result.headers["purpose"], "wake_ack")

    async def test_orchestrator_speaks_to_its_meeting_ref(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ack_path = Path(tmpdir) / "wake_ack.wav"
            ack_path.write_bytes(b"recorded-wav")
            settings = Settings(
                wake_cooldown_ms=1,
                wake_response_playback_guard_ms=0,
                wake_ack_audio_path=str(ack_path),
                bot_echo_cooldown_ms=1,
            )
            vexa = FakeVexa()
            meeting = MeetingRef(platform="google_meet", native_id="abc-defg-hij", meeting_id=42)
            orchestrator = WakeOrchestrator(settings, FakeGroq(), FakeAivis(), vexa, meeting)

            await orchestrator.handle_segment(
                TranscriptSegment(
                    text="カボス",
                    speaker="Alice",
                    segment_id="seg-1",
                    completed=True,
                )
            )

        self.assertEqual(vexa.calls[0][1], meeting)
        self.assertEqual(vexa.calls[1][1], meeting)

    async def test_orchestrator_detects_wake_in_middle_and_at_end(self):
        settings = Settings(
            wake_cooldown_ms=1,
            wake_response_playback_guard_ms=0,
            wake_ack_enabled=False,
            bot_echo_cooldown_ms=1,
        )
        groq = FakeGroq()
        vexa = FakeVexa()
        orchestrator = WakeOrchestrator(settings, groq, FakeAivis(), vexa)

        await orchestrator.handle_segment(
            TranscriptSegment(
                text="この件カボスどう思う",
                speaker="Alice",
                segment_id="seg-1",
                completed=True,
            )
        )
        await orchestrator.handle_segment(
            TranscriptSegment(
                text="今の論点まとめて、かばす",
                speaker="Bob",
                segment_id="seg-2",
                completed=True,
            )
        )

        self.assertEqual(len(groq.calls), 2)
        self.assertEqual(groq.calls[0][1], "この件 どう思う")
        self.assertEqual(groq.calls[1][1], "今の論点まとめて")
        self.assertEqual(len(vexa.calls), 2)

    async def test_orchestrator_ignores_new_voice_while_answering(self):
        started = asyncio.Event()
        release = asyncio.Event()
        settings = Settings(
            wake_cooldown_ms=1,
            wake_response_playback_guard_ms=0,
            wake_ack_enabled=False,
            bot_echo_cooldown_ms=1,
        )
        groq = FakeGroq()
        aivis = BlockingAivis(started, release)
        vexa = FakeVexa()
        orchestrator = WakeOrchestrator(settings, groq, aivis, vexa)

        task = asyncio.create_task(
            orchestrator.handle_segment(
                TranscriptSegment(
                    text="カボス、最初の質問",
                    speaker="Alice",
                    segment_id="seg-1",
                    completed=True,
                )
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        self.assertEqual(orchestrator.state, WakeState.SYNTHESIZING)

        await orchestrator.handle_segment(
            TranscriptSegment(
                text="カボス、二つ目の質問",
                speaker="Bob",
                segment_id="seg-2",
                completed=True,
            )
        )

        release.set()
        await task
        self.assertEqual(len(groq.calls), 1)
        self.assertEqual(len(aivis.calls), 1)
        self.assertEqual(len(vexa.calls), 1)
        self.assertEqual(orchestrator.state, WakeState.IDLE)

    async def test_orchestrator_accepts_new_wake_right_after_non_echo_reply(self):
        settings = Settings(
            wake_cooldown_ms=1,
            wake_response_playback_guard_ms=0,
            wake_ack_enabled=False,
            bot_echo_cooldown_ms=60000,
            wake_same_speaker_dedupe_ms=1,
        )
        groq = FakeGroq()
        vexa = FakeVexa()
        orchestrator = WakeOrchestrator(settings, groq, FakeAivis(), vexa)

        await orchestrator.handle_segment(
            TranscriptSegment(
                text="カボス、最初の質問",
                speaker="Alice",
                segment_id="seg-1",
                completed=True,
            )
        )
        await asyncio.sleep(0.01)
        await orchestrator.handle_segment(
            TranscriptSegment(
                text="カボス、次の質問",
                speaker="Alice",
                segment_id="seg-2",
                completed=True,
            )
        )

        self.assertEqual(len(groq.calls), 2)
        self.assertEqual(len(vexa.calls), 2)

    async def test_orchestrator_accepts_repeated_same_request_as_new_wake(self):
        settings = Settings(
            wake_cooldown_ms=0,
            wake_response_playback_guard_ms=0,
            wake_ack_enabled=False,
            bot_echo_cooldown_ms=0,
            wake_same_speaker_dedupe_ms=0,
        )
        groq = FakeGroq()
        vexa = FakeVexa()
        orchestrator = WakeOrchestrator(settings, groq, FakeAivis(), vexa)

        await orchestrator.handle_segment(
            TranscriptSegment(
                text="カボス、自己紹介して",
                speaker="Alice",
                segment_id="seg-1",
                completed=True,
            )
        )
        await orchestrator.handle_segment(
            TranscriptSegment(
                text="カボス、自己紹介して",
                speaker="Alice",
                segment_id="seg-2",
                completed=True,
            )
        )

        self.assertEqual(len(groq.calls), 2)
        self.assertEqual(len(vexa.calls), 2)

    async def test_handle_message_answers_once_per_transcript_batch(self):
        settings = Settings(
            wake_cooldown_ms=0,
            wake_input_settle_ms=1,
            wake_max_input_ms=50,
            wake_response_playback_guard_ms=0,
            wake_ack_enabled=False,
            bot_echo_cooldown_ms=0,
            wake_same_speaker_dedupe_ms=0,
        )
        groq = FakeGroq()
        vexa = FakeVexa()
        orchestrator = WakeOrchestrator(settings, groq, FakeAivis(), vexa)

        await orchestrator.handle_message(
            {
                "type": "transcript",
                "speaker": "Alice",
                "pending": [
                    {"text": "カボス、チャット見れる?", "speaker": "Alice", "segment_id": "seg-1"},
                    {"text": "カボス、チャットの内容見れる?", "speaker": "Alice", "segment_id": "seg-2"},
                    {"text": "カボス、チャットの内容見れますか?", "speaker": "Alice", "segment_id": "seg-3"},
                ],
            }
        )
        await asyncio.sleep(0.05)
        await orchestrator.handle_message(
            {
                "type": "transcript",
                "speaker": "Alice",
                "pending": [
                    {"text": "カボス、チャットの内容見れる?", "speaker": "Alice", "segment_id": "seg-2"},
                ],
            }
        )

        self.assertEqual(len(groq.calls), 1)
        self.assertEqual(groq.calls[0][1], "チャットの内容見れますか?")
        self.assertEqual(len(vexa.calls), 1)

    async def test_pending_wake_acks_immediately_then_answers_latest_update(self):
        audio_bytes = b"recorded-wav"
        with tempfile.TemporaryDirectory() as tmpdir:
            ack_path = Path(tmpdir) / "wake_ack.wav"
            ack_path.write_bytes(audio_bytes)
            settings = Settings(
                wake_cooldown_ms=0,
                wake_input_settle_ms=20,
                wake_max_input_ms=200,
                wake_ack_min_answer_gap_ms=0,
                wake_response_playback_guard_ms=0,
                wake_ack_audio_path=str(ack_path),
                bot_echo_cooldown_ms=0,
                wake_same_speaker_dedupe_ms=0,
            )
            groq = FakeGroq()
            vexa = FakeVexa()
            orchestrator = WakeOrchestrator(settings, groq, FakeAivis(), vexa)

            await orchestrator.handle_message(
                {
                    "type": "transcript",
                    "speaker": "Alice",
                    "pending": [
                        {
                            "text": "カボスはクリンゴン語喋れる?",
                            "speaker": "Alice",
                            "segment_id": "seg-1",
                            "completed": False,
                        },
                        {
                            "text": "カボスはクリンゴン語喋られる?",
                            "speaker": "Alice",
                            "segment_id": "seg-2",
                            "completed": False,
                        },
                    ],
                }
            )
            await asyncio.sleep(0)

            self.assertEqual(len(vexa.calls), 1)
            self.assertEqual(vexa.calls[0][0].audio, audio_bytes)
            self.assertEqual(groq.calls, [])

            await asyncio.sleep(0.05)

        self.assertEqual(len(groq.calls), 1)
        self.assertEqual(groq.calls[0][1], "はクリンゴン語喋られる?")
        self.assertEqual(len(vexa.calls), 2)

    async def test_pending_wake_skips_ack_when_transcript_is_stale(self):
        audio_bytes = b"recorded-wav"
        with tempfile.TemporaryDirectory() as tmpdir:
            ack_path = Path(tmpdir) / "wake_ack.wav"
            ack_path.write_bytes(audio_bytes)
            settings = Settings(
                wake_cooldown_ms=0,
                wake_input_settle_ms=1,
                wake_max_input_ms=50,
                wake_response_playback_guard_ms=0,
                wake_ack_audio_path=str(ack_path),
                wake_ack_max_lag_ms=1000,
                bot_echo_cooldown_ms=0,
                wake_same_speaker_dedupe_ms=0,
            )
            groq = FakeGroq()
            vexa = FakeVexa()
            orchestrator = WakeOrchestrator(settings, groq, FakeAivis(), vexa)
            old_end = (datetime.now(timezone.utc) - timedelta(seconds=3)).isoformat()

            await orchestrator.handle_message(
                {
                    "type": "transcript",
                    "speaker": "Alice",
                    "pending": [
                        {
                            "text": "カボス、自己紹介して",
                            "speaker": "Alice",
                            "segment_id": "seg-stale",
                            "completed": False,
                            "absolute_end_time": old_end,
                        },
                    ],
                }
            )
            await asyncio.sleep(0.05)

        self.assertEqual(len(groq.calls), 1)
        self.assertEqual(groq.calls[0][1], "自己紹介して")
        self.assertEqual(len(vexa.calls), 1)
        self.assertTrue(vexa.calls[0][2].startswith("wake-reply-"))

    async def test_pending_wake_ignores_late_stabilized_duplicate(self):
        settings = Settings(
            wake_cooldown_ms=0,
            wake_input_settle_ms=1,
            wake_max_input_ms=50,
            wake_response_playback_guard_ms=0,
            wake_ack_enabled=False,
            bot_echo_cooldown_ms=0,
            wake_same_speaker_dedupe_ms=0,
            wake_stabilized_duplicate_ms=20000,
        )
        groq = FakeGroq()
        vexa = FakeVexa()
        orchestrator = WakeOrchestrator(settings, groq, FakeAivis(), vexa)

        await orchestrator.handle_message(
            {
                "type": "transcript",
                "speaker": "Alice",
                "pending": [
                    {
                        "text": "カボスはクリンゴン語喋れる?",
                        "speaker": "Alice",
                        "segment_id": "seg-1",
                        "completed": False,
                    },
                    {
                        "text": "カボスはクリンゴン語喋られる?",
                        "speaker": "Alice",
                        "segment_id": "seg-2",
                        "completed": False,
                    },
                ],
            }
        )
        await asyncio.sleep(0.05)
        await orchestrator.handle_segment(
            TranscriptSegment(
                text="カボスはクリンゴン語喋られる?",
                speaker="Alice",
                segment_id="seg-3",
                completed=False,
            )
        )

        self.assertEqual(len(groq.calls), 1)
        self.assertEqual(len(vexa.calls), 1)

    async def test_orchestrator_ignores_same_pending_update_after_wake(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ack_path = Path(tmpdir) / "wake_ack.wav"
            ack_path.write_bytes(b"recorded-wav")
            settings = Settings(
                wake_cooldown_ms=1,
                wake_input_settle_ms=0,
                wake_response_playback_guard_ms=0,
                wake_ack_audio_path=str(ack_path),
                bot_echo_cooldown_ms=1,
            )
            vexa = FakeVexa()
            groq = FakeGroq()
            orchestrator = WakeOrchestrator(settings, groq, FakeAivis(), vexa)

            await orchestrator.handle_segment(
                TranscriptSegment(
                    text="カボス",
                    speaker="Alice",
                    segment_id="pending:Alice:0",
                    completed=False,
                )
            )
            await orchestrator.handle_segment(
                TranscriptSegment(
                    text="カボス、自己紹介して",
                    speaker="Alice",
                    segment_id="pending:Alice:0",
                    completed=False,
                )
            )

        self.assertEqual(len(groq.calls), 1)
        self.assertEqual(len(vexa.calls), 2)

    async def test_handle_message_uses_pending_as_default_wake_source(self):
        settings = Settings(
            wake_cooldown_ms=1,
            wake_input_settle_ms=0,
            wake_response_playback_guard_ms=0,
            wake_ack_enabled=False,
            bot_echo_cooldown_ms=1,
        )
        groq = FakeGroq()
        vexa = FakeVexa()
        orchestrator = WakeOrchestrator(settings, groq, FakeAivis(), vexa)

        await orchestrator.handle_message(
            {
                "type": "transcript",
                "speaker": "Alice",
                "confirmed": [
                    {
                        "text": "カボス、これは古い確定字幕",
                        "speaker": "Alice",
                        "completed": True,
                        "segment_id": "confirmed-old",
                    }
                ],
                "pending": [
                    {
                        "text": "カボス、今の入力を見て",
                        "speaker": "Alice",
                        "completed": False,
                    }
                ],
            }
        )

        self.assertEqual(len(groq.calls), 1)
        self.assertEqual(groq.calls[0][1], "今の入力を見て")

    async def test_handle_message_preserves_wake_stt_source_on_segments(self):
        settings = Settings(
            wake_cooldown_ms=1,
            wake_input_settle_ms=0,
            wake_response_playback_guard_ms=0,
            wake_ack_enabled=False,
            bot_echo_cooldown_ms=0,
        )
        orchestrator = WakeOrchestrator(settings, FakeGroq(), FakeAivis(), FakeVexa())

        segments = orchestrator._segments_from_message(
            {
                "type": "transcript",
                "source": "wake-stt",
                "speaker": "Alice",
                "pending": [{"text": "カボス", "speaker": "Alice"}],
            }
        )

        self.assertEqual(segments[0].source, "wake-stt")

    async def test_wake_stt_partial_waits_for_command_final(self):
        settings = Settings(
            wake_cooldown_ms=1,
            wake_input_settle_ms=10,
            wake_max_input_ms=20,
            wake_response_playback_guard_ms=0,
            wake_ack_enabled=False,
            bot_echo_cooldown_ms=0,
        )
        groq = FakeGroq()
        vexa = FakeVexa()
        orchestrator = WakeOrchestrator(settings, groq, FakeAivis(), vexa)

        await orchestrator.handle_message(
            {
                "type": "transcript",
                "event": "transcript.partial",
                "source": "wake-stt",
                "speaker": "Alice",
                "pending": [
                    {
                        "text": "カボス",
                        "speaker": "Alice",
                        "segment_id": "wake-stt:turn:0",
                    }
                ],
            }
        )
        await asyncio.sleep(0.05)

        self.assertEqual(groq.calls, [])
        self.assertEqual(vexa.calls, [])

        await orchestrator.handle_message(
            {
                "type": "command.final",
                "event": "command.final",
                "source": "wake-stt",
                "speaker": "Alice",
                "session_id": "wake-turn-1",
                "wake": "カボス",
                "text": "カボスは天気予報わかる?",
                "command_text": "天気予報わかる?",
            }
        )

        self.assertEqual(len(groq.calls), 1)
        self.assertEqual(groq.calls[0][1], "天気予報わかる?")
        self.assertEqual(len(vexa.calls), 1)

    async def test_command_final_with_particle_only_command_does_not_answer(self):
        settings = Settings(
            wake_cooldown_ms=1,
            wake_response_playback_guard_ms=0,
            wake_ack_enabled=False,
            bot_echo_cooldown_ms=0,
        )
        groq = FakeGroq()
        vexa = FakeVexa()
        orchestrator = WakeOrchestrator(settings, groq, FakeAivis(), vexa)

        await orchestrator.handle_message(
            {
                "type": "command.final",
                "event": "command.final",
                "source": "wake-stt",
                "speaker": "Alice",
                "session_id": "wake-turn-1",
                "wake": "カバス",
                "text": "カバスを",
                "command_text": "",
            }
        )

        self.assertEqual(groq.calls, [])
        self.assertEqual(vexa.calls, [])

    async def test_command_partial_answers_and_command_final_duplicate_is_ignored(self):
        settings = Settings(
            wake_cooldown_ms=1,
            wake_response_playback_guard_ms=0,
            wake_ack_enabled=False,
            bot_echo_cooldown_ms=0,
        )
        groq = FakeGroq()
        vexa = FakeVexa()
        orchestrator = WakeOrchestrator(settings, groq, FakeAivis(), vexa)

        await orchestrator.handle_message(
            {
                "type": "command.partial",
                "event": "command.partial",
                "source": "wake-stt",
                "speaker": "Alice",
                "session_id": "wake-turn-1",
                "wake": "カボス",
                "text": "カボス天気予報を教えて",
                "command_text": "天気予報を教えて",
            }
        )
        await orchestrator.handle_message(
            {
                "type": "command.final",
                "event": "command.final",
                "source": "wake-stt",
                "speaker": "Alice",
                "session_id": "wake-turn-1",
                "wake": "カボス",
                "text": "カボス天気予報を教えて",
                "command_text": "天気予報を教えて",
            }
        )

        self.assertEqual(len(groq.calls), 1)
        self.assertEqual(groq.calls[0][1], "天気予報を教えて")
        self.assertEqual(len(vexa.calls), 1)

    async def test_handle_message_preserves_wake_timing_metadata_on_segments(self):
        settings = Settings(
            wake_cooldown_ms=1,
            wake_input_settle_ms=0,
            wake_response_playback_guard_ms=0,
            wake_ack_enabled=False,
            bot_echo_cooldown_ms=0,
        )
        orchestrator = WakeOrchestrator(settings, FakeGroq(), FakeAivis(), FakeVexa())

        segments = orchestrator._segments_from_message(
            {
                "type": "transcript",
                "source": "wake-stt",
                "speaker": "Alice",
                "wake_trace_id": "trace-1",
                "bot_audio_received_ts_ms": 100,
                "audio_chunk_sent_to_stt_ts_ms": 120,
                "wake_stt_ingest_ts_ms": 140,
                "stt_request_start_ts_ms": 160,
                "stt_response_ts_ms": 220,
                "transcript_mutable_publish_ts_ms": 230,
                "_wake_websocket_received_ts_ms": 245,
                "pending": [{"text": "カボス", "speaker": "Alice"}],
            }
        )

        self.assertEqual(segments[0].wake_trace_id, "trace-1")
        self.assertEqual(segments[0].bot_audio_received_ts_ms, 100)
        self.assertEqual(segments[0].audio_chunk_sent_to_stt_ts_ms, 120)
        self.assertEqual(segments[0].wake_stt_ingest_ts_ms, 140)
        self.assertEqual(segments[0].stt_request_start_ts_ms, 160)
        self.assertEqual(segments[0].stt_response_ts_ms, 220)
        self.assertEqual(segments[0].transcript_mutable_publish_ts_ms, 230)
        self.assertEqual(segments[0].websocket_received_ts_ms, 245)

    async def test_handle_message_does_not_reuse_generic_pending_segment_id(self):
        settings = Settings(
            wake_cooldown_ms=1,
            wake_input_settle_ms=0,
            wake_response_playback_guard_ms=0,
            wake_ack_enabled=False,
            bot_echo_cooldown_ms=0,
            wake_same_speaker_dedupe_ms=1,
        )
        groq = FakeGroq()
        vexa = FakeVexa()
        orchestrator = WakeOrchestrator(settings, groq, FakeAivis(), vexa)

        await orchestrator.handle_message(
            {
                "type": "transcript",
                "speaker": "Alice",
                "pending": [{"text": "カボス自己紹介して", "speaker": "Alice"}],
            }
        )
        await asyncio.sleep(0.01)
        await orchestrator.handle_message(
            {
                "type": "transcript",
                "speaker": "Alice",
                "pending": [{"text": "カボス今日の天気を教えて", "speaker": "Alice"}],
            }
        )

        self.assertEqual(len(groq.calls), 2)
        self.assertEqual(len(vexa.calls), 2)

    async def test_handle_message_ignores_confirmed_wake_by_default(self):
        settings = Settings(
            wake_cooldown_ms=1,
            wake_response_playback_guard_ms=0,
            wake_ack_enabled=False,
            bot_echo_cooldown_ms=1,
        )
        groq = FakeGroq()
        vexa = FakeVexa()
        orchestrator = WakeOrchestrator(settings, groq, FakeAivis(), vexa)

        await orchestrator.handle_message(
            {
                "type": "transcript",
                "speaker": "Alice",
                "confirmed": [
                    {
                        "text": "カボス、確定字幕では起きないで",
                        "speaker": "Alice",
                        "completed": True,
                        "segment_id": "confirmed-only",
                    }
                ],
                "pending": [],
            }
        )

        self.assertEqual(groq.calls, [])
        self.assertEqual(vexa.calls, [])

    async def test_handle_message_can_opt_into_confirmed_wake_source(self):
        settings = Settings(
            wake_cooldown_ms=1,
            wake_response_playback_guard_ms=0,
            wake_ack_enabled=False,
            bot_echo_cooldown_ms=1,
            wake_use_confirmed_transcripts=True,
            wake_use_pending_transcripts=False,
        )
        groq = FakeGroq()
        orchestrator = WakeOrchestrator(settings, groq, FakeAivis(), FakeVexa())

        await orchestrator.handle_message(
            {
                "type": "transcript",
                "speaker": "Alice",
                "confirmed": [
                    {
                        "text": "カボス、互換モード",
                        "speaker": "Alice",
                        "completed": True,
                        "segment_id": "confirmed-opt-in",
                    }
                ],
            }
        )

        self.assertEqual(len(groq.calls), 1)
        self.assertEqual(groq.calls[0][1], "互換モード")

    async def test_orchestrator_ignores_confirmed_extension_after_pending_wake(self):
        settings = Settings(
            wake_cooldown_ms=1,
            wake_input_settle_ms=0,
            wake_response_playback_guard_ms=0,
            wake_ack_enabled=False,
            bot_echo_cooldown_ms=0,
            wake_same_speaker_dedupe_ms=1,
        )
        vexa = FakeVexa()
        groq = FakeGroq()
        orchestrator = WakeOrchestrator(settings, groq, FakeAivis(), vexa)

        await orchestrator.handle_message(
            {
                "type": "transcript",
                "speaker": "Alice",
                "pending": [
                    {
                        "text": "カボスができる機能を",
                        "speaker": "Alice",
                        "segment_id": "pending:Alice:0",
                        "completed": False,
                    }
                ],
            }
        )
        await orchestrator.handle_message(
            {
                "type": "transcript",
                "speaker": "Alice",
                "confirmed": [
                    {
                        "text": "カボスができる機能を教えて",
                        "speaker": "Alice",
                        "segment_id": "confirmed-1",
                        "completed": True,
                        "absolute_start_time": "100.0",
                    }
                ],
                "pending": [],
            }
        )

        self.assertEqual(len(groq.calls), 1)
        self.assertEqual(len(vexa.calls), 1)

    async def test_orchestrator_remembers_wake_transcript_ignored_while_busy(self):
        started = asyncio.Event()
        release = asyncio.Event()
        settings = Settings(
            wake_cooldown_ms=1,
            wake_response_playback_guard_ms=0,
            wake_ack_enabled=False,
            bot_echo_cooldown_ms=0,
            wake_same_speaker_dedupe_ms=1,
        )
        groq = FakeGroq()
        aivis = BlockingAivis(started, release)
        vexa = FakeVexa()
        orchestrator = WakeOrchestrator(settings, groq, aivis, vexa)

        task = asyncio.create_task(
            orchestrator.handle_segment(
                TranscriptSegment(
                    text="カボス、最初の質問",
                    speaker="Alice",
                    segment_id="seg-1",
                    completed=True,
                )
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)

        await orchestrator.handle_segment(
            TranscriptSegment(
                text="カボス?天気の情報わかるの?",
                speaker="Alice",
                segment_id="pending:Alice:0",
                completed=False,
                absolute_start_time="151.6",
            )
        )

        release.set()
        await task
        await asyncio.sleep(0.01)

        await orchestrator.handle_segment(
            TranscriptSegment(
                text="カボス?天気の情報わかるの?",
                speaker="Alice",
                segment_id="confirmed-replayed",
                completed=True,
                absolute_start_time="151.6",
            )
        )

        self.assertEqual(len(groq.calls), 1)
        self.assertEqual(len(vexa.calls), 1)

    async def test_orchestrator_respects_wake_ack_disabled_but_answers(self):
        settings = Settings(
            wake_cooldown_ms=1,
            wake_response_playback_guard_ms=0,
            wake_ack_enabled=False,
            bot_echo_cooldown_ms=1,
        )
        vexa = FakeVexa()
        groq = FakeGroq()
        aivis = FakeAivis()
        orchestrator = WakeOrchestrator(settings, groq, aivis, vexa)

        await orchestrator.handle_segment(
            TranscriptSegment(
                text="カボス、答えて",
                speaker="Alice",
                segment_id="seg-1",
                completed=True,
            )
        )

        self.assertEqual(len(groq.calls), 1)
        self.assertEqual(len(aivis.calls), 1)
        self.assertEqual(len(vexa.calls), 1)
        self.assertEqual(orchestrator.state, WakeState.IDLE)

    async def test_orchestrator_ignores_vexa_wake_word(self):
        settings = Settings(wake_cooldown_ms=1, wake_response_playback_guard_ms=0)
        vexa = FakeVexa()
        orchestrator = WakeOrchestrator(settings, FakeGroq(), FakeAivis(), vexa)

        await orchestrator.handle_segment(
            TranscriptSegment(
                text="ねえVexa、今の論点まとめて。",
                speaker="Alice",
                segment_id="seg-1",
                completed=True,
            )
        )

        self.assertEqual(vexa.calls, [])


if __name__ == "__main__":
    unittest.main()

"""Entrypoint for the Kabosu Wake Orchestrator."""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from dotenv import load_dotenv

from .clients import (
    AivisCloudClient,
    GroqClient,
    MeetingRef,
    VexaClient,
    VexaTranscriptSubscriber,
    WakeSttTranscriptSubscriber,
)
from .config import Settings
from .orchestrator import WakeOrchestrator


CHAT_EVENT_TYPES = {"chat.received", "chat.sent", "chat.messages"}


def _message_type(message: dict) -> str:
    return str(message.get("type") or message.get("event") or "")


def _is_chat_event(message: dict) -> bool:
    return _message_type(message) in CHAT_EVENT_TYPES


async def run() -> None:
    load_dotenv()
    settings = Settings.from_env()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    missing = settings.missing_required()
    if missing:
        raise SystemExit(f"Missing required environment variables: {', '.join(missing)}")

    groq = GroqClient(settings)
    aivis = AivisCloudClient(settings)
    vexa = VexaClient(settings)
    if settings.wake_stt_url:
        subscriber = WakeSttTranscriptSubscriber(settings)
        chat_subscriber = (
            VexaTranscriptSubscriber(settings, vexa) if settings.wake_chat_enabled else None
        )
        transcript_source = "wake-stt"
    else:
        subscriber = VexaTranscriptSubscriber(settings, vexa)
        chat_subscriber = None
        transcript_source = "vexa"
        logging.getLogger(__name__).info(
            "WAKE_STT_URL is not set; using Vexa transcript WebSocket fallback"
        )
    orchestrators: dict[str, WakeOrchestrator] = {}

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    async def route_message(message: dict) -> None:
        meeting = MeetingRef.from_message(message.get("_wake_meeting"))
        key = meeting.key if meeting else "default"
        orchestrator = orchestrators.get(key)
        if orchestrator is None:
            orchestrator = WakeOrchestrator(settings, groq, aivis, vexa, meeting)
            orchestrators[key] = orchestrator
            logging.getLogger(__name__).info("Tracking wake state for meeting=%s", key)
        await orchestrator.handle_message(message)

    async def consume(event_subscriber) -> None:
        async for message in event_subscriber.messages():
            await route_message(message)

    async def consume_chat() -> None:
        if chat_subscriber is None:
            return
        async for message in chat_subscriber.messages():
            if _is_chat_event(message):
                await route_message(message)

    async def ticker() -> None:
        while not stop_event.is_set():
            for orchestrator in list(orchestrators.values()):
                await orchestrator.tick()
            await asyncio.sleep(0.2)

    logging.getLogger(__name__).info(
        "Kabosu Wake Orchestrator started source=%s platform=%s meeting=%s auto_discover=%s wake_words=%s mode=vexa_realtime_llm ack_audio=%s",
        transcript_source,
        settings.vexa_platform,
        settings.vexa_native_meeting_id,
        settings.wake_auto_discover_bots,
        settings.wake_words,
        settings.wake_ack_audio_path or "app/assets/wake_ack_un_bang.wav",
    )

    tasks = [asyncio.create_task(consume(subscriber)), asyncio.create_task(ticker())]
    if chat_subscriber is not None:
        tasks.append(asyncio.create_task(consume_chat()))
    await stop_event.wait()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    if os.getenv("WAKE_ORCHESTRATOR_CHECK_CONFIG") == "1":
        load_dotenv()
        settings = Settings.from_env()
        missing = settings.missing_required()
        if missing:
            raise SystemExit(f"Missing required environment variables: {', '.join(missing)}")
        print("config ok")
        return
    asyncio.run(run())


if __name__ == "__main__":
    main()

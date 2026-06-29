"""FastAPI entrypoint for the Kabosu wake STT bridge."""

from __future__ import annotations

import logging
from urllib.parse import parse_qs

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
import uvicorn

from .config import Settings
from .service import AudioIngest, BroadcastHub, WakeSttService

load_dotenv()
settings = Settings.from_env()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Kabosu Wake STT", version="0.1.0")
hub = BroadcastHub()
service = WakeSttService(settings, hub)


def _token_from_authorization(value: str | None) -> str | None:
    if not value:
        return None
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return value.strip()


async def verify_token(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> None:
    if not settings.api_token:
        return
    token = _token_from_authorization(authorization) or x_api_key
    if token != settings.api_token:
        raise HTTPException(status_code=401, detail="Invalid or missing wake STT token")


def _websocket_token(websocket: WebSocket) -> str | None:
    auth = websocket.headers.get("authorization")
    header_token = _token_from_authorization(auth) or websocket.headers.get("x-api-key")
    if header_token:
        return header_token
    query = parse_qs(websocket.scope.get("query_string", b"").decode("utf-8"))
    values = query.get("token") or []
    return values[0] if values else None


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "healthy",
        "transcription_configured": bool(settings.transcription_url),
        "missing": [] if settings.transcription_url else ["WAKE_STT_TRANSCRIPTION_URL"],
        "language": settings.language or "auto",
    }


@app.post("/v1/audio/ingest", dependencies=[Depends(verify_token)])
async def ingest_audio(payload: AudioIngest) -> dict[str, object]:
    return await service.ingest(payload)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    if settings.api_token and _websocket_token(websocket) != settings.api_token:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    await hub.add(websocket)
    logger.info("wake-stt websocket connected")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.remove(websocket)
        logger.info("wake-stt websocket disconnected")


@app.on_event("shutdown")
async def shutdown() -> None:
    await service.close()


def main() -> None:
    if not settings.transcription_url:
        logger.warning("WAKE_STT_TRANSCRIPTION_URL is not configured; /v1/audio/ingest will return 503")
    uvicorn.run(app, host="0.0.0.0", port=8058)


if __name__ == "__main__":
    main()


from contextlib import asynccontextmanager
from io import BytesIO
import hmac
import logging
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field
import pillow_heif
import socketio

from weather_service import get_weather_summary, KID, PROJECT_ID
from event_service import get_event_summary
from message_service import (
    delete_all_messages,
    delete_message,
    init_message_db,
    insert_message,
    list_deleted_messages,
    list_messages,
    restore_deleted_message,
)

logger = logging.getLogger(__name__)

socket_server = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR.parent / "web"
UPLOADS_DIR = APP_DIR / ".cache" / "uploads"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
LOCAL_BASE_URL = os.getenv("LOCAL_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
MANAGE_PASSWORD = os.getenv("MANAGE_PASSWORD", "1234")
MANAGE_SESSION_TTL_SECONDS = 8 * 60 * 60
manage_sessions: dict[str, float] = {}
if len(MANAGE_PASSWORD) != 4 or not MANAGE_PASSWORD.isascii() or not MANAGE_PASSWORD.isdigit():
    raise RuntimeError("MANAGE_PASSWORD must contain exactly four ASCII digits")
HEIC_CONTENT_TYPES = {
    "image/heic",
    "image/heif",
    "image/heic-sequence",
    "image/heif-sequence",
}
HEIC_SUFFIXES = {".heic", ".heif"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    init_message_db()
    if not KID or not PROJECT_ID:
        logger.warning("QWEATHER_KID or QWEATHER_PROJECT_ID not set — weather requests will fail")
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR), check_dir=False), name="uploads")
socket_app = socketio.ASGIApp(socket_server, other_asgi_app=app)


class MessageCreateRequest(BaseModel):
    type: Literal["text", "image", "notify"]
    content: str = Field(min_length=1)
    sub_content: str | None = None
    source_name: str | None = None


class MessageNotifyWebhookRequest(BaseModel):
    app_name: str = Field(min_length=1)
    body: str = Field(min_length=1)


class ManageLoginRequest(BaseModel):
    password: str = Field(pattern=r"^[0-9]{4}$")


def require_manage_session(authorization: str | None = Header(default=None)) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Manage authentication required")

    token = authorization.removeprefix("Bearer ")
    expires_at = manage_sessions.get(token)
    if expires_at is None or expires_at <= time.time():
        manage_sessions.pop(token, None)
        raise HTTPException(status_code=401, detail="Manage session expired")


@app.post("/api/manage/login")
def manage_login(request: ManageLoginRequest):
    if not hmac.compare_digest(request.password, MANAGE_PASSWORD):
        raise HTTPException(status_code=401, detail="密码错误")

    now = time.time()
    for token, expires_at in list(manage_sessions.items()):
        if expires_at <= now:
            manage_sessions.pop(token, None)

    token = secrets.token_urlsafe(32)
    manage_sessions[token] = now + MANAGE_SESSION_TTL_SECONDS
    return {"token": token, "expires_in": MANAGE_SESSION_TTL_SECONDS}


def _normalize_uploaded_image(
    data: bytes,
    filename: str | None,
    content_type: str | None,
) -> tuple[bytes, str]:
    suffix = Path(filename or "").suffix.lower()
    normalized_content_type = (content_type or "").lower()

    if suffix not in HEIC_SUFFIXES and normalized_content_type not in HEIC_CONTENT_TYPES:
        return data, suffix or ".jpg"

    pillow_heif.register_heif_opener()

    with Image.open(BytesIO(data)) as image:
        image.load()

        output = BytesIO()
        if "A" in image.getbands():
            rgba_image = image.convert("RGBA")
            background = Image.new("RGBA", rgba_image.size, (255, 255, 255, 255))
            flattened = Image.alpha_composite(background, rgba_image).convert("RGB")
            flattened.save(output, format="JPEG", quality=92, optimize=True)
        else:
            image.convert("RGB").save(output, format="JPEG", quality=92, optimize=True)

    return output.getvalue(), ".jpg"


@app.post("/api/messages")
async def create_message(request: MessageCreateRequest):
    if request.type == "image" and request.sub_content:
        raise HTTPException(status_code=400, detail="Image message does not support sub_content")
    if request.type == "text" and request.sub_content:
        raise HTTPException(status_code=400, detail="Text message subtitle is generated by server time")
    if request.type == "notify" and not request.source_name:
        raise HTTPException(status_code=400, detail="Notify messages require source_name")

    saved = insert_message(
        message_type=request.type,
        content=request.content,
        sub_content=request.sub_content,
        source_name=request.source_name,
    )
    await socket_server.emit("messages_updated", {"message": saved})
    return {"message": saved}


@app.get("/api/messages")
def get_messages():
    return {"messages": list_messages()}


@app.get("/api/messages/deleted", dependencies=[Depends(require_manage_session)])
def get_deleted_messages():
    return {"messages": list_deleted_messages()}


@app.post("/api/messages/upload-image")
async def upload_image_message(file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are allowed")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")

    data, suffix = _normalize_uploaded_image(data, file.filename, file.content_type)
    filename = f"{uuid4().hex}{suffix}"
    file_path = UPLOADS_DIR / filename

    file_path.write_bytes(data)
    base_url = PUBLIC_BASE_URL or LOCAL_BASE_URL
    image_url = f"{base_url}/uploads/{filename}"
    saved = insert_message(message_type="image", content=image_url)
    await socket_server.emit("messages_updated", {"message": saved})

    return {"url": image_url, "message": saved}


@app.post("/api/messages/webhook/notify")
async def webhook_notify_message(request: MessageNotifyWebhookRequest):
    saved = insert_message(
        message_type="notify",
        content=request.body,
        source_name=request.app_name,
    )
    await socket_server.emit("messages_updated", {"message": saved})
    return {"message": saved}


@app.delete("/api/messages/{message_id}", dependencies=[Depends(require_manage_session)])
async def delete_single_message(message_id: int):
    message = delete_message(message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    await socket_server.emit("messages_updated", {"deleted_id": message_id})
    return {"message": message}


@app.post("/api/messages/deleted/{message_id}/restore", dependencies=[Depends(require_manage_session)])
async def restore_single_message(message_id: int):
    message = restore_deleted_message(message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found in history")

    await socket_server.emit("messages_updated", {"restored_id": message_id})
    return {"message": message}


@app.post("/api/messages/clear", dependencies=[Depends(require_manage_session)])
async def clear_messages():
    removed = delete_all_messages()
    await socket_server.emit("messages_updated", {"cleared": True})
    return {"deleted_count": len(removed)}


@socket_server.event
async def connect(sid, environ, auth):
    logger.info("Socket.IO client connected: %s", sid)


@socket_server.event
async def disconnect(sid):
    logger.info("Socket.IO client disconnected: %s", sid)


@socket_server.event
async def request_weather(sid, data=None):
    try:
        await socket_server.emit("weather_data", get_weather_summary(), to=sid)
    except Exception:
        logger.exception("Failed to fetch weather data for %s", sid)
        await socket_server.emit("weather_error", {"error": "Failed to fetch weather data"}, to=sid)


@socket_server.event
async def request_event(sid, data=None):
    try:
        await socket_server.emit("event_data", get_event_summary(), to=sid)
    except Exception:
        logger.exception("Failed to fetch event data for %s", sid)
        await socket_server.emit("event_error", {"error": "Failed to fetch event data"}, to=sid)


app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "5000"))
    uvicorn.run(socket_app, host="0.0.0.0", port=port)

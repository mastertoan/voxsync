"""VoxSync local TTS server. Binds 127.0.0.1 only; every route requires X-VoxSync-Token. No CORS:
the only client is the extension service worker, which is exempt via host_permissions."""
import io
import logging
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import soundfile as sf
import uvicorn
from fastapi import Depends, FastAPI, File, Form, HTTPException, Path as PathParam, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from . import config
from .auth import ensure_home, load_or_create_token, require_token, set_token, token_ok
from .cache import DiskCache
from .episodes import Registry
from .normalize import normalize
from .voices import VoiceStore

log = logging.getLogger("voxsync")


class State:
    tts = None
    cache: DiskCache
    voices: VoiceStore
    registry: Registry
    previews: dict[tuple[str, str], bytes] = {}


S = State()


def build(tts, cache: DiskCache | None = None, voices: VoiceStore | None = None) -> None:
    """Wire components; tests call this with a fake engine."""
    S.tts = tts
    S.cache = cache or DiskCache()
    S.voices = voices or VoiceStore(tts)
    S.registry = Registry(tts, S.cache, S.voices)
    S.registry.start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if S.tts is None:                       # production start: load the real engine
        from .engine import load_tts
        set_token(load_or_create_token())
        build(load_tts())
    yield
    S.registry.stop()


app = FastAPI(title="VoxSync TTS", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None,
              dependencies=[Depends(require_token)])


@app.exception_handler(HTTPException)
async def _http_exc(_req: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
    return JSONResponse(detail, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def _invalid(_req: Request, exc: RequestValidationError):
    # never echo the input back (it may be non-finite / large); location + type is enough for the client
    errs = [{"loc": [str(x) for x in e.get("loc", ())], "type": e.get("type")} for e in exc.errors()]
    return JSONResponse({"code": "invalid_request", "errors": errs}, status_code=422)


class Guard:
    """Outermost ASGI layer: rejects bad tokens before any body is read, and caps the body by counting
    received bytes (covers chunked uploads with no Content-Length)."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)
        headers = dict(scope["headers"])
        if not token_ok(headers.get(b"x-voxsync-token")):
            return await JSONResponse({"code": "unauthorized"}, status_code=401)(scope, receive, send)
        limit = config.MAX_CLIP_BYTES + 64 * 1024 if scope["path"] == "/voices" else config.MAX_VTT_BYTES
        cl = headers.get(b"content-length")
        if cl is not None and (not cl.isdigit() or int(cl) > limit):
            return await JSONResponse({"code": "payload_too_large"}, status_code=413)(scope, receive, send)
        # Read the whole (capped) body here, then replay it: an exception raised from inside receive() would
        # be swallowed by FastAPI's body parser and turned into a 400.
        chunks, seen = [], 0
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            body = msg.get("body", b"")
            seen += len(body)
            if seen > limit:
                return await JSONResponse({"code": "payload_too_large"}, status_code=413)(scope, receive, send)
            chunks.append(body)
            if not msg.get("more_body"):
                break
        replayed = False

        async def replay():
            nonlocal replayed
            if replayed:
                return await receive()               # after the body: disconnect notifications
            replayed = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        await self.inner(scope, replay, send)


def _not_found(code: str):
    raise HTTPException(404, {"code": code})


def _ep(key: str):
    return S.registry.get(key) or _not_found("unknown_episode")


# ---- health / voices ----
@app.get("/health")
def health():
    return {"ok": True, "model": config.TTS_REPO, "revision": config.TTS_REVISION[:12], "backend": "onnx",
            "voices": len(S.voices.list()), "defaultVoice": S.voices.default_id, "cacheBytes": S.cache.bytes,
            "episodes": S.registry.episode_count(), "rtf": S.registry.rtf(), "diskFull": S.registry.disk_full()}


@app.get("/voices")
def list_voices():
    return {"default": S.voices.default_id, "voices": S.voices.list()}


@app.post("/voices", status_code=201)
async def create_voice(name: str = Form(..., min_length=1, max_length=60), consent: bool = Form(...),
                       gender: str = Form("", max_length=10), clip: UploadFile = File(...)):
    if not consent:
        raise HTTPException(400, {"code": "consent_required"})
    data = await clip.read(config.MAX_CLIP_BYTES + 1)
    if len(data) > config.MAX_CLIP_BYTES:
        raise HTTPException(413, {"code": "payload_too_large"})
    with tempfile.TemporaryDirectory() as d:
        p = Path(d, "clip")
        p.write_bytes(data)
        try:
            dur = sf.info(p).duration
        except Exception:
            raise HTTPException(400, {"code": "unsupported_audio"})
        if not config.MIN_CLIP_SECONDS <= dur <= config.MAX_CLIP_SECONDS:
            raise HTTPException(400, {"code": "clip_duration", "min": config.MIN_CLIP_SECONDS,
                                      "max": config.MAX_CLIP_SECONDS, "got": round(dur, 2)})
        v = await run_in_threadpool(S.voices.create, name.strip(), p, gender)   # ONNX enroll: off the loop
    return v.public()


class Rename(BaseModel):
    name: str = Field(min_length=1, max_length=60)


def _mutate_user_voice(fn, vid: str):
    try:
        return fn()
    except KeyError:
        _not_found("unknown_voice")
    except PermissionError:
        raise HTTPException(403, {"code": "preset_voice_readonly"})


@app.patch("/voices/{vid}")
def rename_voice(vid: str, body: Rename):
    return _mutate_user_voice(lambda: S.voices.rename(vid, body.name.strip()).public(), vid)


@app.delete("/voices/{vid}", status_code=204)
def delete_voice(vid: str):
    _mutate_user_voice(lambda: S.voices.delete(vid), vid)
    S.previews = {k: v for k, v in S.previews.items() if k[0] != vid}
    S.registry.reassign_voice(vid)


class Preview(BaseModel):
    text: str | None = Field(default=None, max_length=config.MAX_PREVIEW_CHARS)


@app.post("/voices/{vid}/preview")
def preview_voice(vid: str, body: Preview | None = None):
    if S.voices.get(vid) is None:
        _not_found("unknown_voice")
    text = normalize((body.text if body and body.text else None) or "Xin chào, đây là giọng đọc thử của VoxSync.")
    key = (vid, text)
    if key not in S.previews:
        wav = S.tts.infer(text, voice=vid, apply_watermark=False)
        buf = io.BytesIO()
        sf.write(buf, wav, S.tts.sample_rate, subtype="PCM_16", format="WAV")
        if len(S.previews) > 64:
            S.previews.clear()
        S.previews[key] = buf.getvalue()
    return Response(S.previews[key], media_type="audio/wav")


# ---- episodes ----
class CueIn(BaseModel):
    idx: int = Field(ge=0)
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(ge=0, allow_inf_nan=False)
    text: str = Field(max_length=config.MAX_CUE_CHARS)

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, v, info):
        if v < info.data.get("start", 0):
            raise ValueError("end < start")
        return v


class EpisodeIn(BaseModel):
    voice: str | None = Field(default=None, max_length=80)
    cues: list[CueIn] = Field(max_length=config.MAX_CUES)

    @field_validator("cues")
    @classmethod
    def _unique_idx(cls, v):
        if len({c.idx for c in v}) != len(v):
            raise ValueError("duplicate cue idx")
        return v


KEY = r"^[A-Za-z0-9_-]{8,64}$"
EpKey = PathParam(pattern=KEY)


@app.put("/episodes/{key}")
def put_episode(body: EpisodeIn, key: str = EpKey):
    ep = S.registry.put(key, body.voice, [c.model_dump() for c in body.cues])
    return {"cues": len(ep.cues), "voice": ep.voice}


class Playhead(BaseModel):
    t: float = Field(ge=0, allow_inf_nan=False)


@app.post("/episodes/{key}/playhead")
def set_playhead(key: str, body: Playhead):
    ep = S.registry.set_playhead(key, body.t) or _not_found("unknown_episode")
    return S.registry.status(ep)


@app.get("/episodes/{key}/status")
def episode_status(key: str):
    return S.registry.status(_ep(key))


RETRY = {"Retry-After": "1", "X-Retry-Ms": "250"}   # Retry-After must be whole seconds; client uses X-Retry-Ms


@app.get("/episodes/{key}/cues/{idx}")
def get_cue(key: str, idx: int):
    """200 wav | 202 not yet | 204 known but silent (dropped, or SFX-only text) | 404 unknown episode/cue |
    410 synthesis failed for good. X-Delay = this cue's own start delay (s): play at start + X-Delay."""
    ep = _ep(key)
    c = ep.by_idx.get(idx)
    if c is None:
        if idx in ep.empty_idx:
            return Response(status_code=204, headers={"X-Reason": "empty"})
        _not_found("unknown_cue")
    if S.registry.is_failed(c):
        raise HTTPException(410, {"code": "synth_failed"})
    if not c.ready:
        if S.registry.disk_full():
            raise HTTPException(507, {"code": "disk_full"})
        return Response(status_code=202, headers=RETRY)
    if c.dropped:
        return Response(status_code=204, headers={"X-Reason": "dropped", "X-Delay": f"{c.delay:.3f}"})
    data = S.cache.read_bytes(c.audio_key)
    if data is None:                       # evicted or corrupt since it was prepared: resynthesize
        S.registry.invalidate(ep, c)
        return Response(status_code=202, headers=RETRY)
    return Response(data, media_type="audio/wav",
                    headers={"X-Stretch": f"{c.rate:.3f}", "X-Delay": f"{c.delay:.3f}"})


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ensure_home()
    set_token(load_or_create_token())
    log.info("token file: %s", config.TOKEN_FILE)
    uvicorn.run(asgi, host=config.HOST, port=config.PORT, log_level="info", access_log=False)


asgi = Guard(app)


if __name__ == "__main__":
    run()

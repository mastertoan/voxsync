"""Paths, limits and pinned model revisions. Everything lives under VOXSYNC_HOME (default ~/.voxsync)."""
import os
from pathlib import Path

HOME = Path(os.environ.get("VOXSYNC_HOME") or Path.home() / ".voxsync")
CACHE_DIR = HOME / "cache"
MODELS_DIR = HOME / "models"
VOICES_FILE = HOME / "voices.json"
TOKEN_FILE = HOME / "token"

HOST = "127.0.0.1"   # never configurable to a LAN address: the API has no per-origin auth beyond the token
PORT = int(os.environ.get("VOXSYNC_PORT", "8765"))

# Pinned upstream artifacts (Phase 0 measured on exactly these).
TTS_REPO = "pnnbao-ump/VieNeu-TTS-v3-Turbo"
TTS_REVISION = "61b85e3d937fbbacb387714180e8182823512523"
CODEC_REPO = "OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX"
CODEC_REVISION = "ceff0d0749bfb3fa2d61149794ec6feef0d1e1ae"
ONNX_SUBFOLDER = "onnx_update"   # fp32; int8 stays opt-in until human ABX
ORT_THREADS = int(os.environ.get("VOXSYNC_THREADS", "6"))

CACHE_MAX_BYTES = int(os.environ.get("VOXSYNC_CACHE_MAX_BYTES", str(5 * 1024**3)))
MAX_VTT_BYTES = 2 * 1024**2
MAX_CUES = 5000
MAX_CUE_CHARS = 500
MAX_CLIP_BYTES = 10 * 1024**2
MAX_CLIP_SECONDS = 15.0
MIN_CLIP_SECONDS = 2.0
MAX_PREVIEW_CHARS = 200

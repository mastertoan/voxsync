"""Voice catalog: VieNeu built-in presets + user clones persisted in VOICES_FILE.

Upstream `save_voices()` rewrites the package's own preset file (wiped on upgrade), so user voices are
kept in our own file with the same per-voice layout (speaker_emb + codes) and re-registered at startup.
Voice ids are opaque: presets keep their upstream name; user voices get "u_<hex>" so a rename never
invalidates cache keys.
"""
import json
import os
import re
import secrets
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import config

_REGION = re.compile(r"\b(Bắc|Trung|Nam)\b")


@dataclass
class Voice:
    id: str
    name: str
    gender: str
    region: str
    style: str
    kind: str   # "preset" | "user"

    def public(self) -> dict:
        return self.__dict__.copy()


def _parse_desc(desc: str) -> tuple[str, str]:
    """Upstream description looks like 'Nữ · Bắc · Phong cách tin tức'."""
    parts = [p.strip() for p in desc.split("·")]
    region = next((m.group(1) for p in parts[1:2] if (m := _REGION.search(p))), "")
    style = parts[2] if len(parts) > 2 else ""
    return region, style


class VoiceStore:
    def __init__(self, tts, path: Path = config.VOICES_FILE):
        self.tts, self.path = tts, path
        self._lock = threading.Lock()
        self._voices: dict[str, Voice] = {}
        for vid, v in tts._preset_voices.items():
            region, style = _parse_desc(v.get("description", ""))
            self._voices[vid] = Voice(vid, vid, v.get("gender", ""), region, style, "preset")
        self.default_id: str = tts._default_voice
        self._load_user()

    # ---- persistence ----
    def _load_user(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for vid, v in data.get("voices", {}).items():
            self.tts._preset_voices[vid] = {
                "description": "", "gender": v.get("gender", ""),
                "speaker_emb": np.asarray(v["speaker_emb"], dtype=np.float32),
                "codes": None if v.get("codes") is None else np.asarray(v["codes"], dtype=np.int64),
            }
            self._voices[vid] = Voice(vid, v["name"], v.get("gender", ""), "", "", "user")

    def _save_user(self) -> None:
        out = {}
        for vid, meta in self._voices.items():
            if meta.kind != "user":
                continue
            p = self.tts._preset_voices[vid]
            out[vid] = {
                "name": meta.name, "gender": meta.gender,
                "speaker_emb": [round(float(x), 6) for x in np.asarray(p["speaker_emb"]).reshape(-1)],
                "codes": None if p.get("codes") is None else np.asarray(p["codes"]).tolist(),
            }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "voices": out}, f, ensure_ascii=False)
        os.replace(tmp, self.path)

    # ---- queries ----
    def list(self) -> list[dict]:
        return [v.public() for v in self._voices.values()]

    def get(self, vid: str) -> Voice | None:
        return self._voices.get(vid)

    def resolve(self, vid: str | None) -> str:
        """Unknown/empty → server default; never raises (episode keeps playing with a sane voice)."""
        return vid if vid in self._voices else self.default_id

    # ---- mutations (user voices only) ----
    def create(self, name: str, clip: Path, gender: str = "") -> Voice:
        vid = "u_" + secrets.token_hex(6)
        with self._lock:
            emb, codes = self.tts.encode_reference(str(clip), denoise=True)
            self.tts._preset_voices[vid] = {
                "description": "", "gender": gender,
                "speaker_emb": np.asarray(emb, dtype=np.float32),
                "codes": None if codes is None else np.asarray(codes, dtype=np.int64),
            }
            v = Voice(vid, name, gender, "", "", "user")
            self._voices[vid] = v
            self._save_user()
        return v

    def rename(self, vid: str, name: str) -> Voice:
        with self._lock:
            v = self._user(vid)
            v.name = name
            self._save_user()
        return v

    def delete(self, vid: str) -> None:
        with self._lock:
            self._user(vid)
            self._voices.pop(vid)
            self.tts._preset_voices.pop(vid, None)
            self._save_user()

    def _user(self, vid: str) -> Voice:
        v = self._voices.get(vid)
        if v is None:
            raise KeyError(vid)
        if v.kind != "user":
            raise PermissionError(vid)
        return v

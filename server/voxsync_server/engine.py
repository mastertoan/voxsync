"""Load VieNeu v3 Turbo fully from local snapshots (pinned revision).

Materializing to a real directory (not the HF symlink cache) also sidesteps the ORT >=1.31 external-data
check that rejects symlinked `*.data` files (Phase 0 finding).
"""
import logging
import time
from pathlib import Path

from huggingface_hub import snapshot_download

from . import config

log = logging.getLogger("voxsync.engine")

_TTS_PATTERNS = ["config.json", "denoiser.onnx", "speaker_encoder.onnx", "voices_v3_turbo.json",
                 f"{config.ONNX_SUBFOLDER}/*"]


def ensure_models(models_dir: Path = config.MODELS_DIR) -> tuple[Path, Path]:
    tts_dir = models_dir / "vieneu-v3-turbo" / config.TTS_REVISION[:12]
    codec_dir = models_dir / "moss-codec-onnx"
    if not (tts_dir / config.ONNX_SUBFOLDER / "vieneu_prefill.onnx").is_file():
        log.info("downloading %s@%s → %s", config.TTS_REPO, config.TTS_REVISION[:12], tts_dir)
        snapshot_download(config.TTS_REPO, revision=config.TTS_REVISION, allow_patterns=_TTS_PATTERNS,
                          local_dir=tts_dir)
    if not (codec_dir / "moss_audio_tokenizer_decode_full.onnx").is_file():
        log.info("downloading %s → %s", config.CODEC_REPO, codec_dir)
        snapshot_download(config.CODEC_REPO, revision=config.CODEC_REVISION, local_dir=codec_dir)
    return tts_dir, codec_dir


def load_tts():
    from vieneu import Vieneu
    from vieneu._v3_turbo_engine import onnx_runtime_lite as lite

    tts_dir, codec_dir = ensure_models()
    # vieneu 3.8.4's V3TurboVieNeuTTS does not forward `codec_dir` to OnnxV3LiteEngine (and the engine's
    # codec_repo default is bound at import), so it would fetch the codec unpinned from the HF hub on every
    # start. Inject our pinned local snapshot.
    if not getattr(lite.OnnxV3LiteEngine.__init__, "_voxsync", False):
        _orig_init = lite.OnnxV3LiteEngine.__init__

        def _init(self, *a, codec_dir=None, **kw):
            _orig_init(self, *a, codec_dir=codec_dir or str(_init.codec_dir), **kw)

        _init._voxsync = True
        lite.OnnxV3LiteEngine.__init__ = _init
    lite.OnnxV3LiteEngine.__init__.codec_dir = codec_dir
    t0 = time.perf_counter()
    tts = Vieneu(backend="onnx", backbone_repo=str(tts_dir), onnx_dir=str(tts_dir / config.ONNX_SUBFOLDER),
                 threads=config.ORT_THREADS)
    tts.infer("Xin chào.", apply_watermark=False)        # warm-up: first call pays graph init
    log.info("VieNeu ready in %.1fs (backend=%s, voices=%d)", time.perf_counter() - t0, tts.backend,
             len(tts._preset_voices))
    return tts

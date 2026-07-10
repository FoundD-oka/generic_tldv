import importlib
import io
import os
import sys

import numpy as np
import pytest
import soundfile as sf

SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SERVICE_ROOT not in sys.path:
    sys.path.insert(0, SERVICE_ROOT)


def make_wav_bytes(duration_seconds: float, sample_rate: int = 16000) -> bytes:
    """Build a small synthetic mono WAV (no real speech, no PII) for tests."""
    n_samples = max(1, int(duration_seconds * sample_rate))
    t = np.linspace(0, duration_seconds, n_samples, endpoint=False, dtype=np.float32)
    tone = 0.1 * np.sin(2 * np.pi * 220.0 * t).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, tone, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


@pytest.fixture
def load_app(monkeypatch):
    """Reload main.py with the given env vars applied first.

    main.py reads all config from os.environ at import time, so tests that
    need non-default config (token, byte caps, concurrency limits, ...)
    must reload the module after setting env vars via monkeypatch.
    """

    def _load(**env):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        if "main" in sys.modules:
            module = importlib.reload(sys.modules["main"])
        else:
            module = importlib.import_module("main")
        return module

    return _load

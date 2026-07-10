"""Fernet-based encryption for voiceprint embeddings (issue #27 Phase 4).

`VOICEPRINT_ENCRYPTION_KEY` missing or invalid degrades the whole voiceprint
feature to "disabled mode" rather than failing hard or ever storing a
plaintext embedding (PII policy §4, plan §4 blocker resolution): enroll
returns 503, matching is skipped (+ audited), and existing rows are never
decrypted. NEVER log the key itself or a decrypted vector.
"""
from __future__ import annotations

import logging
import os
import struct
from typing import List, Optional

logger = logging.getLogger("meeting_api.voiceprint_crypto")

DEFAULT_KEY_ID = "default"


def _load_key(env_var: str = "VOICEPRINT_ENCRYPTION_KEY") -> Optional[bytes]:
    raw = os.getenv(env_var, "").strip()
    if not raw:
        return None
    try:
        from cryptography.fernet import Fernet

        key_bytes = raw.encode("ascii")
        Fernet(key_bytes)  # raises if the key is not a valid Fernet key
        return key_bytes
    except Exception:
        # Never log `raw` — even a malformed key must not be echoed.
        logger.error(
            "%s is set but is not a valid Fernet key — voiceprint feature disabled",
            env_var,
        )
        return None


class VoiceprintCrypto:
    """Wraps a single Fernet key.

    `key_id` is reserved for future key-ring rotation (plan §4 ops notes);
    Phase 4 always uses one fixed key_id since only one key is supported.
    """

    key_id: str = DEFAULT_KEY_ID

    def __init__(self, key: Optional[bytes] = None):
        self._key = key if key is not None else _load_key()
        self._fernet = None
        if self._key is not None:
            from cryptography.fernet import Fernet

            self._fernet = Fernet(self._key)

    def is_enabled(self) -> bool:
        return self._fernet is not None

    def encrypt_embedding(self, embedding: List[float]) -> bytes:
        if not self.is_enabled():
            raise RuntimeError(
                "voiceprint encryption is disabled (missing/invalid VOICEPRINT_ENCRYPTION_KEY)"
            )
        payload = struct.pack(f"<{len(embedding)}f", *[float(x) for x in embedding])
        return self._fernet.encrypt(payload)

    def decrypt_embedding(self, ciphertext: bytes, *, dim: int) -> List[float]:
        if not self.is_enabled():
            raise RuntimeError(
                "voiceprint encryption is disabled (missing/invalid VOICEPRINT_ENCRYPTION_KEY)"
            )
        payload = self._fernet.decrypt(bytes(ciphertext))
        return list(struct.unpack(f"<{dim}f", payload))


_singleton: Optional[VoiceprintCrypto] = None


def get_voiceprint_crypto() -> VoiceprintCrypto:
    """Process-wide singleton — reads VOICEPRINT_ENCRYPTION_KEY once.

    Tests that flip the env var must call `reset_voiceprint_crypto_cache()`
    afterwards so the next call re-reads it.
    """
    global _singleton
    if _singleton is None:
        _singleton = VoiceprintCrypto()
    return _singleton


def reset_voiceprint_crypto_cache() -> None:
    """Test hook: force re-read of VOICEPRINT_ENCRYPTION_KEY on next access."""
    global _singleton
    _singleton = None

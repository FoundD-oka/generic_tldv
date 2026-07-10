"""Issue #27 Phase 4 — VOICEPRINT_ENCRYPTION_KEY missing/invalid must
degrade to disabled mode, never store plaintext, never raise at import
time, and never log the key or a decrypted vector (plan §4 blocker
resolution)."""
from __future__ import annotations

from cryptography.fernet import Fernet

from meeting_api.voiceprint_crypto import VoiceprintCrypto


def test_missing_key_is_disabled():
    crypto = VoiceprintCrypto(key=None)
    assert crypto.is_enabled() is False


def test_disabled_encrypt_raises_instead_of_storing_plaintext():
    crypto = VoiceprintCrypto(key=None)
    try:
        crypto.encrypt_embedding([0.1, 0.2, 0.3])
        assert False, "encrypt_embedding must raise when disabled"
    except RuntimeError:
        pass


def test_disabled_decrypt_raises_and_never_attempts_decryption():
    crypto = VoiceprintCrypto(key=None)
    try:
        crypto.decrypt_embedding(b"whatever", dim=3)
        assert False, "decrypt_embedding must raise when disabled"
    except RuntimeError:
        pass


def test_valid_key_enables_roundtrip_encryption():
    key = Fernet.generate_key()
    crypto = VoiceprintCrypto(key=key)
    assert crypto.is_enabled() is True

    embedding = [0.125, -0.5, 1.0, 0.0] * 48  # 192-dim
    ciphertext = crypto.encrypt_embedding(embedding)
    assert isinstance(ciphertext, bytes)
    # Never plaintext: the raw floats must not appear as text in the blob.
    assert b"0.125" not in ciphertext

    decrypted = crypto.decrypt_embedding(ciphertext, dim=len(embedding))
    for original, restored in zip(embedding, decrypted):
        assert abs(original - restored) < 1e-5


def test_invalid_key_format_disables_via_env(monkeypatch):
    """A malformed VOICEPRINT_ENCRYPTION_KEY must disable the feature, not
    crash the process — read via _load_key's env path."""
    monkeypatch.setenv("VOICEPRINT_ENCRYPTION_KEY", "not-a-valid-fernet-key")
    crypto = VoiceprintCrypto()
    assert crypto.is_enabled() is False


def test_missing_key_env_disables(monkeypatch):
    monkeypatch.delenv("VOICEPRINT_ENCRYPTION_KEY", raising=False)
    crypto = VoiceprintCrypto()
    assert crypto.is_enabled() is False


def test_valid_key_env_enables(monkeypatch):
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("VOICEPRINT_ENCRYPTION_KEY", key)
    crypto = VoiceprintCrypto()
    assert crypto.is_enabled() is True


def test_key_id_is_the_fixed_default():
    """Phase 4 always writes one fixed key_id — key-ring rotation is
    reserved for a future phase (plan §4)."""
    assert VoiceprintCrypto.key_id == "default"

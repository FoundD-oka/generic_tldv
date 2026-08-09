"""Startup environment validation (ST-12).

Required env whose absence always breaks transcription must abort startup;
optional env must only warn. All env is read at call time, so every case here
is driven by monkeypatch inside a single process.
"""

import inspect
import logging

import pytest

from meeting_api.env_validation import collect_env_issues, validate_startup_env


_MANAGED_ENVS = (
    "TRANSCRIPTION_SERVICE_URL",
    "TRANSCRIPTION_SERVICE_TOKEN",
    "DEFERRED_TRANSCRIPTION_SERVICE_URL",
    "STORAGE_BACKEND",
    "GCS_BUCKET",
    "VOICEPRINT_SERVICE_URL",
    "INTERNAL_API_SECRET",
    "KABOSU_DRIVE_EXPORT_ENABLED",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "KABOSU_GOOGLE_REFRESH_TOKEN",
    "KABOSU_DRIVE_FOLDER_ID",
    "STARTUP_ENV_VALIDATION",
)

TEST_LOGGER = logging.getLogger("meeting_api.test_env_validation")


@pytest.fixture
def env(monkeypatch):
    """Empty, deterministic environment for the envs this module inspects."""
    for name in _MANAGED_ENVS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def valid_env(env):
    """Environment where no violation is expected."""
    env.setenv("TRANSCRIPTION_SERVICE_URL", "http://whisperlive:9090")
    return env


# ---------------------------------------------------------------------------
# AT-001 — TRANSCRIPTION_SERVICE_URL is required
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_transcription_service_url_is_violation(env, value):
    if value is not None:
        env.setenv("TRANSCRIPTION_SERVICE_URL", value)

    violations, _ = collect_env_issues()

    assert any("TRANSCRIPTION_SERVICE_URL" in v for v in violations)


def test_missing_transcription_service_url_raises_in_strict_mode(env):
    with pytest.raises(RuntimeError) as excinfo:
        validate_startup_env(TEST_LOGGER)

    assert "TRANSCRIPTION_SERVICE_URL" in str(excinfo.value)


def test_configured_transcription_service_url_has_no_violation(valid_env):
    violations, _ = collect_env_issues()

    assert violations == []
    validate_startup_env(TEST_LOGGER)  # must not raise


# ---------------------------------------------------------------------------
# AT-002 — storage configuration is validated up front
# ---------------------------------------------------------------------------

def test_gcs_backend_without_bucket_is_violation(valid_env):
    valid_env.setenv("STORAGE_BACKEND", "gcs")

    violations, _ = collect_env_issues()

    assert any("GCS_BUCKET" in v for v in violations)


def test_gcs_backend_with_bucket_has_no_violation(valid_env):
    valid_env.setenv("STORAGE_BACKEND", "gcs")
    valid_env.setenv("GCS_BUCKET", "vexa-recordings")

    violations, _ = collect_env_issues()

    assert violations == []


def test_unknown_storage_backend_is_violation(valid_env):
    valid_env.setenv("STORAGE_BACKEND", "foo")

    violations, _ = collect_env_issues()

    assert any("STORAGE_BACKEND" in v for v in violations)


def test_default_storage_backend_has_no_violation(valid_env):
    violations, _ = collect_env_issues()

    assert violations == []


@pytest.mark.parametrize("backend", ["minio", "s3", "gcs", "local"])
def test_known_storage_backends_are_accepted(valid_env, backend):
    valid_env.setenv("STORAGE_BACKEND", backend)
    valid_env.setenv("GCS_BUCKET", "vexa-recordings")

    violations, _ = collect_env_issues()

    assert violations == []


# ---------------------------------------------------------------------------
# AT-003 — all violations are aggregated into one error
# ---------------------------------------------------------------------------

def test_multiple_violations_are_all_reported(env):
    env.setenv("TRANSCRIPTION_SERVICE_URL", "")
    env.setenv("STORAGE_BACKEND", "gcs")
    env.setenv("GCS_BUCKET", "  ")

    violations, _ = collect_env_issues()
    assert len(violations) == 2

    with pytest.raises(RuntimeError) as excinfo:
        validate_startup_env(TEST_LOGGER)

    message = str(excinfo.value)
    assert "TRANSCRIPTION_SERVICE_URL" in message
    assert "GCS_BUCKET" in message


# ---------------------------------------------------------------------------
# AT-004 — escape hatch and fail-safe mode parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", [None, "strict", "yes"])
def test_strict_is_the_default_and_invalid_values_stay_strict(env, mode):
    if mode is not None:
        env.setenv("STARTUP_ENV_VALIDATION", mode)

    with pytest.raises(RuntimeError):
        validate_startup_env(TEST_LOGGER)


def test_warn_mode_downgrades_violations_to_warnings(env, caplog):
    env.setenv("STARTUP_ENV_VALIDATION", "warn")

    with caplog.at_level(logging.WARNING, logger=TEST_LOGGER.name):
        validate_startup_env(TEST_LOGGER)  # must not raise

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("TRANSCRIPTION_SERVICE_URL" in m for m in warnings)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


# ---------------------------------------------------------------------------
# AT-005 — optional env only warns
# ---------------------------------------------------------------------------

def test_optional_envs_warn_without_blocking_startup(valid_env, caplog):
    valid_env.setenv("KABOSU_DRIVE_EXPORT_ENABLED", "true")

    violations, warnings = collect_env_issues()
    assert violations == []

    joined = " ".join(warnings)
    assert "TRANSCRIPTION_SERVICE_TOKEN" in joined
    assert "VOICEPRINT_SERVICE_URL" in joined
    assert "KABOSU_DRIVE_FOLDER_ID" in joined
    assert "INTERNAL_API_SECRET" in joined

    with caplog.at_level(logging.WARNING, logger=TEST_LOGGER.name):
        validate_startup_env(TEST_LOGGER)  # must not raise

    logged = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    # The disabled/degraded feature must be named for the operator.
    assert "speaker attribution" in logged
    assert "Drive export" in logged


def test_drive_warning_lists_every_missing_credential(valid_env):
    valid_env.setenv("KABOSU_DRIVE_EXPORT_ENABLED", "1")
    valid_env.setenv("GOOGLE_CLIENT_ID", "client-id")

    _, warnings = collect_env_issues()

    drive = [w for w in warnings if "Drive export" in w]
    assert len(drive) == 1
    assert "GOOGLE_CLIENT_SECRET" in drive[0]
    assert "KABOSU_GOOGLE_REFRESH_TOKEN" in drive[0]
    assert "KABOSU_DRIVE_FOLDER_ID" in drive[0]
    assert "GOOGLE_CLIENT_ID" not in drive[0]


def test_disabled_drive_export_does_not_warn(valid_env):
    valid_env.setenv("KABOSU_DRIVE_EXPORT_ENABLED", "false")

    _, warnings = collect_env_issues()

    assert not [w for w in warnings if "Drive export" in w]


def test_deferred_transcription_url_is_not_reported(valid_env):
    _, warnings = collect_env_issues()

    assert not [w for w in warnings if "DEFERRED_TRANSCRIPTION_SERVICE_URL" in w]


def test_no_warnings_when_optional_envs_are_set(valid_env):
    valid_env.setenv("TRANSCRIPTION_SERVICE_TOKEN", "token")
    valid_env.setenv("VOICEPRINT_SERVICE_URL", "http://voiceprint-service:8000")
    valid_env.setenv("INTERNAL_API_SECRET", "secret")
    valid_env.setenv("KABOSU_DRIVE_EXPORT_ENABLED", "false")

    violations, warnings = collect_env_issues()

    assert violations == []
    assert warnings == []


# ---------------------------------------------------------------------------
# NFT-001 — violation messages are self-contained
# ---------------------------------------------------------------------------

def test_violation_messages_explain_impact_and_example(env):
    env.setenv("STORAGE_BACKEND", "foo")

    violations, _ = collect_env_issues()

    assert len(violations) == 2
    for message in violations:
        assert "Example:" in message
        assert len(message.split()) > 8


# ---------------------------------------------------------------------------
# AT-006 — validation runs before init_db in startup()
# ---------------------------------------------------------------------------

def test_startup_validates_env_before_init_db():
    from meeting_api import main

    source = inspect.getsource(main.startup)

    assert "validate_startup_env" in source
    assert source.index("validate_startup_env") < source.index("init_db")

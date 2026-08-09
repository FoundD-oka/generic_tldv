"""Startup environment validation for the Meeting API (fail-fast).

Configuration mistakes that always break the primary feature (transcription)
must be detected when the process starts, not after a meeting has already been
recorded and lost. This module reads ``os.environ`` at call time (never at
import time) and reports:

- **violations** — required settings whose absence guarantees transcription /
  recording failure. In the default ``strict`` mode these abort startup.
- **warnings** — optional features that will simply be unavailable.

``STARTUP_ENV_VALIDATION=warn`` downgrades violations to warnings for partial
development environments. Any other value (including unset or an invalid one)
means ``strict`` (fail-safe).

The Redis and database connection settings are intentionally out of scope here:
``config.py`` and ``database.py`` already validate them at import time, and
duplicating those checks would only create two sources of truth.
"""

import logging
import os
from typing import List, Tuple

# Mirrors storage.py: the backend string is compared verbatim (no strip/lower),
# so any value outside this set raises at use time.
SUPPORTED_STORAGE_BACKENDS = ("minio", "s3", "gcs", "local")

# Mirrors drive_export.py: the export is enabled unless explicitly disabled.
_DRIVE_DISABLED_VALUES = {"0", "false", "no", "off"}

_DRIVE_CREDENTIAL_ENVS = (
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "KABOSU_GOOGLE_REFRESH_TOKEN",
    "KABOSU_DRIVE_FOLDER_ID",
)


def _stripped(name: str) -> str:
    """Value of ``name`` with surrounding whitespace removed ("" when unset)."""
    return (os.environ.get(name) or "").strip()


def _drive_export_enabled() -> bool:
    return (
        os.getenv("KABOSU_DRIVE_EXPORT_ENABLED", "true").strip().lower()
        not in _DRIVE_DISABLED_VALUES
    )


def collect_env_issues() -> Tuple[List[str], List[str]]:
    """Inspect the current environment.

    Returns ``(violations, warnings)`` — both are lists of operator-facing,
    self-contained one-line messages (env name, what breaks, example value).
    Pure and side-effect free: only ``os.environ`` is read, no network access.
    """
    violations: List[str] = []
    warnings: List[str] = []

    # --- Required: transcription endpoint -----------------------------------
    if not _stripped("TRANSCRIPTION_SERVICE_URL"):
        violations.append(
            "TRANSCRIPTION_SERVICE_URL is unset or empty — it is the transcription "
            "endpoint for both the realtime bot pipeline and deferred final "
            "transcription, so every meeting would be recorded without any "
            "transcript. Example: TRANSCRIPTION_SERVICE_URL=http://whisperlive:9090"
        )

    # --- Required: recording storage ----------------------------------------
    backend = os.environ.get("STORAGE_BACKEND", "minio")
    if backend not in SUPPORTED_STORAGE_BACKENDS:
        violations.append(
            f"STORAGE_BACKEND={backend!r} is not a supported backend — recording "
            "storage fails at use time and the audio needed for deferred "
            "transcription is lost. Supported values: "
            f"{', '.join(SUPPORTED_STORAGE_BACKENDS)}. Example: STORAGE_BACKEND=minio"
        )
    elif backend == "gcs" and not _stripped("GCS_BUCKET"):
        violations.append(
            "GCS_BUCKET is unset or empty while STORAGE_BACKEND=gcs — recording "
            "uploads fail and the meeting audio is lost. "
            "Example: GCS_BUCKET=vexa-recordings"
        )

    # --- Optional: degraded features ----------------------------------------
    if not _stripped("TRANSCRIPTION_SERVICE_TOKEN"):
        warnings.append(
            "TRANSCRIPTION_SERVICE_TOKEN is unset or empty — calls to the "
            "transcription service are unauthenticated (feature: transcription "
            "service authentication). Example: TRANSCRIPTION_SERVICE_TOKEN=<token>"
        )

    if not _stripped("VOICEPRINT_SERVICE_URL"):
        warnings.append(
            "VOICEPRINT_SERVICE_URL is unset or empty — speaker attribution "
            "(voiceprint matching) is disabled. "
            "Example: VOICEPRINT_SERVICE_URL=http://voiceprint-service:8000"
        )

    if _drive_export_enabled():
        missing_drive = [name for name in _DRIVE_CREDENTIAL_ENVS if not _stripped(name)]
        if missing_drive:
            warnings.append(
                "KABOSU_DRIVE_EXPORT_ENABLED is on but "
                f"{', '.join(missing_drive)} is unset or empty — Google Drive "
                "export will fail at run time (feature: Drive export). "
                "Set the credentials or disable with KABOSU_DRIVE_EXPORT_ENABLED=false"
            )

    if not _stripped("INTERNAL_API_SECRET"):
        warnings.append(
            "INTERNAL_API_SECRET is unset or empty — bot callback authentication "
            "(feature: internal service-to-service auth) is not enforced. "
            "Example: INTERNAL_API_SECRET=<shared-secret>"
        )

    return violations, warnings


def _strict_mode() -> bool:
    """True unless ``STARTUP_ENV_VALIDATION`` is exactly ``warn`` (fail-safe)."""
    return (os.environ.get("STARTUP_ENV_VALIDATION") or "").strip().lower() != "warn"


def validate_startup_env(logger: logging.Logger) -> None:
    """Log optional-feature warnings and fail fast on required-env violations.

    All violations are reported together (never short-circuited on the first
    one) so a single restart shows the operator the complete list.
    """
    violations, warnings = collect_env_issues()

    for message in warnings:
        logger.warning("Startup env warning: %s", message)

    if not violations:
        logger.info("Startup env validation passed (%d warning(s))", len(warnings))
        return

    if not _strict_mode():
        for message in violations:
            logger.warning(
                "Startup env violation (downgraded by STARTUP_ENV_VALIDATION=warn): %s",
                message,
            )
        return

    for message in violations:
        logger.error("Startup env violation: %s", message)

    raise RuntimeError(
        f"Startup environment validation failed ({len(violations)} violation(s)): "
        + " | ".join(violations)
        + " | Fix the settings above, or set STARTUP_ENV_VALIDATION=warn to start "
        "in a degraded development environment."
    )

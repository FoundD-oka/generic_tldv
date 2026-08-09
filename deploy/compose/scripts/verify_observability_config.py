#!/usr/bin/env python3
"""Static check of the compose observability config (ST-19 log rotation / ST-20 healthchecks).

Expands `deploy/compose/docker-compose.yml` with every profile via
`docker compose config --format json` and asserts:

  1. every expanded service has json-file logging with max-size + max-file
     (ST-19: an unbounded container log can fill the host disk),
  2. every service outside EXCLUDED_FROM_HEALTHCHECK declares a healthcheck,
  3. each healthcheck points at the endpoint it is supposed to point at --
     in particular meeting-api must probe /readyz, not the always-200 /health,
  4. the healthchecks that already existed before ST-20 are unchanged.

Only stdlib + the docker CLI are required, so this runs as-is in CI.

Usage: python3 deploy/compose/scripts/verify_observability_config.py
Exit code 0 = all checks pass, 1 = at least one violation.
"""

import json
import os
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
COMPOSE_FILE = os.path.join(REPO_ROOT, "deploy", "compose", "docker-compose.yml")

PROFILES = ["kabosu", "tts", "wake-stt", "wake", "voiceprint", "calendar"]

# Placeholder values for the variables the compose file has no default for.
# They only need to make the expansion deterministic; nothing is started.
DUMMY_ENV = {
    "IMAGE_TAG": "verify",
    "AGENT_IMAGE": "vexaai/vexa-agent:verify",
    "BROWSER_IMAGE": "vexaai/vexa-bot:verify",
    "CLAUDE_CREDENTIALS_PATH": "/dev/null",
    "CLAUDE_JSON_PATH": "/dev/null",
}

# Services that legitimately have no healthcheck.
EXCLUDED_FROM_HEALTHCHECK = {
    # One-shot bucket bootstrap: runs `mc mb` and exits 0, so there is no
    # long-running process for a healthcheck to report on.
    "minio-init",
    # Client-side polling loop with no HTTP server: nothing in-container to probe.
    "wake-orchestrator",
}

# Substrings every healthcheck test must contain, i.e. the endpoint each service
# is required to probe. Guards against a silent regression to a weaker endpoint
# (meeting-api /readyz -> /health is the one this task exists to prevent).
REQUIRED_TEST_FRAGMENTS = {
    "meeting-api": ["localhost:8080/readyz"],
    "runtime-api": ["localhost:8090/health"],
    "mcp": ["localhost:18888/health"],
    "dashboard": ["localhost:3000/api/health"],
    "kabosu-dashboard": ["localhost:3000/api/health"],
    "tts-service": ["localhost:8002/health"],
    "wake-stt": ["localhost:8058/health"],
    "calendar-service": ["localhost:8050/health"],
    "minio": ["localhost:9000/minio/health/live"],
}

# Healthchecks that predate ST-20 and must not be touched by it.
PRE_EXISTING_TEST_FRAGMENTS = {
    "redis": ["redis-cli", "ping"],
    "postgres": ["pg_isready"],
    "admin-api": ["localhost:8001/"],
    "api-gateway": ["localhost:8000/"],
    "voiceprint-service": ["curl", "localhost:8000/health"],
}


def load_config():
    """Return the fully expanded compose config as a dict."""
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as env_file:
        for key, value in DUMMY_ENV.items():
            env_file.write("%s=%s\n" % (key, value))
        env_path = env_file.name

    cmd = ["docker", "compose", "--env-file", env_path, "-f", COMPOSE_FILE]
    for profile in PROFILES:
        cmd += ["--profile", profile]
    cmd += ["config", "--format", "json"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    finally:
        os.unlink(env_path)

    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit("docker compose config failed (exit %d)" % result.returncode)
    return json.loads(result.stdout)


def test_as_text(service):
    test = (service.get("healthcheck") or {}).get("test") or []
    if isinstance(test, str):
        return test
    return " ".join(str(part) for part in test)


def main():
    config = load_config()
    services = config.get("services") or {}
    if not services:
        raise SystemExit("no services in the expanded compose config")

    violations = []

    # --- Check 1: log rotation on every service (ST-19) ---
    for name in sorted(services):
        logging = services[name].get("logging") or {}
        options = logging.get("options") or {}
        if logging.get("driver") != "json-file":
            violations.append(
                "%s: logging.driver is %r, expected 'json-file'"
                % (name, logging.get("driver"))
            )
            continue
        for option in ("max-size", "max-file"):
            if not options.get(option):
                violations.append("%s: logging.options.%s is missing" % (name, option))

    # --- Check 2: healthcheck on every non-excluded service (ST-20) ---
    for name in sorted(services):
        has_test = bool((services[name].get("healthcheck") or {}).get("test"))
        if name in EXCLUDED_FROM_HEALTHCHECK:
            if has_test:
                violations.append(
                    "%s: listed in EXCLUDED_FROM_HEALTHCHECK but declares a healthcheck; "
                    "update the exclusion list" % name
                )
        elif not has_test:
            violations.append("%s: no healthcheck.test" % name)

    # --- Check 3: healthchecks probe the intended endpoints ---
    for name, fragments in sorted(REQUIRED_TEST_FRAGMENTS.items()):
        if name not in services:
            violations.append("%s: expected service is missing from the config" % name)
            continue
        text = test_as_text(services[name])
        for fragment in fragments:
            if fragment not in text:
                violations.append(
                    "%s: healthcheck test %r does not contain %r" % (name, text, fragment)
                )

    # --- Check 4: pre-existing healthchecks unchanged ---
    for name, fragments in sorted(PRE_EXISTING_TEST_FRAGMENTS.items()):
        if name not in services:
            violations.append("%s: expected service is missing from the config" % name)
            continue
        text = test_as_text(services[name])
        for fragment in fragments:
            if fragment not in text:
                violations.append(
                    "%s: pre-existing healthcheck changed; test %r no longer contains %r"
                    % (name, text, fragment)
                )

    print("services checked: %d (%s)" % (len(services), ", ".join(sorted(services))))
    print("healthcheck exclusions: %s" % ", ".join(sorted(EXCLUDED_FROM_HEALTHCHECK)))

    if violations:
        print("\nFAIL: %d violation(s)" % len(violations))
        for violation in violations:
            print("  - %s" % violation)
        return 1

    print("\nOK: log rotation, healthcheck coverage and endpoints all verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())

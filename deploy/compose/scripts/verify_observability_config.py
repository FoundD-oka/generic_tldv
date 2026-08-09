#!/usr/bin/env python3
"""Static check of the compose config (ST-19/ST-20 observability, ST-24/ST-25 limits+deps).

Expands `deploy/compose/docker-compose.yml` with every profile via
`docker compose config --format json` and asserts:

  1. every expanded service has json-file logging with max-size + max-file
     (ST-19: an unbounded container log can fill the host disk),
  2. every service outside EXCLUDED_FROM_HEALTHCHECK declares a healthcheck,
  3. each healthcheck points at the endpoint it is supposed to point at --
     in particular meeting-api must probe /readyz, not the always-200 /health,
  4. the healthchecks that already existed before ST-20 are unchanged,
  5. runtime-api declares a memory limit (ST-24),
  6. every depends_on condition matches EXPECTED_DEPENDS exactly (ST-25),
  7. no structural deadlock: every `service_healthy` target owns an enabled
     healthcheck,
  8. no service with `restart: "no"` waits on a healthy/completed condition.

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

# ST-24: services that must declare a memory limit, in bytes. runtime-api is the
# audited one; the value mirrors the load-tested helm limit
# (deploy/helm/charts/vexa/values.yaml runtimeApi.resources.limits.memory: 512Mi)
# so a compose-only bump cannot silently diverge from what was measured.
REQUIRED_MEMORY_LIMIT_BYTES = {
    "runtime-api": 512 * 1024 * 1024,
}

# ST-25: the full depends_on graph, asserted for exact equality against the
# expanded config. Exact equality (not "at least") is the point: a *removed*
# condition is as much of a regression as a wrong one, and a newly added
# dependency has to be reviewed here before it can ship.
#
# `service_healthy` is only used for infrastructure that becomes healthy on its
# own (redis / postgres / minio) plus admin-api, which predates ST-25. The
# application dependencies stay on `service_started` on purpose -- a condition
# pointed at a service that can legitimately stay un-healthy would stop the
# whole stack from starting (see the comments in docker-compose.yml).
EXPECTED_DEPENDS = {
    "admin-api": {"postgres": "service_healthy"},
    "api-gateway": {
        "admin-api": "service_healthy",
        "meeting-api": "service_started",
        "redis": "service_healthy",
    },
    "calendar-service": {
        "meeting-api": "service_started",
        "postgres": "service_healthy",
    },
    "dashboard": {"api-gateway": "service_started"},
    "kabosu-dashboard": {"api-gateway": "service_started"},
    "mcp": {"api-gateway": "service_started"},
    "meeting-api": {
        "minio-init": "service_completed_successfully",
        "postgres": "service_healthy",
        "redis": "service_healthy",
        "runtime-api": "service_started",
    },
    "minio-init": {"minio": "service_healthy"},
    "runtime-api": {"postgres": "service_healthy", "redis": "service_healthy"},
    "wake-orchestrator": {"api-gateway": "service_started"},
}

# Conditions that block startup until the dependency reaches a state the
# dependency itself has to reach. A service that is never restarted cannot
# recover if such a condition is not met, so check 8 forbids the combination.
BLOCKING_CONDITIONS = ("service_healthy", "service_completed_successfully")

# An explicit "never restart" policy, as normalised by `docker compose config`
# (measured 2026-08-09 with compose v5.1.4: the YAML string "no" stays the
# string "no"; "none" is the equivalent spelling in some compose versions).
NO_RESTART_VALUES = ("no", "none")


def memory_limit_bytes(service):
    """Return the service's memory limit in bytes, or None if it has none.

    RF-101: `docker compose config --format json` normalises `mem_limit: 512m`
    to a top-level `mem_limit` holding a byte count (measured 2026-08-09 with
    compose v5.1.4: the string "536870912"). Older/newer compose versions and
    swarm-style configs express the same cap as
    `deploy.resources.limits.memory`, so both spellings are accepted here.
    """
    raw = service.get("mem_limit")
    if raw is None:
        raw = (
            ((service.get("deploy") or {}).get("resources") or {}).get("limits") or {}
        ).get("memory")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    text = str(raw).strip()
    if text.isdigit():
        return int(text)
    units = {"b": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}
    suffix = text[-1:].lower()
    if suffix in units and text[:-1].replace(".", "", 1).isdigit():
        return int(float(text[:-1]) * units[suffix])
    return None


def depends_conditions(service):
    """Return {dependency: condition} for a service, both compose spellings."""
    depends_on = service.get("depends_on") or {}
    if isinstance(depends_on, list):
        # Short list form; compose expands it to service_started.
        return dict((name, "service_started") for name in depends_on)
    return dict(
        (name, (spec or {}).get("condition")) for name, spec in depends_on.items()
    )


def has_enabled_healthcheck(service):
    healthcheck = service.get("healthcheck") or {}
    return bool(healthcheck.get("test")) and not healthcheck.get("disable")


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

    # --- Check 5: memory limits (ST-24) ---
    for name, expected_bytes in sorted(REQUIRED_MEMORY_LIMIT_BYTES.items()):
        if name not in services:
            violations.append("%s: expected service is missing from the config" % name)
            continue
        actual = memory_limit_bytes(services[name])
        if actual is None:
            violations.append(
                "%s: no memory limit (expected mem_limit or "
                "deploy.resources.limits.memory = %d bytes)" % (name, expected_bytes)
            )
        elif actual != expected_bytes:
            violations.append(
                "%s: memory limit is %d bytes, expected %d"
                % (name, actual, expected_bytes)
            )

    # --- Check 6: depends_on conditions match EXPECTED_DEPENDS exactly (ST-25) ---
    actual_depends = dict(
        (name, depends_conditions(services[name]))
        for name in services
        if services[name].get("depends_on")
    )
    for name in sorted(set(actual_depends) | set(EXPECTED_DEPENDS)):
        expected = EXPECTED_DEPENDS.get(name)
        actual = actual_depends.get(name)
        if expected is None:
            violations.append(
                "%s: depends_on %r is not declared in EXPECTED_DEPENDS; review the "
                "startup ordering and add it" % (name, actual)
            )
        elif name not in services:
            violations.append("%s: expected service is missing from the config" % name)
        elif actual != expected:
            violations.append(
                "%s: depends_on is %r, expected %r" % (name, actual, expected)
            )

    # --- Check 7: no structural deadlock on service_healthy targets ---
    for name in sorted(services):
        for dependency, condition in sorted(depends_conditions(services[name]).items()):
            if condition != "service_healthy":
                continue
            if dependency not in services:
                violations.append(
                    "%s: depends on %s (service_healthy) but that service is not in "
                    "the config" % (name, dependency)
                )
            elif not has_enabled_healthcheck(services[dependency]):
                violations.append(
                    "%s: waits for %s to be healthy but %s has no enabled healthcheck; "
                    "the condition can never be met" % (name, dependency, dependency)
                )

    # --- Check 8: services that are never restarted must not block on conditions ---
    for name in sorted(services):
        restart = services[name].get("restart")
        if restart is None or str(restart).strip().lower() not in NO_RESTART_VALUES:
            continue
        for dependency, condition in sorted(depends_conditions(services[name]).items()):
            if condition in BLOCKING_CONDITIONS:
                violations.append(
                    "%s: restart is %r, so a %s condition on %s would leave it "
                    "permanently down when the condition is not met"
                    % (name, services[name].get("restart"), condition, dependency)
                )

    print("services checked: %d (%s)" % (len(services), ", ".join(sorted(services))))
    print("healthcheck exclusions: %s" % ", ".join(sorted(EXCLUDED_FROM_HEALTHCHECK)))
    print("depends_on edges checked: %d" % sum(len(v) for v in actual_depends.values()))
    print(
        "memory limits checked: %s"
        % ", ".join(
            "%s=%s" % (name, memory_limit_bytes(services.get(name) or {}))
            for name in sorted(REQUIRED_MEMORY_LIMIT_BYTES)
        )
    )

    if violations:
        print("\nFAIL: %d violation(s)" % len(violations))
        for violation in violations:
            print("  - %s" % violation)
        return 1

    print(
        "\nOK: log rotation, healthcheck coverage and endpoints, memory limits and "
        "depends_on conditions all verified"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

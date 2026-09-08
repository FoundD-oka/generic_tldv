#!/usr/bin/env python3
"""Static regression tests for the calendar authenticated-join compose wiring.

The two variables that enable the feature must reach exactly one service each:
`BROWSER_FRAME_ANCESTORS` -> api-gateway, `KABOSU_MEET_AUTHENTICATED` ->
calendar-service. Both must stay safe when unset (empty / false), so a plain
`docker compose up` never turns authenticated joins on by itself.

Stdlib only, reads docker-compose.yml from disk: no Docker daemon, no network,
no PyYAML.

Run: python3 -m unittest discover -s deploy/compose/scripts/tests
"""

import io
import os
import re
import unittest

COMPOSE_PATH = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docker-compose.yml")
)

FRAME_ANCESTORS = "BROWSER_FRAME_ANCESTORS"
MEET_AUTHENTICATED = "KABOSU_MEET_AUTHENTICATED"

# ${VAR}, ${VAR:-default} and ${VAR-default}, as compose interpolates them.
# `:-` also falls back on an empty value, plain `-` only on an unset one.
INTERPOLATION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?-)([^}]*))?\}")


def parse_service_environment(path):
    """{service: {KEY: raw_value}} from the `environment:` list of each service.

    Indentation-based: `services:` at column 0, service names at 2, keys at 4,
    list entries at 6 — the shape this compose file already uses throughout.
    """
    services = {}
    in_services = False
    service = None
    in_environment = False
    with io.open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            stripped = line.strip()
            if indent == 0:
                in_services = stripped == "services:"
                service = None
                in_environment = False
                continue
            if not in_services:
                continue
            if indent == 2 and stripped.endswith(":"):
                service = stripped[:-1]
                services.setdefault(service, {})
                in_environment = False
                continue
            if service is None:
                continue
            if indent == 4:
                in_environment = stripped == "environment:"
                continue
            if in_environment and indent == 6 and stripped.startswith("- "):
                entry = stripped[2:]
                if "=" in entry:
                    key, value = entry.split("=", 1)
                    services[service][key.strip()] = value
    return services


def interpolate(value, env):
    """Resolve ${VAR} / ${VAR:-default} the way compose does for unset vars."""

    def replace(match):
        name, operator, default = match.group(1), match.group(2), match.group(3)
        value = env.get(name)
        if value is None:
            return default if default is not None else ""
        if value == "" and operator == ":-":
            return default
        return value

    return INTERPOLATION.sub(replace, value)


class ComposeCalendarAuthEnvTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.services = parse_service_environment(COMPOSE_PATH)

    def test_parser_sees_the_target_services(self):
        # Guards the indentation-based parser itself: if it silently stopped
        # finding services, every assertion below would pass vacuously.
        for service in ("api-gateway", "calendar-service"):
            self.assertIn(service, self.services)
            self.assertTrue(self.services[service], "%s has no environment" % service)

    def test_api_gateway_declares_frame_ancestors(self):
        # AT-001
        self.assertEqual(
            self.services["api-gateway"].get(FRAME_ANCESTORS),
            "${BROWSER_FRAME_ANCESTORS:-}",
        )

    def test_calendar_service_declares_meet_authenticated(self):
        # AT-002
        self.assertEqual(
            self.services["calendar-service"].get(MEET_AUTHENTICATED),
            "${KABOSU_MEET_AUTHENTICATED:-false}",
        )

    def test_unset_defaults_are_safe(self):
        # AT-003 / FP-002: a bare `up` must not enable authenticated joins.
        self.assertEqual(
            interpolate(self.services["api-gateway"][FRAME_ANCESTORS], {}), ""
        )
        self.assertEqual(
            interpolate(self.services["calendar-service"][MEET_AUTHENTICATED], {}),
            "false",
        )

    def test_explicit_values_reach_their_service(self):
        # AT-004, with non-secret test values.
        env = {
            FRAME_ANCESTORS: "https://dashboard.example.test",
            MEET_AUTHENTICATED: "true",
        }
        self.assertEqual(
            interpolate(self.services["api-gateway"][FRAME_ANCESTORS], env),
            "https://dashboard.example.test",
        )
        self.assertEqual(
            interpolate(self.services["calendar-service"][MEET_AUTHENTICATED], env),
            "true",
        )

    def test_variables_are_not_wired_into_other_services(self):
        # FP-001: wrong-service wiring is the failure this file exists to catch.
        for service, environment in self.services.items():
            if service != "api-gateway":
                self.assertNotIn(FRAME_ANCESTORS, environment, service)
            if service != "calendar-service":
                self.assertNotIn(MEET_AUTHENTICATED, environment, service)


if __name__ == "__main__":
    unittest.main()

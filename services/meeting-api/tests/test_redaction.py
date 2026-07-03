from __future__ import annotations

import json
from pathlib import Path

from meeting_api.redaction import redact_secrets


def test_redact_secrets_matches_shared_contract():
    path = Path(__file__).resolve().parents[3] / "packages" / "redaction-tests" / "secret-redaction-cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))

    for case in cases:
        assert redact_secrets(case["input"]) == case["expected"], case["name"]

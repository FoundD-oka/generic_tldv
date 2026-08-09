"""conftest.py -- pytest path setup for mcp unit tests.

Prerequisite: the meeting-api package must be installed into the venv
(sys.path hacks do not bring in its dependencies such as email-validator):

    pip install -e libs/admin-models/ -e services/meeting-api/
    pytest services/mcp/tests/ -v

Only the mcp service root is added to sys.path here, so that `from main import ...`
resolves. fastapi_mcp / mcp.types are stubbed by the test modules themselves.
"""
import sys
import os

SERVICE_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, SERVICE_ROOT)

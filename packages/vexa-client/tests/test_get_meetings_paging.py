"""VexaClient の会議一覧 API(slim + paged)契約テスト。

`GET /meetings` は既定で summary `data` の 50件ページを返すようになった。
SDK は List を返す従来の `get_meetings()` を保ちつつ、`has_more` を扱える
`get_meetings_page()` を提供する。
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vexa_client.vexa import VexaClient  # noqa: E402


def _response(payload):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = json.loads(json.dumps(payload))
    response.raise_for_status.return_value = None
    return response


def _client():
    return VexaClient(base_url="http://api.test", api_key="vxa-test-key")


def test_get_meetings_without_arguments_sends_no_query_and_returns_list():
    client = _client()
    payload = {"meetings": [{"id": 1, "platform": "google_meet"}], "has_more": False}

    with patch.object(client._session, "request", return_value=_response(payload)) as request:
        meetings = client.get_meetings()

    assert isinstance(meetings, list)
    assert meetings[0]["id"] == 1
    # backward compat: missing `data` is filled in
    assert meetings[0]["data"] == {}
    assert request.call_args.kwargs["params"] is None


def test_get_meetings_page_returns_has_more_and_only_given_params():
    client = _client()
    payload = {"meetings": [{"id": 2, "data": {"name": "会議"}}], "has_more": True}

    with patch.object(client._session, "request", return_value=_response(payload)) as request:
        page = client.get_meetings_page(limit=10, offset=20, status="completed")

    assert page["has_more"] is True
    assert page["meetings"][0]["data"] == {"name": "会議"}
    assert request.call_args.kwargs["params"] == {
        "limit": 10,
        "offset": 20,
        "status": "completed",
    }


def test_include_data_adds_include_query_parameter():
    client = _client()

    with patch.object(client._session, "request",
                      return_value=_response({"meetings": [], "has_more": False})) as request:
        page = client.get_meetings_page(include_data=True, platform="teams")

    assert page == {"meetings": [], "has_more": False}
    assert request.call_args.kwargs["params"] == {"platform": "teams", "include": "data"}


def test_get_meeting_by_id_walks_pages_with_full_data():
    client = _client()
    page1 = {"meetings": [{"platform": "google_meet", "native_meeting_id": "aaa-bbbb-ccc"}],
             "has_more": True}
    page2 = {"meetings": [{"platform": "teams", "native_meeting_id": "999",
                           "data": {"notes": "全文"}}],
             "has_more": False}

    with patch.object(client._session, "request",
                      side_effect=[_response(page1), _response(page2)]) as request:
        found = client.get_meeting_by_id("teams", "999")

    assert found is not None
    assert found["data"] == {"notes": "全文"}
    assert request.call_count == 2
    first_params = request.call_args_list[0].kwargs["params"]
    second_params = request.call_args_list[1].kwargs["params"]
    assert first_params == {"limit": 100, "offset": 0, "include": "data"}
    assert second_params == {"limit": 100, "offset": 100, "include": "data"}


def test_get_meeting_by_id_stops_when_has_more_is_false():
    client = _client()
    payload = {"meetings": [{"platform": "teams", "native_meeting_id": "111"}],
               "has_more": False}

    with patch.object(client._session, "request", return_value=_response(payload)) as request:
        found = client.get_meeting_by_id("teams", "999")

    assert found is None
    assert request.call_count == 1

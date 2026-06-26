"""Tests for the native GCS storage backend (issue #1).

A fake google-cloud-storage client is injected into GCSStorageClient so these
tests need neither the SDK nor GCP credentials. The fake mirrors the real
download_as_bytes inclusive-end semantics, which is the load-bearing contract
for the 206/Range playback path.
"""

import logging

import pytest

from meeting_api import storage as storage_mod
from meeting_api.storage import GCSStorageClient, create_storage_client


# --------------------------------------------------------------------------
# Fake google-cloud-storage client
# --------------------------------------------------------------------------

class _FakeBlob:
    def __init__(self, store, counters, bucket, name):
        self._store = store
        self._counters = counters
        self._bucket = bucket
        self.name = name

    @property
    def size(self):
        data = self._store.get((self._bucket, self.name))
        return len(data) if data is not None else None

    def upload_from_string(self, data, content_type=None):
        self._store[(self._bucket, self.name)] = bytes(data)

    def upload_from_filename(self, src, content_type=None):
        with open(src, "rb") as fh:
            self._store[(self._bucket, self.name)] = fh.read()

    def download_as_bytes(self, start=None, end=None):
        self._counters["download_as_bytes"] += 1
        data = self._store[(self._bucket, self.name)]
        if start is None and end is None:
            return data
        # Real google-cloud-storage treats `end` as INCLUSIVE.
        stop = (end + 1) if end is not None else None
        return data[start:stop]

    def download_to_filename(self, dest):
        with open(dest, "wb") as fh:
            fh.write(self._store[(self._bucket, self.name)])

    def delete(self):
        del self._store[(self._bucket, self.name)]

    def exists(self):
        return (self._bucket, self.name) in self._store

    def generate_signed_url(self, **kwargs):
        return f"https://signed.example/{self._bucket}/{self.name}?sig=1"


class _FakeBucket:
    def __init__(self, store, counters, name):
        self._store = store
        self._counters = counters
        self.name = name

    def blob(self, path):
        return _FakeBlob(self._store, self._counters, self.name, path)

    def get_blob(self, path):
        self._counters["get_blob"] += 1
        if (self.name, path) in self._store:
            return _FakeBlob(self._store, self._counters, self.name, path)
        return None


class FakeGCSClient:
    def __init__(self):
        self.store = {}
        self.counters = {"download_as_bytes": 0, "get_blob": 0}

    def bucket(self, name):
        return _FakeBucket(self.store, self.counters, name)

    def list_blobs(self, bucket, prefix=None, max_results=None):
        keys = sorted(
            name for (b, name) in self.store
            if b == bucket and (prefix is None or name.startswith(prefix))
        )
        if max_results is not None:
            keys = keys[:max_results]
        return [_FakeBlob(self.store, self.counters, bucket, k) for k in keys]


@pytest.fixture
def gcs():
    fake = FakeGCSClient()
    client = GCSStorageClient(bucket="test-bucket", client=fake)
    return client, fake


# --------------------------------------------------------------------------
# Range / size — the correctness traps
# --------------------------------------------------------------------------

def test_download_file_range_is_inclusive_single_byte(gcs):
    client, _ = gcs
    client.upload_file("k", b"0123456789", content_type="application/octet-stream")
    # Inclusive end: (0, 0) must return exactly ONE byte.
    assert client.download_file_range("k", 0, 0) == b"0"
    assert client.download_file_range("k", 2, 4) == b"234"
    assert client.download_file_range("k", 7, 9) == b"789"


def test_get_file_size_does_not_download_body(gcs):
    client, fake = gcs
    client.upload_file("k", b"0123456789")
    assert client.get_file_size("k") == 10
    # Must use metadata (get_blob), never fetch the body.
    assert fake.counters["download_as_bytes"] == 0
    assert fake.counters["get_blob"] >= 1


def test_get_file_size_missing_raises(gcs):
    client, _ = gcs
    with pytest.raises(FileNotFoundError):
        client.get_file_size("nope")


# --------------------------------------------------------------------------
# Existence / round-trip / listing
# --------------------------------------------------------------------------

def test_upload_download_roundtrip(gcs):
    client, _ = gcs
    client.upload_file("a/b/c", b"hello")
    assert client.download_file("a/b/c") == b"hello"


def test_file_exists(gcs):
    client, _ = gcs
    assert client.file_exists("missing") is False
    client.upload_file("present", b"x")
    assert client.file_exists("present") is True


def test_upload_and_download_file_path(gcs, tmp_path):
    client, _ = gcs
    src = tmp_path / "src.bin"
    src.write_bytes(b"streamed-bytes")
    client.upload_file_path("obj", str(src))
    dest = tmp_path / "dest.bin"
    client.download_file_to_path("obj", str(dest))
    assert dest.read_bytes() == b"streamed-bytes"


def test_list_objects_sorted(gcs):
    client, _ = gcs
    for k in ["p/000002.bin", "p/000000.bin", "p/000001.bin", "other/x"]:
        client.upload_file(k, b"x")
    assert client.list_objects("p/") == ["p/000000.bin", "p/000001.bin", "p/000002.bin"]


def test_list_objects_bounded_truncates_and_warns(gcs, caplog):
    client, _ = gcs
    for i in range(10):
        client.upload_file(f"p/{i:06d}.bin", b"x")
    with caplog.at_level(logging.WARNING):
        keys = client.list_objects_bounded("p/", max_keys=4)
    assert len(keys) == 4
    assert keys == sorted(keys)
    assert any("truncated" in r.message for r in caplog.records)


def test_delete_removes_object(gcs):
    client, _ = gcs
    client.upload_file("k", b"x")
    assert client.file_exists("k") is True
    client.delete_file("k")
    assert client.file_exists("k") is False


# --------------------------------------------------------------------------
# Signed URLs — fall back, never fail silently
# --------------------------------------------------------------------------

def test_presigned_url_success(gcs, monkeypatch):
    client, _ = gcs
    client.upload_file("k", b"x")

    class _Creds:
        service_account_email = "sa@example.iam.gserviceaccount.com"
        token = "tok"

    monkeypatch.setattr(client, "_get_signing_credentials", lambda: _Creds())
    url = client.get_presigned_url("k", expires=600)
    assert url is not None
    assert url.startswith("https://signed.example/")


def test_presigned_url_returns_none_when_signing_unavailable(gcs, monkeypatch, caplog):
    client, _ = gcs
    client.upload_file("k", b"x")

    def _boom():
        raise RuntimeError("no signBlob permission")

    monkeypatch.setattr(client, "_get_signing_credentials", _boom)
    with caplog.at_level(logging.WARNING):
        url = client.get_presigned_url("k")
    # None signals the caller to fall back to /raw — and it is logged, not silent.
    assert url is None
    assert any("signed-URL generation unavailable" in r.message for r in caplog.records)


# --------------------------------------------------------------------------
# Factory dispatch
# --------------------------------------------------------------------------

def test_factory_dispatches_gcs(monkeypatch):
    created = {}

    class _Stub:
        def __init__(self, *a, **k):
            created["yes"] = True

    monkeypatch.setattr(storage_mod, "GCSStorageClient", _Stub)
    client = create_storage_client("gcs")
    assert isinstance(client, _Stub)
    assert created.get("yes") is True

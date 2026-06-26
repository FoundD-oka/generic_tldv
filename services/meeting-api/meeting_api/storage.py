"""
Storage client abstraction for Vexa recording media files.

Supports MinIO (S3-compatible), native Google Cloud Storage, and local
filesystem backends. MinIO is the default for development (Docker Compose)
and production. GCS (issue #1) is the source-of-truth backend for recordings
with a 14d-Standard / Nearline / 60d-delete lifecycle. Local filesystem is
available for testing without object storage.
"""

import os
import logging
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


class StorageClient(ABC):
    """Abstract interface for object storage operations."""

    @abstractmethod
    def upload_file(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Upload data to storage. Returns the storage path."""
        ...

    @abstractmethod
    def download_file(self, path: str) -> bytes:
        """Download file from storage. Returns file content as bytes."""
        ...

    def get_file_size(self, path: str) -> int:
        """Return the object size in bytes."""
        return len(self.download_file(path))

    def download_file_range(self, path: str, start: int, end: int) -> bytes:
        """Download an inclusive byte range from storage."""
        return self.download_file(path)[start:end + 1]

    def upload_file_path(self, key: str, src_file_path: str, content_type: str = "application/octet-stream") -> str:
        """Stream-upload from a local file path. Default implementation
        falls back to upload_file(read-all-bytes); MinIO/S3 backends
        override with multipart streaming for bounded memory."""
        with open(src_file_path, "rb") as fh:
            data = fh.read()
        return self.upload_file(key, data, content_type)

    def download_file_to_path(self, key: str, dest_file_path: str) -> str:
        """Stream-download to a local file path. Default implementation
        falls back to download_file(read-all); MinIO/S3 backends
        override with multipart streaming for bounded memory."""
        data = self.download_file(key)
        with open(dest_file_path, "wb") as fh:
            fh.write(data)
        return dest_file_path

    @abstractmethod
    def get_presigned_url(self, path: str, expires: int = 3600) -> str:
        """Generate a presigned download URL. expires is in seconds."""
        ...

    @abstractmethod
    def delete_file(self, path: str) -> None:
        """Delete a file from storage."""
        ...

    @abstractmethod
    def file_exists(self, path: str) -> bool:
        """Check if a file exists in storage."""
        ...

    @abstractmethod
    def list_objects(self, prefix: str) -> list:
        """List storage paths under prefix. Returns a sorted list of full paths.

        Used by recording_finalizer to enumerate per-session chunks under
        `recordings/<user>/<rec>/<session>/<media_type>/`. Sorted ascending
        so byte-concat order matches the chunk_seq order.
        """

    @abstractmethod
    def list_objects_bounded(self, prefix: str, max_keys: int = 10000) -> list:
        """Bounded variant of list_objects. Stops enumerating at max_keys and
        logs a warning when truncation occurs. Callers that scan user-wide
        prefixes (sweeps, finalizers) should prefer this to avoid unbounded
        memory growth from a user with very many chunks."""
        ...


class MinIOStorageClient(StorageClient):
    """MinIO/S3-compatible storage client using boto3."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        bucket: Optional[str] = None,
        secure: Optional[bool] = None,
    ):
        try:
            import boto3
            from botocore.config import Config as BotoConfig
        except ImportError:
            raise ImportError("boto3 is required for MinIO storage. Install it: pip install boto3")

        self.endpoint = os.environ.get("MINIO_ENDPOINT", "minio:9000") if endpoint is None else endpoint
        self.access_key = os.environ.get("MINIO_ACCESS_KEY", "vexa-access-key") if access_key is None else access_key
        self.secret_key = os.environ.get("MINIO_SECRET_KEY", "vexa-secret-key") if secret_key is None else secret_key
        self.bucket = bucket or os.environ.get("MINIO_BUCKET", "vexa-recordings")
        if secure is None:
            self.secure = os.environ.get("MINIO_SECURE", "false").lower() == "true"
        else:
            self.secure = secure
        self.region = os.environ.get("AWS_REGION", "us-east-1")

        protocol = "https" if self.secure else "http"
        if self.endpoint:
            endpoint_url = self.endpoint if "://" in self.endpoint else f"{protocol}://{self.endpoint}"
        else:
            endpoint_url = None

        # v0.10.5.3 Pack D-3 follow-up (Option B):
        # MINIO_ENDPOINT is the cluster-internal hostname used for server-side
        # I/O (put_object/get_object) — fast, no NAT traversal.
        # MINIO_PUBLIC_ENDPOINT is what the BROWSER must use to fetch a
        # presigned URL — typically a NodePort, ingress, or host-mapped port.
        # When unset, falls back to MINIO_ENDPOINT; dashboard deployments that
        # expose only the dashboard should use the same-origin master proxy
        # instead of handing this internal hostname to the browser.
        # When set on helm with cluster-internal-only MinIO Service, points at
        # the externally reachable surface.
        # Pre-fix: presigned URLs always carried the internal hostname; on
        # helm with ClusterIP-only MinIO, browsers got DNS-unresolvable URLs
        # and audio playback hung at "Preparing audio...".
        public_endpoint_raw = (os.environ.get("MINIO_PUBLIC_ENDPOINT") or "").strip() or self.endpoint
        if public_endpoint_raw:
            public_endpoint_url = public_endpoint_raw if "://" in public_endpoint_raw else f"{protocol}://{public_endpoint_raw}"
        else:
            public_endpoint_url = endpoint_url
        self.public_endpoint_url = public_endpoint_url

        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=self.access_key or None,
            aws_secret_access_key=self.secret_key or None,
            region_name=self.region,
            config=BotoConfig(signature_version="s3v4"),
        )
        # Separate signing client only differs in endpoint_url. Reuses the
        # same credentials/region/bucket. Used solely by get_presigned_url.
        # If public_endpoint_url == endpoint_url, presigned URLs work as
        # before (no behavior change for compose/lite when MINIO_PUBLIC_ENDPOINT
        # is unset).
        if public_endpoint_url != endpoint_url:
            self._signing_client = boto3.client(
                "s3",
                endpoint_url=public_endpoint_url,
                aws_access_key_id=self.access_key or None,
                aws_secret_access_key=self.secret_key or None,
                region_name=self.region,
                config=BotoConfig(signature_version="s3v4"),
            )
        else:
            self._signing_client = self.client
        logger.info(
            f"MinIO storage client initialized: endpoint={endpoint_url}, "
            f"public_endpoint={public_endpoint_url}, bucket={self.bucket}"
        )

    def upload_file(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self.client.put_object(
            Bucket=self.bucket,
            Key=path,
            Body=data,
            ContentType=content_type,
        )
        logger.info(f"Uploaded {len(data)} bytes to {self.bucket}/{path}")
        return path

    def upload_file_path(self, key: str, src_file_path: str, content_type: str = "application/octet-stream") -> str:
        """Stream-upload from a local file path. boto3's upload_file uses
        multipart with bounded memory regardless of file size.

        Use this for objects that are already on local disk to avoid
        the bytes-in-memory round-trip required by upload_file().
        """
        self.client.upload_file(
            Filename=src_file_path,
            Bucket=self.bucket,
            Key=key,
            ExtraArgs={"ContentType": content_type},
        )
        size = os.path.getsize(src_file_path)
        logger.info(f"Uploaded {size} bytes to {self.bucket}/{key} (streamed from {src_file_path})")
        return key

    def download_file(self, path: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=path)
        data = response["Body"].read()
        logger.info(f"Downloaded {len(data)} bytes from {self.bucket}/{path}")
        return data

    def get_file_size(self, path: str) -> int:
        response = self.client.head_object(Bucket=self.bucket, Key=path)
        return int(response.get("ContentLength") or 0)

    def download_file_range(self, path: str, start: int, end: int) -> bytes:
        byte_range = f"bytes={start}-{end}"
        response = self.client.get_object(Bucket=self.bucket, Key=path, Range=byte_range)
        data = response["Body"].read()
        logger.info(
            f"Downloaded range {byte_range} ({len(data)} bytes) from {self.bucket}/{path}"
        )
        return data

    def download_file_to_path(self, key: str, dest_file_path: str) -> str:
        """Stream-download to a local file path. Bounded memory."""
        self.client.download_file(
            Bucket=self.bucket,
            Key=key,
            Filename=dest_file_path,
        )
        size = os.path.getsize(dest_file_path)
        logger.info(f"Downloaded {size} bytes from {self.bucket}/{key} (streamed to {dest_file_path})")
        return dest_file_path

    def get_presigned_url(self, path: str, expires: int = 3600) -> str:
        # v0.10.5.3 Pack D-3 follow-up (Option B): sign against the PUBLIC
        # endpoint (browser-reachable) rather than the internal endpoint
        # used for server-side I/O. See __init__ for the rationale.
        url = self._signing_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": path},
            ExpiresIn=expires,
        )
        return url

    def delete_file(self, path: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=path)
        logger.info(f"Deleted {self.bucket}/{path}")

    def file_exists(self, path: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=path)
            return True
        except self.client.exceptions.ClientError:
            return False

    def list_objects(self, prefix: str) -> list:
        # Paginate so we don't truncate at 1000 (S3 default page size).
        # Sorted ascending so callers can rely on chunk_seq lexicographic
        # ordering (zero-padded 6-digit seq numbers sort correctly).
        keys = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        keys.sort()
        return keys

    def list_objects_bounded(self, prefix: str, max_keys: int = 10000) -> list:
        keys = []
        paginator = self.client.get_paginator("list_objects_v2")
        truncated = False
        for page in paginator.paginate(
            Bucket=self.bucket,
            Prefix=prefix,
            PaginationConfig={"PageSize": min(1000, max_keys)},
        ):
            for obj in page.get("Contents", []):
                if len(keys) >= max_keys:
                    truncated = True
                    break
                keys.append(obj["Key"])
            if truncated:
                break
        if truncated:
            logger.warning(
                "storage.list_objects_bounded truncated at max_keys=%d prefix=%s",
                max_keys, prefix,
            )
        keys.sort()
        return keys


class GCSStorageClient(StorageClient):
    """Native Google Cloud Storage client (google-cloud-storage SDK).

    Issue #1: GCS is the source-of-truth backend for recordings. The bucket
    carries a lifecycle policy applied out-of-band (see deploy/gcs/):
    14d Standard -> Nearline, delete at 60d.

    Auth is ADC / Workload Identity — no HMAC key lives in the app. Signed URLs
    are minted on demand via V4 signing backed by the IAM signBlob API, so no
    service-account key file is required. When signing is unavailable (e.g. the
    runtime SA lacks serviceAccountTokenCreator on itself) get_presigned_url
    returns None and the caller falls back to the /raw proxy — the same posture
    as the local backend, and it is logged rather than failing silently.

    A pre-built client may be injected (tests); otherwise one is created from
    ADC. The SDK import is lazy so non-GCS deployments don't need the package.
    """

    def __init__(
        self,
        bucket: Optional[str] = None,
        project: Optional[str] = None,
        client=None,
    ):
        bucket_name = (bucket or os.environ.get("GCS_BUCKET") or "").strip()
        if not bucket_name:
            raise ValueError("GCS_BUCKET is required when STORAGE_BACKEND=gcs")
        self.bucket_name = bucket_name
        self.project = project or os.environ.get("GCS_PROJECT") or None
        # Optional explicit signing SA email; otherwise we derive it from the
        # ADC credentials' service_account_email when signing.
        self.signing_sa = (os.environ.get("GCS_SIGNING_SERVICE_ACCOUNT") or "").strip() or None
        self._signing_credentials = None  # lazy, cached after first refresh

        if client is None:
            try:
                from google.cloud import storage as gcs_storage
            except ImportError:
                raise ImportError(
                    "google-cloud-storage is required for the gcs backend. "
                    "Install it: pip install google-cloud-storage"
                )
            client = gcs_storage.Client(project=self.project) if self.project else gcs_storage.Client()
        self.client = client
        self.bucket = self.client.bucket(self.bucket_name)
        logger.info(
            f"GCS storage client initialized: bucket={self.bucket_name}, project={self.project}"
        )

    def _blob(self, path: str):
        return self.bucket.blob(path)

    def upload_file(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        blob = self._blob(path)
        blob.upload_from_string(data, content_type=content_type)
        logger.info(f"Uploaded {len(data)} bytes to gs://{self.bucket_name}/{path}")
        return path

    def upload_file_path(self, key: str, src_file_path: str, content_type: str = "application/octet-stream") -> str:
        """Stream-upload from a local file path. The SDK chunks the upload so
        memory stays bounded regardless of file size."""
        blob = self._blob(key)
        blob.upload_from_filename(src_file_path, content_type=content_type)
        size = os.path.getsize(src_file_path)
        logger.info(f"Uploaded {size} bytes to gs://{self.bucket_name}/{key} (streamed from {src_file_path})")
        return key

    def download_file(self, path: str) -> bytes:
        data = self._blob(path).download_as_bytes()
        logger.info(f"Downloaded {len(data)} bytes from gs://{self.bucket_name}/{path}")
        return data

    def get_file_size(self, path: str) -> int:
        # get_blob() fetches object metadata only (no body) — never download the
        # whole object just to learn its size (the 206 path calls this per
        # request before download_file_range).
        blob = self.bucket.get_blob(path)
        if blob is None:
            raise FileNotFoundError(path)
        return int(blob.size or 0)

    def download_file_range(self, path: str, start: int, end: int) -> bytes:
        # INCLUSIVE end — matches the StorageClient contract and the 206 path,
        # which builds `Content-Range: bytes {start}-{end}/{total}` (inclusive)
        # from _parse_range_header. google-cloud-storage download_as_bytes
        # treats the `end` argument as inclusive, mirroring MinIO's
        # `Range: bytes=start-end`. An off-by-one here corrupts every ranged
        # playback, so this is asserted byte-exactly in test_gcs_storage.py.
        data = self._blob(path).download_as_bytes(start=start, end=end)
        logger.info(
            f"Downloaded range bytes={start}-{end} ({len(data)} bytes) from gs://{self.bucket_name}/{path}"
        )
        return data

    def download_file_to_path(self, key: str, dest_file_path: str) -> str:
        """Stream-download to a local file path. Bounded memory."""
        self._blob(key).download_to_filename(dest_file_path)
        size = os.path.getsize(dest_file_path)
        logger.info(f"Downloaded {size} bytes from gs://{self.bucket_name}/{key} (streamed to {dest_file_path})")
        return dest_file_path

    def _get_signing_credentials(self):
        if self._signing_credentials is not None:
            return self._signing_credentials
        import google.auth
        from google.auth.transport.requests import Request as GoogleAuthRequest
        creds, _ = google.auth.default()
        creds.refresh(GoogleAuthRequest())
        self._signing_credentials = creds
        return creds

    def get_presigned_url(self, path: str, expires: int = 3600):
        # V4 signed URL via IAM signBlob — works under Workload Identity with no
        # key file. Returns None (logged) on failure so download_media_file
        # falls back to the /raw proxy instead of 500ing.
        from datetime import timedelta
        try:
            creds = self._get_signing_credentials()
            sa_email = self.signing_sa or getattr(creds, "service_account_email", None)
            token = getattr(creds, "token", None)
            return self._blob(path).generate_signed_url(
                version="v4",
                expiration=timedelta(seconds=expires),
                method="GET",
                service_account_email=sa_email,
                access_token=token,
            )
        except Exception as e:
            logger.warning(
                "GCS signed-URL generation unavailable for %s (%s); "
                "caller should fall back to /raw proxy",
                path, e,
            )
            return None

    def delete_file(self, path: str) -> None:
        try:
            self._blob(path).delete()
            logger.info(f"Deleted gs://{self.bucket_name}/{path}")
        except Exception as e:
            try:
                from google.api_core import exceptions as gcs_exc
                not_found = isinstance(e, gcs_exc.NotFound)
            except Exception:
                not_found = False
            if not_found:
                logger.info(f"Delete no-op (already absent) gs://{self.bucket_name}/{path}")
                return
            raise

    def file_exists(self, path: str) -> bool:
        # blob.exists() returns False on NotFound and raises on other errors
        # (transient 5xx, permission) — we let those propagate rather than
        # masking them as a silent 404, mirroring MinIO's head_object posture.
        return self._blob(path).exists()

    def list_objects(self, prefix: str) -> list:
        keys = [b.name for b in self.client.list_blobs(self.bucket_name, prefix=prefix)]
        keys.sort()
        return keys

    def list_objects_bounded(self, prefix: str, max_keys: int = 10000) -> list:
        keys = []
        truncated = False
        # Fetch one extra to detect truncation without scanning the whole prefix.
        for b in self.client.list_blobs(self.bucket_name, prefix=prefix, max_results=max_keys + 1):
            if len(keys) >= max_keys:
                truncated = True
                break
            keys.append(b.name)
        if truncated:
            logger.warning(
                "storage.list_objects_bounded truncated at max_keys=%d prefix=%s",
                max_keys, prefix,
            )
        keys.sort()
        return keys


class LocalStorageClient(StorageClient):
    """Filesystem-based storage client for development/testing."""

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or os.environ.get("LOCAL_STORAGE_DIR", "/tmp/vexa-recordings")
        self.fsync_enabled = os.environ.get("LOCAL_STORAGE_FSYNC", "true").lower() == "true"
        os.makedirs(self.base_dir, exist_ok=True)
        logger.info(f"Local storage client initialized: base_dir={self.base_dir}, fsync={self.fsync_enabled}")

    def _normalize_path(self, path: str) -> str:
        # Normalize storage key and reject path traversal.
        normalized = os.path.normpath(path.replace("\\", "/")).lstrip("/")
        if normalized in ("", ".", "..") or normalized.startswith("../"):
            raise ValueError(f"Invalid storage path: {path}")
        return normalized

    def _full_path(self, path: str, create_dirs: bool = False) -> str:
        normalized = self._normalize_path(path)
        full = os.path.join(self.base_dir, normalized)
        if create_dirs:
            os.makedirs(os.path.dirname(full), exist_ok=True)
        return full

    def upload_file(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        full_path = self._full_path(path, create_dirs=True)
        with open(full_path, "wb") as f:
            f.write(data)
            f.flush()
            if self.fsync_enabled:
                os.fsync(f.fileno())
        logger.info(f"Stored {len(data)} bytes to {full_path}")
        return self._normalize_path(path)

    def download_file(self, path: str) -> bytes:
        full_path = self._full_path(path)
        with open(full_path, "rb") as f:
            return f.read()

    def get_file_size(self, path: str) -> int:
        return os.path.getsize(self._full_path(path))

    def download_file_range(self, path: str, start: int, end: int) -> bytes:
        full_path = self._full_path(path)
        with open(full_path, "rb") as f:
            f.seek(start)
            return f.read(end - start + 1)

    def get_presigned_url(self, path: str, expires: int = 3600) -> str:
        # Local storage doesn't support presigned URLs — return a file:// URI
        return f"file://{self._full_path(path)}"

    def delete_file(self, path: str) -> None:
        full_path = self._full_path(path)
        if os.path.exists(full_path):
            os.remove(full_path)
            logger.info(f"Deleted {full_path}")

    def file_exists(self, path: str) -> bool:
        return os.path.exists(self._full_path(path))

    def list_objects(self, prefix: str) -> list:
        # Walk the local filesystem under the prefix directory; return
        # storage-relative paths (forward-slash) so callers can treat them
        # interchangeably with MinIO keys.
        normalized_prefix = self._normalize_path(prefix) if prefix else ""
        full_prefix = os.path.join(self.base_dir, normalized_prefix) if normalized_prefix else self.base_dir
        keys = []
        if not os.path.isdir(full_prefix):
            return keys
        for root, _dirs, files in os.walk(full_prefix):
            for fname in files:
                full_path = os.path.join(root, fname)
                rel = os.path.relpath(full_path, self.base_dir).replace("\\", "/")
                keys.append(rel)
        keys.sort()
        return keys

    def list_objects_bounded(self, prefix: str, max_keys: int = 10000) -> list:
        normalized_prefix = self._normalize_path(prefix) if prefix else ""
        full_prefix = os.path.join(self.base_dir, normalized_prefix) if normalized_prefix else self.base_dir
        keys = []
        if not os.path.isdir(full_prefix):
            return keys
        truncated = False
        for root, _dirs, files in os.walk(full_prefix):
            for fname in files:
                if len(keys) >= max_keys:
                    truncated = True
                    break
                full_path = os.path.join(root, fname)
                rel = os.path.relpath(full_path, self.base_dir).replace("\\", "/")
                keys.append(rel)
            if truncated:
                break
        if truncated:
            logger.warning(
                "storage.list_objects_bounded truncated at max_keys=%d prefix=%s",
                max_keys, prefix,
            )
        keys.sort()
        return keys


def create_storage_client(backend: Optional[str] = None) -> StorageClient:
    """Factory function to create the appropriate storage client based on configuration."""
    backend = backend or os.environ.get("STORAGE_BACKEND", "minio")

    if backend == "minio":
        return MinIOStorageClient()
    elif backend == "s3":
        return MinIOStorageClient(
            endpoint=os.environ.get("S3_ENDPOINT", ""),
            access_key=os.environ.get("AWS_ACCESS_KEY_ID", ""),
            secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
            bucket=os.environ.get("S3_BUCKET", os.environ.get("MINIO_BUCKET", "vexa-recordings")),
            secure=os.environ.get("S3_SECURE", "true").lower() == "true",
        )
    elif backend == "gcs":
        return GCSStorageClient()
    elif backend == "local":
        return LocalStorageClient()
    else:
        raise ValueError(f"Unknown storage backend: {backend}. Supported: minio, s3, gcs, local")

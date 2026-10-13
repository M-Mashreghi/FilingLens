"""Content-addressed, write-once raw snapshot storage on the local filesystem."""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from pathlib import Path

from filinglens.domain.errors import ImmutabilityError, NotFoundError


class RawIntegrityError(ImmutabilityError):
    """Stored bytes do not match their content address."""


class FileSystemRawStore:
    """Stores bytes at ``sha256/<aa>/<digest>``. Existing objects are never overwritten."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @staticmethod
    def key_for(digest: str) -> str:
        return f"sha256/{digest[:2]}/{digest}"

    def _path(self, key: str) -> Path:
        parts = key.split("/")
        if len(parts) != 3 or parts[0] != "sha256" or len(parts[2]) != 64:
            raise ValueError(f"invalid storage key {key!r}")
        return self.root.joinpath(*parts)

    def put(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        key = self.key_for(digest)
        path = self._path(key)
        with self._lock:
            if path.exists():
                if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise RawIntegrityError(
                        f"existing object {key} is corrupt; refusing to replace"
                    )
                return key
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
            path.chmod(0o444)
        return key

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise NotFoundError(key)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != key.rsplit("/", 1)[-1]:
            raise RawIntegrityError(f"object {key} failed hash verification")
        return data

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        """Remove an object. Callers must enforce the retention policy (pinned snapshots)."""
        path = self._path(key)
        with self._lock:
            if path.exists():
                path.chmod(0o644)
                path.unlink()

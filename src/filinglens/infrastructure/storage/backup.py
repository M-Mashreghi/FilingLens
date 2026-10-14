"""Consistent backup / verified restore of SQLite metadata and raw snapshots (``backup/1``).

- SQLite is copied with the online backup API (consistent even while the API is running).
- Raw snapshot objects are copied and re-hashed; the manifest records every object hash, the
  database file hash, schema version and completed run ids.
- Restore verifies every hash before writing and refuses to overwrite a non-empty target.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from filinglens.domain.errors import FilingLensError

BACKUP_VERSION = "backup/1"


class BackupError(FilingLensError):
    pass


@dataclass(frozen=True)
class BackupManifest:
    version: str
    created_at: str
    schema_version: int
    db_sha256: str
    raw_objects: dict[str, str]  # storage key -> sha256
    completed_runs: list[str]

    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=2, sort_keys=True)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def create_backup(
    db_path: Path, raw_dir: Path, out_dir: Path, *, created_at: datetime
) -> BackupManifest:
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise BackupError(f"backup target {out} is not empty")
    (out / "raw").mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(out / "filinglens.db"))
    try:
        src.backup(dst)
        schema = int(dst.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] or 0)
        completed = [
            r[0]
            for r in dst.execute(
                "SELECT run_id FROM runs WHERE state = 'Completed' ORDER BY run_id"
            )
        ]
    finally:
        dst.close()
        src.close()
    objects: dict[str, str] = {}
    raw_root = Path(raw_dir)
    for obj in sorted(raw_root.glob("sha256/*/*")):
        if obj.name.startswith(".tmp-"):
            continue
        digest = _sha(obj)
        if digest != obj.name:
            raise BackupError(f"raw object {obj.name} failed hash verification during backup")
        key = obj.relative_to(raw_root).as_posix()
        target = out / "raw" / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(obj, target)
        objects[key] = digest
    manifest = BackupManifest(
        BACKUP_VERSION,
        created_at.isoformat(),
        schema,
        _sha(out / "filinglens.db"),
        objects,
        completed,
    )
    (out / "manifest.json").write_text(manifest.to_json(), encoding="utf-8")
    return manifest


def verify_backup(backup_dir: Path) -> BackupManifest:
    b = Path(backup_dir)
    data = json.loads((b / "manifest.json").read_text(encoding="utf-8"))
    manifest = BackupManifest(**data)
    if _sha(b / "filinglens.db") != manifest.db_sha256:
        raise BackupError("database copy hash mismatch")
    for key, digest in manifest.raw_objects.items():
        p = b / "raw" / key
        if not p.exists() or _sha(p) != digest:
            raise BackupError(f"raw object {key} missing or corrupt in backup")
    return manifest


def restore_backup(backup_dir: Path, db_path: Path, raw_dir: Path) -> BackupManifest:
    manifest = verify_backup(backup_dir)
    db_path, raw_dir = Path(db_path), Path(raw_dir)
    if db_path.exists() or (raw_dir.exists() and any(raw_dir.rglob("*"))):
        raise BackupError("restore target is not empty; refusing to overwrite")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(backup_dir) / "filinglens.db", db_path)
    for key in manifest.raw_objects:
        target = raw_dir / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(backup_dir) / "raw" / key, target)
        target.chmod(0o444)
    return manifest


def prune_backups(root: Path, keep: int) -> list[Path]:
    """Keep the newest ``keep`` backup directories (by manifest timestamp)."""
    dirs = [d for d in Path(root).iterdir() if (d / "manifest.json").exists()]
    dirs.sort(
        key=lambda d: json.loads((d / "manifest.json").read_text(encoding="utf-8"))["created_at"]
    )
    removed = dirs[: max(0, len(dirs) - keep)]
    for d in removed:
        shutil.rmtree(d)
    return removed

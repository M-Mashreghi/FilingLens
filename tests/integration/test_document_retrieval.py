"""Task 2.4: primary filing and analyst-relevant exhibit retrieval."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from filinglens.application.retrieval import (
    DocumentRetriever,
    ExhibitPolicy,
    RetrievalStatus,
)
from filinglens.domain.errors import NotFoundError
from filinglens.domain.models import FilingDocument
from filinglens.domain.package import PackageManifest
from filinglens.infrastructure.sec.client import RateGovernor, SecHttpClient
from filinglens.infrastructure.storage.raw_store import FileSystemRawStore, RawIntegrityError
from filinglens.infrastructure.storage.sqlite import Database, SqliteRepositories

ACC = "0001234567-24-000040"
BASE = "https://www.sec.gov/Archives/edgar/data/1234567/000123456724000040/"
BODIES = {
    "form8k.htm": (b"<html><body>Item 2.02 Results</body></html>", "text/html"),
    "ex991.htm": (b"<html><body>Press release: revenue up</body></html>", "text/html"),
    "ex101.htm": (b"<html><body>Credit agreement</body></html>", "text/html"),
    "ex32.htm": (b"<html>certification</html>", "text/html"),
    f"{ACC}.txt": (b"<SEC-DOCUMENT>full text</SEC-DOCUMENT>", "text/plain"),
    "logo.jpg": (b"\xff\xd8binary", "image/jpeg"),
}


def _doc(
    name: str, role: str, dtype: str | None, seq: int | None, size: int | None = None
) -> FilingDocument:
    return FilingDocument(
        accession=ACC,
        filename=name,
        sequence=seq,
        doc_type=dtype,
        role=role,
        url=BASE + name,
        size_bytes=size,
    )


def _manifest(**over: object) -> PackageManifest:
    docs = (
        _doc("form8k.htm", "primary", "8-K", 1),
        _doc("ex991.htm", "exhibit", "EX-99.1", 2),
        _doc("ex101.htm", "exhibit", "EX-10.1", 3),
        _doc("ex32.htm", "exhibit", "EX-32.1", 4),
        _doc("logo.jpg", "supporting", "GRAPHIC", 5),
        _doc(f"{ACC}.txt", "complete_submission", None, None),
    )
    m = PackageManifest(accession=ACC, cik=1234567, form="8-K", documents=docs, source_versions=())
    return replace(m, **over)  # type: ignore[arg-type]


def _env(tmp_path: Path, fail: set[str] | None = None, policy: ExhibitPolicy | None = None):  # type: ignore[no-untyped-def]
    fail = fail or set()
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        name = str(req.url).rsplit("/", 1)[-1]
        calls.append(name)
        if name in fail:
            return httpx.Response(404)
        body, ctype = BODIES[name]
        return httpx.Response(200, content=body, headers={"Content-Type": ctype})

    client = SecHttpClient(
        user_agent="t t@example.com",
        live=False,
        transport=httpx.MockTransport(handler),
        governor=RateGovernor(1000),
    )
    repos = SqliteRepositories(Database(":memory:"))
    store = FileSystemRawStore(tmp_path / "raw")
    return DocumentRetriever(client, store, repos.snapshots, policy), repos, store, calls


def test_primary_and_policy_exhibits_retrieved(tmp_path: Path) -> None:
    retriever, repos, store, calls = _env(tmp_path)
    result = retriever.retrieve_package(_manifest())
    got = {o.filename: o.status for o in result.outcomes}
    assert got == {
        "form8k.htm": RetrievalStatus.RETRIEVED,
        "ex991.htm": RetrievalStatus.RETRIEVED,
        "ex101.htm": RetrievalStatus.RETRIEVED,
    }
    assert "ex32.htm" not in calls and "logo.jpg" not in calls  # not analyst-relevant by policy
    assert result.primary_snapshot is not None
    assert result.primary_snapshot.filename == "form8k.htm"
    assert not result.used_complete_submission_fallback
    for snap in result.snapshots():
        assert repos.snapshots.get(snap.snapshot_id) == snap
        assert snap.verify(store.get(snap.storage_key))
        assert snap.accession == ACC and snap.content_type == "text/html"


def test_exhibit_selection_is_policy_driven(tmp_path: Path) -> None:
    policy = ExhibitPolicy(include_prefixes={"8-K": ("EX-32",)})
    retriever, *_ = _env(tmp_path, policy=policy)
    result = retriever.retrieve_package(_manifest())
    assert {o.filename for o in result.outcomes} == {"form8k.htm", "ex32.htm"}
    none = ExhibitPolicy(include_prefixes={})
    retriever2, *_ = _env(tmp_path, policy=none)
    assert {o.filename for o in retriever2.retrieve_package(_manifest()).outcomes} == {"form8k.htm"}


def test_prefix_matching_is_exact_on_segments() -> None:
    policy = ExhibitPolicy(include_prefixes={"8-K": ("EX-9",)})
    assert not policy.selects("8-K", _doc("x", "exhibit", "EX-99.1", 2))
    assert policy.selects("8-K", _doc("x", "exhibit", "EX-9", 2))
    assert not policy.selects("8-K", _doc("x", "primary", "EX-9", 1))


def test_exhibit_never_selected_as_primary(tmp_path: Path) -> None:
    docs = tuple(d for d in _manifest().documents if d.role != "primary")
    retriever, *_ = _env(tmp_path)
    result = retriever.retrieve_package(_manifest(documents=docs))
    assert result.primary_snapshot is None
    first = result.outcomes[0]
    assert first.role == "primary" and first.status is RetrievalStatus.FAILED
    assert result.used_complete_submission_fallback
    full = next(o for o in result.outcomes if o.role == "complete_submission")
    assert full.snapshot is not None and full.snapshot.content_type == "text/plain"


def test_fallback_can_be_disabled(tmp_path: Path) -> None:
    retriever, *_ = _env(
        tmp_path,
        fail={"form8k.htm"},
        policy=ExhibitPolicy(include_complete_submission_fallback=False),
    )
    result = retriever.retrieve_package(_manifest())
    assert result.primary_snapshot is None and not result.used_complete_submission_fallback
    assert all(o.role != "complete_submission" for o in result.outcomes)


def test_failed_retrieval_creates_no_snapshot(tmp_path: Path) -> None:
    retriever, repos, store, _ = _env(tmp_path, fail={"ex991.htm"})
    result = retriever.retrieve_package(_manifest())
    failed = next(o for o in result.outcomes if o.filename == "ex991.htm")
    assert failed.status is RetrievalStatus.FAILED and failed.snapshot is None
    digest = hashlib.sha256(BODIES["ex991.htm"][0]).hexdigest()
    assert repos.snapshots.get(digest) is None
    assert not store.exists(FileSystemRawStore.key_for(digest))


def test_size_and_content_type_guards(tmp_path: Path) -> None:
    retriever, repos, *_ = _env(tmp_path, policy=ExhibitPolicy(max_bytes=10))
    big = retriever.retrieve(_doc("form8k.htm", "primary", "8-K", 1))
    assert big.status is RetrievalStatus.FAILED and "max_bytes" in big.reason
    declared = retriever.retrieve(_doc("form8k.htm", "primary", "8-K", 1, size=10_000))
    assert declared.status is RetrievalStatus.SKIPPED
    retriever2, repos2, *_ = _env(tmp_path)
    img = retriever2.retrieve(_doc("logo.jpg", "supporting", "GRAPHIC", 5))
    assert img.status is RetrievalStatus.FAILED and "content type" in img.reason
    assert repos2.snapshots.get(hashlib.sha256(BODIES["logo.jpg"][0]).hexdigest()) is None


def test_raw_hash_is_stable_across_refetch(tmp_path: Path) -> None:
    retriever, repos, store, _ = _env(tmp_path)
    a = retriever.retrieve(_doc("form8k.htm", "primary", "8-K", 1))
    b = retriever.retrieve(_doc("form8k.htm", "primary", "8-K", 1))
    assert a.snapshot is not None and b.snapshot is not None
    assert (
        a.snapshot.snapshot_id
        == b.snapshot.snapshot_id
        == hashlib.sha256(BODIES["form8k.htm"][0]).hexdigest()
    )
    assert b.snapshot == a.snapshot  # first capture retained


def test_raw_store_is_write_once_and_verifies(tmp_path: Path) -> None:
    store = FileSystemRawStore(tmp_path / "raw")
    key = store.put(b"abc")
    assert store.put(b"abc") == key and store.get(key) == b"abc"
    path = tmp_path / "raw" / Path(key)
    path.chmod(0o644)
    path.write_bytes(b"tampered")
    with pytest.raises(RawIntegrityError):
        store.get(key)
    with pytest.raises(RawIntegrityError):
        store.put(b"abc")  # refuses to silently replace a corrupt object
    with pytest.raises(NotFoundError):
        store.get(FileSystemRawStore.key_for("0" * 64))
    with pytest.raises(ValueError):
        store.get("../../etc/passwd")

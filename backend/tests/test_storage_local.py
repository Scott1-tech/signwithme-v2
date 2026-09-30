"""The filesystem backend used on small hosts, where MinIO does not fit."""
import os
import pathlib
import threading

import pytest

from app.core.exceptions import StorageError
from app.services.storage import BACKEND_DIR, LocalStorage


@pytest.fixture
def store(tmp_path) -> LocalStorage:
    backend = LocalStorage(root=str(tmp_path / "storage"))
    backend.ensure_bucket()
    return backend


def test_round_trip(store):
    store.put_bytes("contracts/abc/original.pdf", b"%PDF-1.4 hello")
    assert store.get_bytes("contracts/abc/original.pdf") == b"%PDF-1.4 hello"
    assert store.stream("contracts/abc/original.pdf").read() == b"%PDF-1.4 hello"


def test_nested_keys_create_their_directories(store):
    store.put_bytes("final/deep/nested/signed.pdf", b"x")
    assert store.get_bytes("final/deep/nested/signed.pdf") == b"x"


def test_local_copy_hands_back_a_real_path(store):
    store.put_bytes("templates/t/source.pdf", b"%PDF-1.4")
    with store.local_copy("templates/t/source.pdf") as path:
        with open(path, "rb") as handle:
            assert handle.read() == b"%PDF-1.4"


def test_a_traversing_key_is_refused(store):
    """A key is attacker-influenced only indirectly, but writing outside the
    storage root should never be reachable at all."""
    with pytest.raises(StorageError):
        store.put_bytes("../../etc/passwd", b"nope")
    with pytest.raises(StorageError):
        store.get_bytes("contracts/../../../etc/hosts")


def test_delete_is_idempotent(store):
    store.put_bytes("a/b.pdf", b"x")
    store.delete("a/b.pdf")
    store.delete("a/b.pdf")
    with pytest.raises(StorageError):
        store.get_bytes("a/b.pdf")


def test_an_absolute_storage_dir_is_used_as_given(tmp_path):
    backend = LocalStorage(root=str(tmp_path / "storage"))
    assert backend.root == (tmp_path / "storage").resolve()


def test_a_relative_storage_dir_is_anchored_to_the_backend_directory():
    """Not to the process CWD: uvicorn, a Celery worker and a service wrapper
    all start with a different one, and they must see the same files."""
    backend = LocalStorage(root="var/storage")
    assert backend.root == (BACKEND_DIR / "var" / "storage").resolve()


def test_a_relative_storage_dir_survives_a_changed_cwd(tmp_path, monkeypatch):
    """Write under one CWD, read under another, same configured value."""
    target = tmp_path / "storage"
    try:
        relative = os.path.relpath(target, BACKEND_DIR)
    except ValueError:  # pragma: no cover - only when TEMP is on another drive
        pytest.skip("temp directory is on a different drive than the backend")

    monkeypatch.chdir(tmp_path)
    writer = LocalStorage(root=relative)
    writer.ensure_bucket()
    writer.put_bytes("signatures/c/s.png", b"\x89PNG signature")
    assert target.is_dir(), "the relative root must not follow the CWD"

    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    reader = LocalStorage(root=relative)
    assert reader.root == writer.root
    assert reader.get_bytes("signatures/c/s.png") == b"\x89PNG signature"


def test_a_traversing_key_is_refused_from_any_cwd(tmp_path, monkeypatch):
    backend = LocalStorage(root=str(tmp_path / "storage"))
    backend.ensure_bucket()
    monkeypatch.chdir(tmp_path)

    keys = [
        "../escaped.png",
        "signatures/../../escaped.png",
        str(tmp_path / "escaped.png"),  # an absolute key replaces the root
        "/etc/passwd",
    ]
    if os.name == "nt":
        # A backslash is only a separator on Windows; on POSIX it is a legal
        # filename character and the key stays inside the root.
        keys += ["..\\escaped.png", "C:/Windows/System32/drivers/etc/hosts"]

    for key in keys:
        with pytest.raises(StorageError):
            backend.put_bytes(key, b"nope")
    assert not list(tmp_path.glob("escaped.png"))


def test_a_sibling_directory_sharing_the_root_prefix_is_refused(tmp_path):
    """`storage-evil` starts with `storage`, so a string-prefix containment
    check let this escape the root."""
    backend = LocalStorage(root=str(tmp_path / "storage"))
    backend.ensure_bucket()
    with pytest.raises(StorageError):
        backend.put_bytes("../storage-evil/pwned.png", b"escaped")
    with pytest.raises(StorageError):
        backend.get_bytes("../storage-evil/pwned.png")
    assert not (tmp_path / "storage-evil").exists()


def test_a_key_naming_a_unc_share_or_a_drive_is_refused(store):
    """`os.path.join` lets any of these *replace* the root it is joined to."""
    for key in [
        "\\\\server\\share\\pwned.png",
        "//server/share/pwned.png",
        "C:evil.png",  # drive-qualified without a separator: C:'s own CWD
        "c:/Windows/win.ini",
    ]:
        with pytest.raises(StorageError):
            store.put_bytes(key, b"nope")
        with pytest.raises(StorageError):
            store.get_bytes(key)


def test_a_key_with_a_null_byte_is_refused(store):
    """Truncation fodder for any C-level path API underneath."""
    with pytest.raises(StorageError):
        store.put_bytes("signatures/c/s.png\x00.txt", b"nope")


def test_an_empty_key_is_refused(store):
    with pytest.raises(StorageError):
        store.put_bytes("", b"nope")


def test_containment_never_consults_the_filesystem(store, monkeypatch):
    """The regression that `.resolve()` caused, stated as a rule.

    `Path.resolve()` is not a pure function of its argument on Windows: what it
    returns depends on whether the target and its parents happen to exist at
    that instant. Deciding containment with it made a legitimate write fail
    whenever a parallel request created the parent directory at the wrong
    moment. So the check must not call it at all.
    """
    def forbidden(self, strict=False):
        raise AssertionError("containment must not depend on filesystem state")

    monkeypatch.setattr(pathlib.Path, "resolve", forbidden)

    store.put_bytes("signatures/c/s.png", b"\x89PNG")
    assert store.get_bytes("signatures/c/s.png") == b"\x89PNG"
    with store.local_copy("signatures/c/s.png") as path:
        assert os.path.isfile(path)
    store.delete("signatures/c/s.png")
    # ...and the traversal refusals still hold without it.
    with pytest.raises(StorageError):
        store.put_bytes("../escaped.png", b"nope")


@pytest.mark.skipif(os.name != "nt", reason="the \\\\?\\ spelling is Windows-only")
def test_an_extended_length_resolve_result_is_not_a_false_positive(store, monkeypatch):
    r"""Model the exact Windows behaviour that produced the live 502s.

    `_getfinalpathname` answers with `\\?\C:\...` and in canonical letter case;
    `ntpath.realpath` keeps that prefix when a concurrent `mkdir` changes the
    error code between its two probes. A containment check must survive being
    handed that spelling for one side and the plain one for the other.
    """
    real_resolve = pathlib.Path.resolve
    real_realpath = os.path.realpath

    def extended(self, strict=False):
        return pathlib.Path("\\\\?\\" + str(real_resolve(self, strict=strict)).upper())

    monkeypatch.setattr(pathlib.Path, "resolve", extended)
    monkeypatch.setattr(os.path, "realpath", lambda p: "\\\\?\\" + str(real_realpath(p)).upper())

    store.put_bytes("signatures/c/s.png", b"\x89PNG")
    assert store.get_bytes("signatures/c/s.png") == b"\x89PNG"
    with pytest.raises(StorageError):
        store.put_bytes("../escaped.png", b"nope")


def test_parallel_writes_into_a_fresh_directory_all_succeed(store):
    """The live bug, reduced: several signatures for one new contract at once.

    Each round targets a parent directory that does not exist yet, which is
    what `signatures/<contract-uuid>/` is for the first placements on a freshly
    approved contract, and what made `Path.resolve()` disagree with itself.
    Against the old check this failed within the first few rounds.
    """
    rounds, workers = 40, 6
    failures: list[str] = []
    written: list[str] = []
    lock = threading.Lock()

    def place(key: str, barrier: threading.Barrier) -> None:
        barrier.wait()
        try:
            store.put_bytes(key, key.encode(), content_type="image/png")
        except StorageError as exc:  # pragma: no cover - the bug under test
            with lock:
                failures.append(f"{key}: {exc}")
        else:
            with lock:
                written.append(key)

    for round_number in range(rounds):
        barrier = threading.Barrier(workers)
        threads = [
            threading.Thread(
                target=place,
                args=(f"signatures/contract-{round_number}/sig-{worker}.png", barrier),
            )
            for worker in range(workers)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert failures == []
    assert len(written) == rounds * workers
    for key in written:
        assert store.get_bytes(key) == key.encode()


def test_a_partial_write_leaves_no_readable_key(store, monkeypatch):
    """Write-then-rename: a crash mid-write must not leave a truncated contract
    behind a key that looks valid."""
    original = LocalStorage.put_bytes

    def explode(self, key, data, content_type="application/pdf"):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.with_suffix(path.suffix + ".partial").write_bytes(data[:3])
        raise OSError("disk full")

    monkeypatch.setattr(LocalStorage, "put_bytes", explode)
    with pytest.raises(OSError):
        store.put_bytes("c/d.pdf", b"%PDF-1.4 complete")
    monkeypatch.setattr(LocalStorage, "put_bytes", original)
    with pytest.raises(StorageError):
        store.get_bytes("c/d.pdf")

"""Cross-process file locks and atomic JSON writes (standard library only).

Every shared file under .nitaaq/ (write ledger, approvals, run state) is
read-modify-written under an exclusive lock on a sibling ".lock" file and
saved by writing a temporary file and renaming it over the target, so a
reader never sees a half-written file and two writers never lose each
other's updates.

POSIX uses fcntl.flock; Windows uses msvcrt.locking. Both are advisory:
they protect processes that use these helpers, not arbitrary editors.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

try:  # POSIX
    import fcntl

    def _lock(fd):
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)
except ImportError:  # Windows
    import msvcrt

    def _lock(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


class LockTimeout(RuntimeError):
    pass


@contextmanager
def file_lock(path: str | Path, timeout: float = 30.0, poll: float = 0.02):
    """Exclusive lock for `path` (held on `path + ".lock"`)."""
    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                _lock(fd)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"could not lock {path} within {timeout}s")
                time.sleep(poll)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


def read_json(path: str | Path, default=None):
    p = Path(path)
    if not p.exists():
        return {} if default is None else default
    text = p.read_text(encoding="utf-8")
    return json.loads(text) if text.strip() else ({} if default is None else default)


def atomic_write_json(path: str | Path, data) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@contextmanager
def locked_json(path: str | Path, default=None, timeout: float = 30.0):
    """Lock, load, let the caller mutate the dict, then save atomically.

        with locked_json(p) as data:
            data["k"] = "v"
    """
    with file_lock(path, timeout=timeout):
        data = read_json(path, default)
        yield data
        atomic_write_json(path, data)


def append_jsonl(path: str | Path, record: dict, timeout: float = 30.0) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
    with file_lock(p, timeout=timeout):
        with open(p, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

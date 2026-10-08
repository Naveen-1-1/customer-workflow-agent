"""Where the store's data lives: a working-copy file, or memory for tests."""

import os
import shutil
import tempfile
from pathlib import Path
from typing import Protocol

from customer_workflow_agent.store.models import WorkingDB


def dump_db(db: WorkingDB) -> str:
    # exclude_none keeps the file shaped exactly like the original db.json.
    return db.model_dump_json(exclude_none=True, indent=2)


class StorageBackend(Protocol):
    def load(self) -> WorkingDB: ...

    def save(self, db: WorkingDB) -> None: ...

    def reset(self) -> WorkingDB: ...


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class FileBackend:
    """The original `data/db.json` is never written; changes go to a working copy."""

    def __init__(self, original: Path, working: Path) -> None:
        self.original = original
        self.working = working

    def load(self) -> WorkingDB:
        if not self.working.exists():
            self._copy_original()
        return WorkingDB.model_validate_json(self.working.read_bytes())

    def save(self, db: WorkingDB) -> None:
        _atomic_write(self.working, dump_db(db).encode())

    def reset(self) -> WorkingDB:
        self._copy_original()
        return self.load()

    def _copy_original(self) -> None:
        self.working.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.working.parent, suffix=".tmp")
        os.close(fd)
        try:
            shutil.copyfile(self.original, tmp)
            os.replace(tmp, self.working)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


class InMemoryBackend:
    """For tests: keeps the last saved state as JSON and counts saves."""

    def __init__(self, db: WorkingDB) -> None:
        self._original = dump_db(db)
        self._saved = self._original
        self.saves = 0

    def load(self) -> WorkingDB:
        return WorkingDB.model_validate_json(self._saved)

    def save(self, db: WorkingDB) -> None:
        self._saved = dump_db(db)
        self.saves += 1

    def reset(self) -> WorkingDB:
        self._saved = self._original
        return self.load()

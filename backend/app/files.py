"""File storage behind a small interface (local folder now, S3 in production)."""

import re
import uuid
from pathlib import Path
from typing import Protocol

from app.config import get_settings


class FileStore(Protocol):
    def save(self, prefix: str, filename: str, data: bytes) -> str: ...
    def read(self, ref: str) -> bytes: ...


class LocalFileStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, ref: str) -> Path:
        p = (self.root / ref).resolve()
        if not p.is_relative_to(self.root):
            raise FileNotFoundError(ref)
        return p

    def save(self, prefix: str, filename: str, data: bytes) -> str:
        ext = re.sub(r"[^a-z0-9.]", "", Path(filename).suffix.lower())[:10]
        ref = f"{prefix}/{uuid.uuid4().hex}{ext}"
        path = self._path(ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return ref

    def read(self, ref: str) -> bytes:
        return self._path(ref).read_bytes()


def get_file_store() -> FileStore:
    return LocalFileStore(get_settings().storage_dir)

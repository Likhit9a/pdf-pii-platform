"""Storage interface. LocalStorage is the default; an S3 backend can implement the same methods later."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class StorageBackend(ABC):
    @abstractmethod
    def save_original(self, doc_id: str, data: bytes) -> Path: ...

    @abstractmethod
    def original_path(self, doc_id: str) -> Path: ...

    @abstractmethod
    def output_path(self, doc_id: str) -> Path: ...

    @abstractmethod
    def new_temp_path(self, suffix: str = ".pdf") -> Path: ...

    @abstractmethod
    def promote_output(self, temp_path: Path, doc_id: str) -> Path: ...

    @abstractmethod
    def delete_original(self, doc_id: str) -> None: ...

    @abstractmethod
    def delete_all(self, doc_id: str) -> None: ...

    @abstractmethod
    def read_output(self, doc_id: str) -> bytes: ...

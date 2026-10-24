"""Metadata/search interface. SQLiteStore is the default; a DynamoDB store can implement it later."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class MetadataStore(ABC):
    @abstractmethod
    def create_document(self, record: dict[str, Any]) -> None: ...

    @abstractmethod
    def update_document(self, doc_id: str, **fields: Any) -> None: ...

    @abstractmethod
    def get_document(self, doc_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def list_documents(self, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]: ...

    @abstractmethod
    def index_text(self, doc_id: str, pages: list[str]) -> None: ...

    @abstractmethod
    def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]: ...

    @abstractmethod
    def delete_document(self, doc_id: str) -> None: ...

    @abstractmethod
    def stats(self) -> dict[str, Any]: ...

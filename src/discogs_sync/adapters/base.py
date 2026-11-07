from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from discogs_sync.models import Release


class CollectionSource(ABC):
    """A read-only source of Releases (Discogs in v1)."""

    name: str

    @abstractmethod
    def list_folders(self) -> list[str]: ...

    @abstractmethod
    def iter_releases(self, folder: str) -> Iterator[Release]: ...

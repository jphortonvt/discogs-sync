from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field


class AlbumCandidate(BaseModel):
    target: str
    target_id: str
    title: str
    artist: str
    year: int | None = None
    track_count: int | None = None
    cover_url: str | None = None
    edition_tags: list[str] = Field(default_factory=list)
    raw: dict = Field(default_factory=dict)


class VideoCandidate(BaseModel):
    target: str
    target_id: str
    title: str
    channel: str
    channel_kind: str | None = None
    duration_seconds: int | None = None
    is_official_artist: bool = False
    is_topic_channel: bool = False
    cover_url: str | None = None
    raw: dict = Field(default_factory=dict)


class LibraryTarget(ABC):
    """A streaming library you can search and (M5+) write to."""

    name: str

    @abstractmethod
    def search_album(self, artist: str, title: str, *, limit: int = 5) -> list[AlbumCandidate]: ...

    @abstractmethod
    def search_album_video(
        self, artist: str, title: str, *, limit: int = 5
    ) -> list[VideoCandidate]: ...

    @abstractmethod
    def list_library_albums(self) -> list[AlbumCandidate]: ...

    def add_album_to_library(self, candidate: AlbumCandidate) -> None:
        raise NotImplementedError

    def ensure_playlist(self, name: str) -> str:
        raise NotImplementedError

    def add_video_to_playlist(self, playlist_id: str, video: VideoCandidate) -> None:
        raise NotImplementedError

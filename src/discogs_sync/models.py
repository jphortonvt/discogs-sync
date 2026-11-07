from __future__ import annotations

from pydantic import BaseModel, Field


class Track(BaseModel):
    position: str | None = None
    title: str
    duration_seconds: int | None = None


class Release(BaseModel):
    """Normalized release used across all CollectionSource adapters."""

    source: str
    source_id: str

    artist: str
    title: str
    year: int | None = None
    label: str | None = None
    catalog_number: str | None = None
    format: str | None = None

    artists_raw: list[str] = Field(default_factory=list)
    tracks: list[Track] = Field(default_factory=list)
    edition_tags: list[str] = Field(default_factory=list)
    musicbrainz_id: str | None = None

    folder: str | None = None
    cover_url: str | None = None

    @property
    def track_count(self) -> int | None:
        return len(self.tracks) if self.tracks else None

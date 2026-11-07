from __future__ import annotations

from discogs_sync.normalize import normalize_artist, normalize_text, split_title, strip_diacritics


def test_strip_diacritics():
    assert strip_diacritics("Sigur Rós") == "Sigur Ros"
    assert strip_diacritics("Beyoncé") == "Beyonce"


def test_normalize_text_lowers_and_strips_punct():
    assert normalize_text("Pink Flag!") == "pink flag"
    assert normalize_text("  Hello,   World ") == "hello world"


def test_normalize_artist_strips_the():
    assert normalize_artist("The Beatles") == "beatles"


def test_normalize_artist_canonicalizes_separators():
    assert normalize_artist("Brian Eno and David Byrne") == normalize_artist(
        "Brian Eno & David Byrne"
    )
    assert normalize_artist("Jay-Z featuring Beyonce") == normalize_artist("Jay-Z & Beyonce")
    assert normalize_artist("Kanye West feat. Jay-Z") == normalize_artist("Kanye West & Jay-Z")


def test_split_title_extracts_edition_tags():
    base, tags = split_title("Pink Flag (Remastered 2018)")
    assert base == "pink flag"
    assert tags == ["remastered 2018"]


def test_split_title_multiple_tags():
    base, tags = split_title("Spiderland [Deluxe Edition] (Reissue)")
    assert base == "spiderland"
    assert set(tags) == {"deluxe edition", "reissue"}

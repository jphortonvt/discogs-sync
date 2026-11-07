from __future__ import annotations

import tomllib

from discogs_sync import config


def test_save_drops_nones(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_path)

    config.save({
        "discogs": {
            "username": "testuser",
            "consumer_key": None,
            "default_folder": "Uncategorized",
        },
        "ytmusic": {"client_id": None, "client_secret": None, "video_fallback_playlist": "X"},
    })
    text = cfg_path.read_text()
    assert "consumer_key" not in text
    assert "client_id" not in text

    parsed = tomllib.loads(text)
    assert parsed["discogs"]["username"] == "testuser"
    assert parsed["ytmusic"]["video_fallback_playlist"] == "X"


def test_load_round_trip(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_path)

    config.save({"discogs": {"username": "alice"}})
    loaded = config.load()
    assert loaded["discogs"]["username"] == "alice"
    assert loaded["discogs"]["default_folder"] == "Uncategorized"
    assert loaded["match"]["strong_threshold"] == 0.90

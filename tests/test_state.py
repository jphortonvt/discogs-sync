from __future__ import annotations

from discogs_sync.state import State


def test_start_finish_run(tmp_path):
    state = State(tmp_path / "state.db")
    run_id = state.start_run(source="discogs", target="ytmusic")
    state.finish_run(run_id, {"counts": {"added_album": 3}})

    latest = state.latest_run_summary()
    assert latest is not None
    assert latest["id"] == run_id
    assert latest["summary"]["counts"]["added_album"] == 3


def test_upsert_status_idempotent(tmp_path):
    state = State(tmp_path / "state.db")
    state.upsert_status(
        "1234", "ytmusic", status="added_album", target_kind="album", target_id="MPRE_x"
    )
    assert state.is_already_added("1234", "ytmusic")

    state.upsert_status("1234", "ytmusic", status="error", target_kind="album", target_id="MPRE_x")
    assert not state.is_already_added("1234", "ytmusic")
    row = state.get_status("1234", "ytmusic")
    assert row["status"] == "error"


def test_review_selections_round_trip(tmp_path):
    state = State(tmp_path / "state.db")
    state.upsert_selection("100", "album", "MPRE_x", payload={"title": "X", "artist": "Y"})
    state.upsert_selection("101", "video", "vid_y")

    sels = state.all_selections()
    assert sels["100"]["kind"] == "album"
    assert sels["100"]["payload"]["title"] == "X"
    assert sels["101"]["kind"] == "video"
    assert sels["101"]["payload"] is None


def test_remove_selection(tmp_path):
    state = State(tmp_path / "state.db")
    state.upsert_selection("100", "album", "MPRE_x")
    state.remove_selection("100")
    assert state.all_selections() == {}


def test_review_session_save_load(tmp_path):
    state = State(tmp_path / "state.db")
    payload = {"results": [{"id": 1}], "meta": "x"}
    state.save_review_session("Uncategorized", payload)
    loaded = state.load_review_session()
    assert loaded is not None
    assert loaded["folder"] == "Uncategorized"
    assert loaded["payload"] == payload


def test_clear_review_session_clears_selections_too(tmp_path):
    state = State(tmp_path / "state.db")
    state.upsert_selection("100", "album", "MPRE_x")
    state.save_review_session("F", {"x": 1})
    state.clear_review_session()
    assert state.load_review_session() is None
    assert state.all_selections() == {}

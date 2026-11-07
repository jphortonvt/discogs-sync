from __future__ import annotations

from typer.testing import CliRunner

from discogs_sync.cli.main import app

runner = CliRunner()


def test_help_top_level():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "discogs-sync" in result.stdout.lower() or "Usage" in result.stdout


def test_auth_help():
    result = runner.invoke(app, ["auth", "--help"])
    assert result.exit_code == 0


def test_dump_without_login_errors_cleanly(monkeypatch):
    monkeypatch.setattr("discogs_sync.cli.main._load_discogs_auth", lambda: None)
    result = runner.invoke(app, ["dump"])
    assert result.exit_code == 2
    assert "not logged in" in result.stdout.lower()

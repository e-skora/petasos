"""Acceptance test 1 (spec 005 5.1): `settings_from_environ` reads only the mapping
it is given, never `os.environ`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from petasos.serve import SettingsError, main, settings_from_environ


def test_petasos_db_set_gives_defaults_for_everything_else() -> None:
    settings = settings_from_environ({"PETASOS_DB": "/data/petasos.sqlite"})
    assert settings.db_path == Path("/data/petasos.sqlite")
    assert settings.archive_dir == Path("/data/archive")
    assert settings.host == "0.0.0.0"
    assert settings.port == 8080


def test_every_variable_set_is_honored() -> None:
    settings = settings_from_environ(
        {
            "PETASOS_DB": "/data/petasos.sqlite",
            "PETASOS_ARCHIVE_DIR": "/other/archive",
            "PETASOS_HOST": "127.0.0.1",
            "PETASOS_PORT": "9000",
        }
    )
    assert settings.archive_dir == Path("/other/archive")
    assert settings.host == "127.0.0.1"
    assert settings.port == 9000


@pytest.mark.parametrize("value", [None, ""])
def test_missing_or_empty_petasos_db_raises(value: str | None) -> None:
    environ = {} if value is None else {"PETASOS_DB": value}
    with pytest.raises(SettingsError) as excinfo:
        settings_from_environ(environ)
    assert "\n" not in str(excinfo.value)


@pytest.mark.parametrize("value", ["not-a-number", "0", "65536", "-1"])
def test_bad_port_raises(value: str) -> None:
    with pytest.raises(SettingsError) as excinfo:
        settings_from_environ({"PETASOS_DB": "/data/petasos.sqlite", "PETASOS_PORT": value})
    assert "\n" not in str(excinfo.value)


def test_it_never_reads_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PETASOS_DB", "/marker/path/that/should/never/be/used.sqlite")
    with pytest.raises(SettingsError):
        settings_from_environ({})


def test_main_with_no_petasos_db_returns_2_and_prints_the_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("PETASOS_DB", raising=False)
    exit_code = main([])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "PETASOS_DB" in captured.err


def test_main_rejects_an_unknown_argument(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PETASOS_DB", str(tmp_path / "p.sqlite"))
    exit_code = main(["--not-a-real-flag"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.err.strip() != ""

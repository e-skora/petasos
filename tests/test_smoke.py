"""The lowest bar that still proves the whole loop (uv, pytest, CI) works end to end."""

import petasos


def test_version_is_a_non_empty_string() -> None:
    assert isinstance(petasos.__version__, str)
    assert petasos.__version__

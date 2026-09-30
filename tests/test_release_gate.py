"""Tests for the release gate (`.github/scripts/release_gate.py`, change 005 spec 5.9).

Every decision is a pure function over hand-built JSON of the shape GitHub returns, so no
test reads the network, the environment, or a real repository. `main()` is exercised through
files under `tmp_path`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / ".github/scripts/release_gate.py"
_SPEC = importlib.util.spec_from_file_location("release_gate", MODULE_PATH)
release_gate = importlib.util.module_from_spec(_SPEC)
sys.modules["release_gate"] = release_gate
_SPEC.loader.exec_module(release_gate)

SHA = "a" * 40
OTHER = "b" * 40
REQUIRED = ["python", "demo", "private-identifiers"]


def run(
    run_id: int,
    number: int,
    *,
    sha: str = SHA,
    name: str = "CI",
    status: str = "completed",
    conclusion: str | None = "success",
) -> dict:
    return {
        "id": run_id,
        "run_number": number,
        "head_sha": sha,
        "name": name,
        "status": status,
        "conclusion": conclusion,
    }


def job(name: str, *, status: str = "completed", conclusion: str | None = "success") -> dict:
    return {"name": name, "status": status, "conclusion": conclusion}


def pages(key: str, *groups: list[dict]) -> list[dict]:
    return [{key: list(group)} for group in groups]


def test_select_run_picks_the_newest_successful_ci_run_for_the_exact_commit() -> None:
    doc = pages("workflow_runs", [run(10, 5), run(11, 6)], [run(12, 7)])
    run_id, reason = release_gate.select_run(doc, sha=SHA, workflow="CI")
    assert run_id == 12
    assert "succeeded" in reason


def test_select_run_refuses_when_no_run_exists_for_the_commit() -> None:
    doc = pages("workflow_runs", [run(10, 5, sha=OTHER)])
    run_id, reason = release_gate.select_run(doc, sha=SHA, workflow="CI")
    assert run_id is None
    assert "no `CI` workflow run" in reason


def test_select_run_refuses_a_queued_or_running_newest_run() -> None:
    for status in ("queued", "in_progress"):
        doc = pages("workflow_runs", [run(10, 5), run(11, 6, status=status, conclusion=None)])
        run_id, reason = release_gate.select_run(doc, sha=SHA, workflow="CI")
        assert run_id is None
        assert status in reason


def test_select_run_refuses_when_the_newest_run_failed_even_if_an_older_one_passed() -> None:
    doc = pages("workflow_runs", [run(10, 5), run(11, 6, conclusion="failure")])
    run_id, reason = release_gate.select_run(doc, sha=SHA, workflow="CI")
    assert run_id is None
    assert "concluded failure" in reason


def test_select_run_ignores_a_successful_namesake_from_another_workflow() -> None:
    doc = pages("workflow_runs", [run(10, 5, name="Claude review")])
    run_id, _ = release_gate.select_run(doc, sha=SHA, workflow="CI")
    assert run_id is None


def test_select_run_refuses_a_malformed_sha_and_malformed_pages() -> None:
    run_id, reason = release_gate.select_run(pages("workflow_runs", []), sha="abc", workflow="CI")
    assert run_id is None and "40" in reason
    with pytest.raises(TypeError):
        release_gate.select_run([{"nope": []}], sha=SHA, workflow="CI")


def test_check_jobs_requires_every_named_job_completed_with_success() -> None:
    doc = pages("jobs", [job("python"), job("demo")], [job("private-identifiers")])
    ok, reason = release_gate.check_jobs(doc, required=REQUIRED)
    assert ok and "succeeded" in reason


def test_check_jobs_refuses_a_missing_job() -> None:
    doc = pages("jobs", [job("python"), job("demo")])
    ok, reason = release_gate.check_jobs(doc, required=REQUIRED)
    assert not ok and "`private-identifiers` is missing" in reason


def test_check_jobs_refuses_a_failed_skipped_or_running_job() -> None:
    for status, conclusion in (
        ("completed", "failure"),
        ("completed", "skipped"),
        ("in_progress", None),
    ):
        doc = pages(
            "jobs",
            [job("python"), job("demo", status=status, conclusion=conclusion)],
            [job("private-identifiers")],
        )
        ok, reason = release_gate.check_jobs(doc, required=REQUIRED)
        assert not ok and "`demo`" in reason


def test_check_jobs_refuses_a_name_that_appears_with_conflicting_conclusions() -> None:
    doc = pages(
        "jobs",
        [job("python"), job("python", conclusion="failure"), job("demo")],
        [job("private-identifiers")],
    )
    ok, reason = release_gate.check_jobs(doc, required=REQUIRED)
    assert not ok and "`python`" in reason


def test_main_select_run_prints_the_run_id_and_check_jobs_exits_by_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = tmp_path / "runs.json"
    runs.write_text(json.dumps(pages("workflow_runs", [run(42, 9)])))
    assert release_gate.main(["select-run", "--sha", SHA, str(runs)]) == 0
    assert capsys.readouterr().out.strip() == "42"

    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps(pages("jobs", [job(n) for n in REQUIRED])))
    assert release_gate.main(["check-jobs", "--require", ",".join(REQUIRED), str(jobs)]) == 0
    jobs.write_text(json.dumps(pages("jobs", [job("python")])))
    assert release_gate.main(["check-jobs", "--require", ",".join(REQUIRED), str(jobs)]) == 1

    runs.write_text("not json")
    assert release_gate.main(["select-run", "--sha", SHA, str(runs)]) == 1
    assert release_gate.main(["select-run", "--sha", SHA, str(tmp_path / "missing.json")]) == 1


def test_smoke_result_maps_every_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    assert release_gate.smoke_result(0) == ("confirmed", False, False)
    line, warn, failed = release_gate.smoke_result(3)
    assert "capacity" in line and "unconfirmed" in line and warn and not failed
    line, warn, failed = release_gate.smoke_result(4)
    assert "receipt" in line and "unconfirmed" in line and warn and not failed
    for code in (1, 2, 5, 127):
        line, warn, failed = release_gate.smoke_result(code)
        assert line == f"FAILED (exit {code})" and not warn and failed
    assert release_gate.main(["smoke-result", "0"]) == 0
    assert "::warning::" not in capsys.readouterr().out
    assert release_gate.main(["smoke-result", "4"]) == 0
    assert "::warning::" in capsys.readouterr().out
    assert release_gate.main(["smoke-result", "1"]) == 1

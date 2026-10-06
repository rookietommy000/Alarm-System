"""CLI acceptance tests run against an isolated copy, never real session events."""
import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "tools" / "progress.sh"


@pytest.fixture
def cli(tmp_path):
    root = tmp_path / "repo with spaces"
    (root / "tools").mkdir(parents=True)
    (root / "session_status").mkdir()
    script = root / "tools" / "progress.sh"
    shutil.copy2(SOURCE, script)
    events = root / "session_status" / "events.jsonl"

    def run(*args, by="老師", title="登入修正"):
        env = os.environ.copy()
        env.pop("PROGRESS_BY", None)
        env.pop("PROGRESS_TITLE", None)
        if by is not None:
            env["PROGRESS_BY"] = by
        if title is not None:
            env["PROGRESS_TITLE"] = title
        return subprocess.run(
            [str(script), *args], cwd=tmp_path, env=env,
            capture_output=True, text=True, timeout=15,
        )

    return run, events


def records(events):
    return [json.loads(line) for line in events.read_text().splitlines()]


def accept(run, *args, **kwargs):
    result = run(*args, **kwargs)
    assert result.returncode == 0, result.stderr


def reject(run, events, *args, **kwargs):
    before = events.read_bytes() if events.exists() else b""
    result = run(*args, **kwargs)
    assert result.returncode != 0
    assert result.stderr.strip()
    assert (events.read_bytes() if events.exists() else b"") == before


def failed_task(run):
    for stage in ("待辦", "委派中", "測試中", "測試失敗"):
        accept(run, "T-012", stage)


def test_case_1_create_seven_fields_and_timestamp(cli):
    run, events = cli
    assert not events.exists()
    start = datetime.now(timezone.utc)
    accept(run, "T-012", "待辦", "docs/brief.md", "建立任務")
    end = datetime.now(timezone.utc)
    rows = records(events)
    assert len(rows) == 1
    event = rows[0]
    assert set(event) == {"t", "task", "title", "stage", "by", "ref", "note"}
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?[+-]\d\d:\d\d", event.pop("t"))
    timestamp = datetime.fromisoformat(rows_timestamp(events))
    assert start.replace(microsecond=0) <= timestamp <= end
    assert event == dict(task="T-012", title="登入修正", stage="待辦",
                         by="老師", ref="docs/brief.md", note="建立任務")


def rows_timestamp(events):
    return records(events)[0]["t"]


def test_case_2_invalid_stage_does_not_append(cli):
    run, events = cli
    reject(run, events, "T-012", "待辯")


def test_case_3_missing_role_does_not_append(cli):
    run, events = cli
    reject(run, events, "T-012", "待辦", by=None)


def test_case_4_invalid_role_does_not_append(cli):
    run, events = cli
    reject(run, events, "T-012", "待辦", by="隨便")


def test_case_5_failed_test_cannot_enter_qa(cli):
    run, events = cli
    failed_task(run)
    accept(run, "T-999", "待辦")  # Another task must not hide T-012's failure.
    reject(run, events, "T-012", "QA中", by="QA")


def test_case_6_failed_test_can_be_delegated_again(cli):
    run, events = cli
    failed_task(run)
    accept(run, "T-012", "委派中", title=None)
    assert records(events)[-1]["stage"] == "委派中"
    assert records(events)[-1]["title"] == ""


def test_case_7_append_preserves_previous_bytes(cli):
    run, events = cli
    accept(run, "T-012", "待辦")
    before = events.read_bytes()
    accept(run, "T-012", "委派中")
    assert events.read_bytes().startswith(before)
    assert len(records(events)) == 2


@pytest.mark.parametrize("task", ["012", "T-", "T-a", "T-１２", "T-12\n"])
def test_invalid_task(cli, task):
    run, events = cli
    reject(run, events, task, "待辦")


@pytest.mark.parametrize("args", [(), ("T-1",), ("T-1", "待辦", "", "", "extra")])
def test_argument_count(cli, args):
    run, events = cli
    reject(run, events, *args)


@pytest.mark.parametrize("title", [None, "", " \t\n"])
def test_todo_requires_title(cli, title):
    run, events = cli
    reject(run, events, "T-1", "待辦", title=title)


@pytest.mark.parametrize("stage", ["委派中", "測試中", "QA中", "已commit", "測試失敗", "待你決定"])
def test_new_task_must_start_at_todo(cli, stage):
    run, events = cli
    reject(run, events, "T-1", stage)


@pytest.mark.parametrize("stage", ["測試中", "測試失敗", "待你決定", "已commit"])
def test_failed_test_rejects_other_destinations(cli, stage):
    run, events = cli
    failed_task(run)
    reject(run, events, "T-012", stage)


def test_failed_test_can_return_to_todo(cli):
    run, events = cli
    failed_task(run)
    accept(run, "T-012", "待辦")
    assert records(events)[-1]["stage"] == "待辦"


@pytest.mark.parametrize("by", ["使用者", "老師", "員工", "QA", "hook"])
def test_allowed_roles_and_json_escaping(cli, by):
    run, events = cli
    value = '引號"、反斜線\\、換行\n、tab\t、$(echo nope)'
    accept(run, "T-1", "待辦", value, value, by=by, title=value)
    assert len(events.read_text().splitlines()) == 1
    event = records(events)[0]
    assert event["title"] == event["ref"] == event["note"] == value
    assert event["by"] == by


def test_other_transitions_are_not_restricted(cli):
    run, events = cli
    for stage in ("待辦", "委派中", "已commit", "待你決定", "QA中"):
        accept(run, "T-1", stage)
    assert records(events)[0]["ref"] == records(events)[0]["note"] == ""


def test_concurrent_creation_checks_latest_state_under_lock(cli):
    run, events = cli
    accept(run, "T-1", "待辦")
    accept(run, "T-1", "測試中")
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: run("T-1", "測試失敗"), range(6)))
    assert sum(result.returncode == 0 for result in results) == 1
    assert len(records(events)) == 3


def test_corrupt_history_is_not_modified(cli):
    run, events = cli
    events.write_text('{"task": "T-1"')
    reject(run, events, "T-1", "待辦")

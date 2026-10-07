import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo with spaces"
    (root / "tools").mkdir(parents=True)
    (root / "session_status").mkdir()
    for name in ("progress.sh", "generate_progress.sh"):
        shutil.copy2(ROOT / "tools" / name, root / "tools" / name)
    return root


def event(task, stage, hours=0, **kwargs):
    return dict(t=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)).isoformat(),
                task=task, title=kwargs.get('title', '任務名稱'), stage=stage,
                by='老師', ref='job_123', note='請確認方向', **{k: v for k, v in kwargs.items() if k != 'title'})


def write(repo, events):
    (repo / 'session_status/events.jsonl').write_text(
        ''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in events))


def generate(repo):
    result = subprocess.run(['sh', str(repo / 'tools/generate_progress.sh')],
                            cwd=repo.parent, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return (repo / 'PROGRESS.md').read_text()


def test_1_overview_latest_timestamp_waiting_and_stuck(repo):
    write(repo, [event('T-1', '待你決定', 25, title=''),
                 event('T-1', '待辦', 30, title='原始標題'),
                 event('T-2', '測試中', 2)])
    report = generate(repo)
    assert '| T-1 | 原始標題 | 待你決定 |' in report
    assert '- T-1：請確認方向（1 天 1 小時' in report
    stuck = report.split('## ⚠')[1]
    assert 'T-1' in stuck and 'T-2' not in stuck
    timestamp = report.split('最後更新：')[1].splitlines()[0]
    assert abs((dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(timestamp)).total_seconds()) < 5


def test_2_old_committed_task_is_not_stuck(repo):
    write(repo, [event('T-1', '已commit', 100), event('T-2', 'QA中', 25)])
    stuck = generate(repo).split('## ⚠')[1]
    assert 'T-1' not in stuck and 'T-2' in stuck


def test_3_ratio_counts_only_latest_committed_tasks(repo):
    write(repo, [event('T-1', '待辦', 10), event('T-1', '已commit'),
                 event('T-2', 'QA中'), event('T-3', '測試失敗'), event('T-4', '待辦')])
    assert '▓▓░░░░░░░░ 1/4 (25%)' in generate(repo)


def test_4_regeneration_replaces_report(repo):
    write(repo, [event('T-1', '已commit')])
    generate(repo)
    (repo / 'PROGRESS.md').write_text('manually edited content\n')
    report = generate(repo)
    assert 'manually edited' not in report
    assert report.count('# 專案進度') == 1
    assert generate(repo).count('| T-1 |') == 1


def test_5_progress_success_refreshes_and_failure_does_not(repo):
    env = {**os.environ, 'PROGRESS_BY': '老師', 'PROGRESS_TITLE': '自動報表'}
    script = str(repo / 'tools/progress.sh')
    subprocess.run(['sh', script, 'T-1', '待辦'], env=env, check=True)
    assert '| T-1 | 自動報表 | 待辦 |' in (repo / 'PROGRESS.md').read_text()
    subprocess.run(['sh', script, 'T-1', '已commit'], env=env, check=True)
    before = (repo / 'PROGRESS.md').read_bytes()
    assert b'1/1 (100%)' in before
    result = subprocess.run(['sh', script, 'T-1', 'typo'], env=env, capture_output=True)
    assert result.returncode != 0
    assert (repo / 'PROGRESS.md').read_bytes() == before


@pytest.mark.parametrize('exists', [False, True])
def test_missing_or_empty_events(repo, exists):
    if exists:
        write(repo, [])
    report = generate(repo)
    assert '0/0 (0%)' in report
    assert report.count('\n無\n') == 2
    assert (repo / 'session_status/events.jsonl').exists() == exists


def test_timezone_order_and_markdown_escaping(repo):
    newer = event('T-1', 'QA中', title='a|b\nc')
    newer['t'] = '2026-10-07T09:00:00+08:00'
    older = event('T-1', '待辦', title='old')
    older['t'] = '2026-10-07T00:30:00+00:00'
    write(repo, [newer, older])
    assert '| T-1 | a&#124;b<br>c | QA中 |' in generate(repo)


def test_equal_timestamps_use_later_line(repo):
    first = event('T-1', '待辦')
    second = {**first, 'stage': '已commit'}
    write(repo, [first, second])
    assert '1/1 (100%)' in generate(repo)

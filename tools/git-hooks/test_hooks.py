"""Real Git integration tests, isolated from the working repository."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def call(repo, *args, check=True, **kwargs):
    env = os.environ.copy()
    for key in list(env):
        if key.startswith('GIT_') or key.startswith('PROGRESS_'):
            env.pop(key)
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_NAME='Hook Test', GIT_AUTHOR_EMAIL='test@example.invalid',
               GIT_COMMITTER_NAME='Hook Test', GIT_COMMITTER_EMAIL='test@example.invalid')
    env.update(kwargs.pop('env', {}))
    result = subprocess.run(args, cwd=repo, env=env, text=True,
                            capture_output=True, timeout=30, **kwargs)
    if check:
        assert result.returncode == 0, result.stderr
    return result


def git(repo, *args, **kwargs):
    return call(repo, 'git', *args, **kwargs)


def install(repo, **kwargs):
    return call(repo, 'sh', 'tools/git-hooks/install.sh', **kwargs)


def todo(repo):
    call(repo, './tools/progress.sh', 'T-999', '待辦',
         env={'PROGRESS_BY': '老師', 'PROGRESS_TITLE': 'hook 驗收'})


def rows(repo):
    path = repo / 'session_status/events.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / 'repo with spaces'
    repo.mkdir()
    git(repo, 'init', '-q')
    shutil.copytree(ROOT / 'tools/git-hooks', repo / 'tools/git-hooks',
                    ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    shutil.copy2(ROOT / 'tools/progress.sh', repo / 'tools/progress.sh')
    (repo / 'session_status').mkdir()
    (repo / 'session_status/.gitkeep').touch()
    git(repo, 'add', 'tools', 'session_status/.gitkeep')
    git(repo, 'commit', '-qm', 'initial')
    return repo


def test_1_tagged_commit_records_hash(repo):
    todo(repo)
    install(repo)
    git(repo, 'commit', '--allow-empty', '-qm', '完成 [T-999] [T-123]')
    event = rows(repo)[-1]
    assert len(rows(repo)) == 2
    assert event['task'] == 'T-999'
    assert event['stage'] == '已commit'
    assert event['by'] == 'hook'
    assert event['note'] == ''
    assert event['ref'] == git(repo, 'rev-parse', 'HEAD').stdout.strip()


def test_2_untagged_commit_silently_skips(repo):
    install(repo)
    result = git(repo, 'commit', '--allow-empty', '-qm', 'no task [T-abc]')
    assert result.stdout == result.stderr == ''
    assert rows(repo) == []


def test_3_real_push_records_attempt(repo, tmp_path):
    remote = tmp_path / 'remote.git'
    git(repo, 'init', '--bare', '-q', str(remote))
    git(repo, 'remote', 'add', 'origin', str(remote))
    todo(repo)
    install(repo)
    git(repo, 'commit', '--allow-empty', '-qm', '[T-999] push test')
    head = git(repo, 'rev-parse', 'HEAD').stdout.strip()
    git(repo, 'push', '-q', 'origin', 'HEAD:refs/heads/main')
    assert len(rows(repo)) == 3
    assert rows(repo)[-1]['ref'] == head
    assert rows(repo)[-1]['stage'] == '已commit'
    assert rows(repo)[-1]['note'] == 'push 嘗試（pre-push，尚未確認成功）'
    assert git(repo, '--git-dir', str(remote), 'rev-parse', 'refs/heads/main').stdout.strip() == head
    git(repo, 'push', '-q', 'origin', 'HEAD:refs/heads/main')
    assert len(rows(repo)) == 3


def test_4_fresh_clone_requires_install(repo, tmp_path):
    clone = tmp_path / 'clone'
    git(repo, 'clone', '-q', str(repo), str(clone))
    assert not (clone / '.git/hooks/post-commit').exists()
    assert not (clone / '.git/hooks/pre-push').exists()
    git(clone, 'commit', '--allow-empty', '-qm', '[T-999] before install')
    assert rows(clone) == []
    todo(clone)
    install(clone)
    git(clone, 'commit', '--allow-empty', '-qm', '[T-999] after install')
    assert len(rows(clone)) == 2


@pytest.mark.parametrize('failed', [False, True])
def test_rejected_progress_does_not_fail_commit(repo, failed):
    if failed:
        todo(repo)
        for stage in ('測試中', '測試失敗'):
            call(repo, './tools/progress.sh', 'T-999', stage, env={'PROGRESS_BY': '員工'})
    before = rows(repo)
    install(repo)
    result = git(repo, 'commit', '--allow-empty', '-qm', '[T-999] refused progress')
    assert rows(repo) == before
    assert len(result.stderr.splitlines()) == 1
    assert 'commit 已完成' in result.stderr


@pytest.mark.parametrize('hook', ['post-commit', 'pre-push'])
def test_installer_preserves_existing_hooks(repo, hook):
    path = repo / '.git/hooks' / hook
    path.write_text('existing hook\n')
    result = install(repo, check=False)
    assert result.returncode != 0
    assert path.read_text() == 'existing hook\n'
    other = 'pre-push' if hook == 'post-commit' else 'post-commit'
    assert not (repo / '.git/hooks' / other).exists()


def test_installer_preserves_broken_symlink_and_custom_hook_path(repo):
    path = repo / '.git/hooks/post-commit'
    path.symlink_to('missing')
    assert install(repo, check=False).returncode != 0
    assert path.is_symlink()
    path.unlink()
    git(repo, 'config', 'core.hooksPath', 'custom-hooks')
    assert install(repo, check=False).returncode != 0
    assert not path.exists()


def test_push_uses_pushed_branch_not_head_and_no_backtracking(repo, tmp_path):
    remote = tmp_path / 'remote.git'
    git(repo, 'init', '--bare', '-q', str(remote))
    git(repo, 'remote', 'add', 'origin', str(remote))
    todo(repo)
    install(repo)
    git(repo, 'commit', '--allow-empty', '-qm', '[T-999] target')
    target = git(repo, 'rev-parse', 'HEAD').stdout.strip()
    git(repo, 'branch', 'target')
    git(repo, 'commit', '--allow-empty', '-qm', 'untagged newer head')
    git(repo, 'push', '-q', 'origin', 'target:refs/heads/target')
    assert len(rows(repo)) == 3
    assert rows(repo)[-1]['ref'] == target
    before = rows(repo)
    git(repo, 'push', '-q', 'origin', 'HEAD:refs/heads/target')
    assert rows(repo) == before
    git(repo, 'push', '-q', 'origin', ':refs/heads/target')
    assert rows(repo) == before


def test_progress_failure_does_not_block_push(repo, tmp_path):
    remote = tmp_path / 'remote.git'
    git(repo, 'init', '--bare', '-q', str(remote))
    git(repo, 'commit', '--allow-empty', '-qm', '[T-999] task not initialized')
    install(repo)
    result = git(repo, 'push', '-q', str(remote), 'HEAD:refs/heads/main')
    assert '進度未記錄' in result.stderr
    assert rows(repo) == []


def test_remote_rejection_leaves_documented_attempt(repo, tmp_path):
    remote = tmp_path / 'remote.git'
    git(repo, 'init', '--bare', '-q', str(remote))
    hook = remote / 'hooks/pre-receive'
    hook.write_text('#!/bin/sh\nexit 1\n')
    hook.chmod(0o755)
    todo(repo)
    install(repo)
    git(repo, 'commit', '--allow-empty', '-qm', '[T-999] rejected push')
    result = git(repo, 'push', '-q', str(remote), 'HEAD:refs/heads/main', check=False)
    assert result.returncode != 0
    assert len(rows(repo)) == 3
    assert '尚未確認成功' in rows(repo)[-1]['note']

"""Real local Git tests; no network, credentials, or production reports used."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from tools import publish_shadow_report_batch as publisher


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def write_batch(repo: Path, number: int, *, generated=False):
    reports = repo / "reports"
    reports.mkdir(exist_ok=True)
    for filename in ("all_analysis.json", "decision_hub.json", "decision_hub_01.json", "trade_plan_shadow.json", "trade_plan_shadow_health.json", "public_plan_status.json", "trade_plan_validation.json", "tw_prospective_registry.json"):
        (reports / filename).write_text(json.dumps({"batch": number, "generated": generated}))


@pytest.fixture
def repositories(tmp_path):
    bare = tmp_path / "remote.git"
    bare.mkdir()
    git(bare, "init", "--bare", "--initial-branch=main")
    competitor = tmp_path / "briefing"
    competitor.mkdir()
    git(competitor, "init", "--initial-branch=main")
    git(competitor, "config", "user.name", "Local test")
    git(competitor, "config", "user.email", "test@example.invalid")
    (competitor / ".gitignore").write_text(".prediction_engine/\n")
    write_batch(competitor, 1)
    git(competitor, "add", ".")
    git(competitor, "commit", "-m", "initial frozen batch")
    git(competitor, "remote", "add", "origin", str(bare))
    git(competitor, "push", "-u", "origin", "main")
    source = tmp_path / "workflow"
    git(tmp_path, "clone", str(bare), str(source))
    return source, competitor, bare


def generate(worktree: Path):
    frozen = json.loads((worktree / "reports/all_analysis.json").read_text())
    for path in publisher.REPORT_FILES + ("reports/decision_hub_01.json",):
        (worktree / path).write_text(json.dumps({"batch": frozen["batch"], "generated": True}))
    private = worktree / ".prediction_engine"
    private.mkdir(exist_ok=True)
    (private / "private.json").write_text('{"private":true}')


def advance(competitor: Path, number: int) -> str:
    git(competitor, "pull", "--ff-only", "origin", "main")
    write_batch(competitor, number)
    git(competitor, "add", "reports")
    git(competitor, "commit", "-m", f"official briefing batch {number}")
    git(competitor, "push", "origin", "main")
    return git(competitor, "rev-parse", "HEAD")


def assert_cleaned(source: Path):
    assert git(source, "worktree", "list", "--porcelain").count("worktree ") == 1


def is_push(args):
    return list(args[:3]) == ["git", "push", "origin"]


def test_race_recreates_worktree_regenerates_fresh_batch_and_preserves_source(repositories):
    source, competitor, bare = repositories
    # A dirty caller is not cleaned, reset, rebased, configured, or committed.
    (source / "reports/all_analysis.json").write_text("local uncommitted edits")
    (source / "personal.txt").write_text("local untracked file")
    before_status = git(source, "status", "--porcelain")
    before_config = (source / ".git/config").read_bytes()
    calls, generations, concurrent = [], [], []
    def runner(args, *, cwd):
        calls.append(list(args))
        if is_push(args) and not concurrent:
            concurrent.append(advance(competitor, 2))
        return publisher.run_command(args, cwd=cwd)
    def generator(worktree):
        generations.append((worktree, json.loads((worktree / "reports/all_analysis.json").read_text())["batch"]))
        generate(worktree)
    result = publisher.run(source, runner=runner, generate=generator)
    assert result["status"] == "published"
    assert result["attempts"] == 2
    assert [batch for _, batch in generations] == [1, 2]
    assert generations[0][0] != generations[1][0]
    assert all(not path.exists() for path, _ in generations)
    assert result["base_sha"] == concurrent[0]
    assert git(bare, "rev-parse", "main^") == concurrent[0]
    assert git(bare, "rev-parse", "main") == result["commit_sha"]
    assert json.loads(git(bare, "show", "main:reports/trade_plan_shadow.json")) == {"batch": 2, "generated": True}
    assert json.loads(git(bare, "show", "main:reports/all_analysis.json")) == {"batch": 2, "generated": False}
    assert git(source, "status", "--porcelain") == before_status
    assert (source / ".git/config").read_bytes() == before_config
    assert (source / "reports/all_analysis.json").read_text() == "local uncommitted edits"
    assert (source / "personal.txt").read_text() == "local untracked file"
    changed = git(bare, "diff-tree", "--no-commit-id", "--name-only", "-r", "main").splitlines()
    assert set(changed) == {*publisher.REPORT_FILES, "reports/decision_hub_01.json"}
    assert all("rebase" not in command and "reset" not in command for command in calls)
    assert all("--force" not in command for command in calls if is_push(command))
    assert_cleaned(source)


def test_auth_failure_without_remote_advance_does_not_retry(repositories):
    source, _, bare = repositories
    before = git(bare, "rev-parse", "main")
    pushes, generations = [], []
    def runner(args, *, cwd):
        if is_push(args):
            pushes.append(list(args))
            return subprocess.CompletedProcess(args, 128, "", "authentication failed")
        return publisher.run_command(args, cwd=cwd)
    def generator(worktree):
        generations.append(worktree)
        generate(worktree)
    with pytest.raises(publisher.PublishError, match="without main advancing"):
        publisher.run(source, runner=runner, generate=generator)
    assert len(pushes) == len(generations) == 1
    assert git(bare, "rev-parse", "main") == before
    assert not generations[0].exists()
    assert_cleaned(source)


def test_generation_failure_never_pushes_and_cleans_only_owned_worktree(repositories):
    source, _, _ = repositories
    calls, paths = [], []
    def runner(args, *, cwd):
        calls.append(list(args))
        return publisher.run_command(args, cwd=cwd)
    def fail(worktree):
        paths.append(worktree)
        generate(worktree)
        raise publisher.PublishError("generation test failed")
    with pytest.raises(publisher.PublishError, match="generation test failed"):
        publisher.run(source, runner=runner, generate=fail)
    assert not any(is_push(command) for command in calls)
    assert not paths[0].exists()
    assert_cleaned(source)


def test_noop_does_not_commit_or_push(repositories):
    source, _, bare = repositories
    before = git(bare, "rev-parse", "main")
    calls = []
    def runner(args, *, cwd):
        calls.append(list(args))
        return publisher.run_command(args, cwd=cwd)
    result = publisher.run(source, runner=runner, generate=lambda worktree: None)
    assert result == {"status": "unchanged", "attempts": 1, "base_sha": before}
    assert git(bare, "rev-parse", "main") == before
    assert not any(is_push(command) or "commit" in command for command in calls)
    assert_cleaned(source)


def test_attempt_exhaustion_never_publishes_obsolete_generated_outputs(repositories):
    source, competitor, bare = repositories
    pushes, generations = [], []
    def runner(args, *, cwd):
        if is_push(args):
            pushes.append(list(args))
            advance(competitor, len(pushes) + 1)
        return publisher.run_command(args, cwd=cwd)
    def generator(worktree):
        generations.append(worktree)
        generate(worktree)
    with pytest.raises(publisher.PublishError, match="after 3 attempts"):
        publisher.run(source, runner=runner, generate=generator)
    assert len(pushes) == len(generations) == 3
    assert all(not path.exists() for path in generations)
    assert json.loads(git(bare, "show", "main:reports/trade_plan_shadow.json")) == {"batch": 4, "generated": False}
    assert_cleaned(source)


def test_unexpected_tracked_formal_edit_blocks_commit_and_push(repositories):
    source, _, bare = repositories
    before = git(bare, "rev-parse", "main")
    calls = []
    def runner(args, *, cwd):
        calls.append(list(args))
        return publisher.run_command(args, cwd=cwd)
    def wrong(worktree):
        generate(worktree)
        (worktree / "reports/all_analysis.json").write_text('{"wrong":true}')
    with pytest.raises(publisher.PublishError, match="outside the shadow-output allowlist"):
        publisher.run(source, runner=runner, generate=wrong)
    assert not any(is_push(command) or "commit" in command for command in calls)
    assert git(bare, "rev-parse", "main") == before
    assert_cleaned(source)


def test_lost_push_response_is_verified_without_duplicate_publication(repositories):
    source, _, bare = repositories
    pushes = []
    def runner(args, *, cwd):
        result = publisher.run_command(args, cwd=cwd)
        if is_push(args):
            pushes.append(list(args))
            assert result.returncode == 0
            return subprocess.CompletedProcess(args, 1, "", "connection lost after acceptance")
        return result
    result = publisher.run(source, runner=runner, generate=generate)
    assert result["status"] == "published"
    assert result["attempts"] == len(pushes) == 1
    assert git(bare, "rev-parse", "main") == result["commit_sha"]
    assert_cleaned(source)


def test_noop_on_old_batch_retries_against_new_main(repositories):
    source, competitor, _ = repositories
    generations = []
    def generator(worktree):
        generations.append(worktree)
        if len(generations) == 1:
            advance(competitor, 2)
        else:
            generate(worktree)
    result = publisher.run(source, generate=generator)
    assert result["attempts"] == 2
    assert result["status"] == "published"
    assert_cleaned(source)


def test_fixed_production_generation_contract(tmp_path):
    calls = []
    def runner(args, *, cwd):
        calls.append((list(args), cwd))
        return subprocess.CompletedProcess(args, 0, "", "")
    publisher.generate_and_test(tmp_path, runner=runner)
    assert len(calls) == 4
    assert calls[0][0][1:] == ["decision_hub.py", "--reports-dir", "reports", "--refresh-shadow-inputs-only", "--attest-tw-official"]
    assert calls[1][0][1:] == ["trade_plan_shadow.py", "--reports-dir", "reports"]
    assert calls[2][0][1:] == ["trade_plan_validation.py", "--reports-dir", "reports"]
    assert calls[3][0][1:] == ["-m", "pytest", "-q", *publisher.FOCUSED_TESTS]
    assert all(cwd == tmp_path for _, cwd in calls)


@pytest.mark.parametrize("attempts", [0, 4, True])
def test_attempt_bound_enforced_before_any_command(tmp_path, attempts):
    def fail(*args, **kwargs):
        pytest.fail("invalid attempt count must not invoke Git")
    with pytest.raises(ValueError):
        publisher.run(tmp_path, max_attempts=attempts, runner=fail)


def test_auth_failure_does_not_retry_even_if_another_job_advanced_main(repositories):
    source, competitor, bare = repositories
    pushes = []
    def runner(args, *, cwd):
        if is_push(args):
            pushes.append(list(args))
            advance(competitor, 2)
            return subprocess.CompletedProcess(args, 128, "", "authentication failed")
        return publisher.run_command(args, cwd=cwd)
    with pytest.raises(publisher.PublishError, match="non-race reason"):
        publisher.run(source, runner=runner, generate=generate)
    assert len(pushes) == 1
    assert json.loads(git(bare, "show", "main:reports/trade_plan_shadow.json")) == {"batch": 2, "generated": False}
    assert_cleaned(source)


def test_stale_briefing_rebase_cannot_replay_registry_and_publisher_keeps_latest(repositories):
    source, competitor, bare = repositories
    # A normal briefing changes its own reports, but its read-only writer does
    # not stage or modify the prospective ledger.
    ledger = 'reports/tw_prospective_registry.json'
    original = json.loads((source / ledger).read_text())
    (source / 'reports/all_analysis.json').write_text('{"batch":2,"generated":false}')
    git(source, 'add', 'reports/all_analysis.json')
    git(source, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'briefing')
    newer = {'registered_plan_ids':['first','concurrent'], 'history':['kept']}
    (competitor / ledger).write_text(json.dumps(newer))
    git(competitor,'add',ledger);git(competitor,'commit','-m','concurrent registry')
    git(competitor,'push','origin','main')
    git(source,'fetch','origin','main');git(source,'-c','user.name=Test','-c','user.email=test@example.invalid','rebase','-X','theirs','origin/main')
    assert json.loads((source / ledger).read_text()) == newer
    git(source,'push','origin','HEAD:main')
    def preserving_generator(worktree):
        saved = (worktree / ledger).read_bytes()
        generate(worktree)
        (worktree / ledger).write_bytes(saved)
    publisher.run(source,generate=preserving_generator)
    assert json.loads(git(bare,'show',f'main:{ledger}')) == newer
    assert original != newer


def test_missing_status_blocks_entire_batch_and_preserves_remote(repositories):
    source, competitor, bare = repositories
    before = git(bare, "rev-parse", "main")
    calls = []
    def runner(args, *, cwd):
        calls.append(list(args))
        return publisher.run_command(args, cwd=cwd)
    def missing_status(worktree):
        generate(worktree)
        (worktree / "reports/public_plan_status.json").unlink()
    with pytest.raises(publisher.PublishError, match="all required shadow reports"):
        publisher.run(source, runner=runner, generate=missing_status)
    assert git(bare, "rev-parse", "main") == before
    assert not any(is_push(args) for args in calls)
    assert_cleaned(source)

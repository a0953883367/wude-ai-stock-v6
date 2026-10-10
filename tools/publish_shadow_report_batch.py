"""Publish a regenerated shadow batch against the latest main without rebasing.

Every bounded attempt uses a new, detached temporary worktree. Only approved
shadow report outputs are committed; the caller's checkout and configuration
are untouched. Existing Git authentication is inherited through the worktree.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Callable, Sequence

MAX_ATTEMPTS = 3
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
REPORT_FILES = (
    "reports/trade_plan_shadow.json",
    "reports/trade_plan_shadow_health.json",
    "reports/trade_plan_validation.json",
    "reports/tw_prospective_registry.json",
)
CHUNK_PATTERN = re.compile(r"reports/decision_hub_[0-9]{2}\.json")
FOCUSED_TESTS = (
    "tests/test_trade_plan_shadow.py", "tests/test_trade_plan_validation.py",
    "tests/test_shadow_hub_refresh.py", "tests/test_tw_daily_shadow_attestation.py",
)
Runner = Callable[..., subprocess.CompletedProcess[str]]


class PublishError(RuntimeError):
    """A generation, verification, or publication failed safely."""


def run_command(args: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), cwd=cwd, text=True, capture_output=True, check=False)


def _checked(runner: Runner, args: Sequence[str], *, cwd: Path) -> str:
    result = runner(list(args), cwd=cwd)
    if result.returncode:
        # Do not echo Git's remote/authentication output or environment values.
        raise PublishError(f"{args[0]} {args[1]} failed (exit {result.returncode})")
    return result.stdout or ""


def _fetch_main(repo: Path, runner: Runner) -> str:
    _checked(runner, ["git", "fetch", "origin", "refs/heads/main"], cwd=repo)
    sha = _checked(runner, ["git", "rev-parse", "--verify", "FETCH_HEAD^{commit}"], cwd=repo).strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", sha):
        raise PublishError("fetch did not return a valid main commit")
    return sha


def _ancestor(repo: Path, older: str, newer: str, runner: Runner) -> bool:
    result = runner(["git", "merge-base", "--is-ancestor", older, newer], cwd=repo)
    if result.returncode not in (0, 1):
        raise PublishError("could not verify remote commit ancestry")
    return result.returncode == 0


def _race_rejection(result: subprocess.CompletedProcess[str]) -> bool:
    """Only Git's machine-readable stale-head rejection is retryable."""
    for line in (result.stdout or "").splitlines():
        fields = line.split("\t")
        if (len(fields) == 3 and fields[0] == "!"
                and fields[1].endswith(":refs/heads/main")
                and fields[2] in {"[rejected] (fetch first)", "[rejected] (non-fast-forward)"}):
            return True
    return False


def generate_and_test(worktree: Path, *, runner: Runner = run_command) -> None:
    """Regenerate every dependent shadow output from the same frozen batch."""
    commands = (
        [sys.executable, "decision_hub.py", "--reports-dir", "reports",
         "--refresh-shadow-inputs-only", "--attest-tw-official"],
        [sys.executable, "trade_plan_shadow.py", "--reports-dir", "reports"],
        [sys.executable, "trade_plan_validation.py", "--reports-dir", "reports"],
        [sys.executable, "-m", "pytest", "-q", *FOCUSED_TESTS],
    )
    for command in commands:
        _checked(runner, command, cwd=worktree)


def _allowed(path: str) -> bool:
    return path in REPORT_FILES or CHUNK_PATTERN.fullmatch(path) is not None


def _stage_outputs(worktree: Path, runner: Runner) -> bool:
    # A regression must not silently mutate formal inputs and then publish
    # dependent reports calculated from uncommitted formal state.
    changed = _checked(runner, ["git", "diff", "--name-only", "-z", "HEAD"], cwd=worktree)
    if any(not _allowed(path) for path in changed.split("\0") if path):
        raise PublishError("generation changed tracked files outside the shadow-output allowlist")
    if any(not (worktree / path).is_file() for path in REPORT_FILES):
        raise PublishError("generation did not produce all required shadow reports")
    tracked = _checked(runner, ["git", "ls-files", "-z", "--", "reports"], cwd=worktree)
    paths = {path for path in tracked.split("\0") if _allowed(path)}
    paths.update(REPORT_FILES)
    paths.update(path.relative_to(worktree).as_posix()
                 for path in (worktree / "reports").glob("decision_hub_[0-9][0-9].json")
                 if path.is_file())
    _checked(runner, ["git", "add", "--", *sorted(paths)], cwd=worktree)
    staged = _checked(runner, ["git", "diff", "--cached", "--name-only", "-z"], cwd=worktree)
    staged_paths = [path for path in staged.split("\0") if path]
    if any(not _allowed(path) for path in staged_paths):
        raise PublishError("staged changes exceed the shadow-output allowlist")
    return bool(staged_paths)


def run(repo: Path | str = ".", *, max_attempts: int = MAX_ATTEMPTS,
        runner: Runner = run_command,
        generate: Callable[[Path], None] | None = None) -> dict[str, str | int]:
    """Publish safely or raise, retrying only genuine concurrent advancement.

    ``runner`` and ``generate`` are dependency-injection seams for local tests.
    Production always uses the fixed generators/tests and a normal Git push.
    """
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or not 1 <= max_attempts <= MAX_ATTEMPTS:
        raise ValueError("max_attempts must be between 1 and 3")
    repo = Path(repo).resolve()
    generate = generate or (lambda worktree: generate_and_test(worktree, runner=runner))
    for attempt in range(1, max_attempts + 1):
        base = _fetch_main(repo, runner)
        with tempfile.TemporaryDirectory(prefix="wude-shadow-publish-") as temporary:
            worktree = Path(temporary) / "worktree"
            added = False
            try:
                _checked(runner, ["git", "worktree", "add", "--detach", str(worktree), base], cwd=repo)
                added = True
                generate(worktree)
                if not _stage_outputs(worktree, runner):
                    latest = _fetch_main(repo, runner)
                    if latest == base:
                        return {"status": "unchanged", "attempts": attempt, "base_sha": base}
                    if not _ancestor(repo, base, latest, runner):
                        raise PublishError("main history changed unexpectedly; refusing to retry")
                    # Even a no-op on an obsolete batch must be regenerated.
                    continue
                _checked(runner, ["git", "-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}",
                                  "commit", "-m", "chore: 更新影子交易計畫"], cwd=worktree)
                commit = _checked(runner, ["git", "rev-parse", "HEAD"], cwd=worktree).strip()
                pushed = runner(["git", "push", "origin", "HEAD:refs/heads/main", "--porcelain"], cwd=worktree)
                latest = _fetch_main(repo, runner)
                if _ancestor(repo, commit, latest, runner):
                    # Also handles a lost push response after server acceptance.
                    return {"status": "published", "attempts": attempt,
                            "base_sha": base, "commit_sha": commit, "verified_main_sha": latest}
                if pushed.returncode == 0:
                    raise PublishError("push returned success but its commit is absent from main")
                if latest == base:
                    raise PublishError(f"push failed (exit {pushed.returncode}) without main advancing; refusing retry")
                if not _race_rejection(pushed):
                    raise PublishError("push failed for a non-race reason; refusing retry")
                if not _ancestor(repo, base, latest, runner):
                    raise PublishError("main history changed unexpectedly; refusing to retry")
                # A normal concurrent commit won. Discard this private attempt,
                # fetch again, and regenerate instead of rebasing stale reports.
            finally:
                if added:
                    # Only this script's new temporary worktree is removable.
                    _checked(runner, ["git", "worktree", "remove", "--force", str(worktree)], cwd=repo)
    raise PublishError(f"main kept advancing; no shadow batch published after {max_attempts} attempts")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--max-attempts", type=int, choices=range(1, MAX_ATTEMPTS + 1), default=MAX_ATTEMPTS)
    args = parser.parse_args(argv)
    try:
        result = run(args.repo, max_attempts=args.max_attempts)
    except (PublishError, OSError) as exc:
        print(f"Shadow publication stopped safely: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

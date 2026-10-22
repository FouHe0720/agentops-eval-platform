"""Check publication audit against real temporary Git indexes, never the project index."""

import importlib
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def audit_repository(tmp_path, monkeypatch):
    git(tmp_path, "init", "--quiet")
    monkeypatch.syspath_prepend(str(PROJECT_ROOT))
    audit_module = importlib.import_module("scripts.privacy_check")
    monkeypatch.setattr(audit_module, "ROOT", tmp_path)
    return tmp_path, audit_module


@pytest.mark.parametrize("worktree_state", ["cleaned", "deleted"])
def test_staged_secret_is_rejected_after_worktree_changes(audit_repository, worktree_state):
    repository, audit_module = audit_repository
    path = repository / "settings.txt"
    # Build a long synthetic credential without embedding a matching token in this source.
    secret = "sk-" + "synthetic" * 6
    path.write_text(secret + "\n", encoding="utf-8")
    git(repository, "add", "settings.txt")
    if worktree_state == "cleaned":
        path.write_text("This working-tree copy has been cleaned.\n", encoding="utf-8")
    else:
        path.unlink()

    issues = audit_module.audit()

    assert any("settings.txt" in issue for issue in issues)
    assert secret not in "\n".join(issues)


@pytest.mark.parametrize("filename", [".env.production", "backup.sqlite3"])
def test_forced_tracking_does_not_bypass_prohibited_file_checks(audit_repository, filename):
    repository, audit_module = audit_repository
    (repository / ".gitignore").write_text(".env.*\n*.sqlite3\n", encoding="utf-8")
    (repository / filename).write_text("synthetic review fixture\n", encoding="utf-8")
    git(repository, "add", "--force", filename)

    issues = audit_module.audit()

    assert any(filename in issue for issue in issues)


def test_clean_tracked_and_untracked_files_pass(audit_repository):
    repository, audit_module = audit_repository
    (repository / "README.md").write_text("Synthetic public benchmark.\n", encoding="utf-8")
    git(repository, "add", "README.md")
    (repository / "notes.txt").write_text("Untracked public notes.\n", encoding="utf-8")

    assert audit_module.audit() == []


def test_short_synthetic_credentials_and_known_invalid_url_fixture_pass(audit_repository):
    repository, audit_module = audit_repository
    directory = repository / "tests"
    directory.mkdir()
    invalid_url = "http://" + "user:secret" + "@agent.test"
    fixture = (
        f"SECRET = {'sk-' + 'dummy'!r}\n"
        f"TOKEN = {'ghp_' + 'dummy'!r}\n"
        f"INVALID_URL = {invalid_url!r}\n"
    )
    (directory / "test_adapters.py").write_text(fixture, encoding="utf-8")
    git(repository, "add", "tests/test_adapters.py")

    assert audit_module.audit() == []

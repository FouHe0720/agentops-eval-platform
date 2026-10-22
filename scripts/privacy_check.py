"""Audit both Git index blobs and non-ignored working files before publication.

Heuristic checks never print matched content. Short synthetic invalid credentials
in tests are intentional; this audit is not a universal secret-scanning guarantee.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {".env", ".venv", "runtime", "private", "downloads"}
PATTERNS = [
    re.compile(rb"(?:sk-[A-Za-z0-9_-]{30,}|gh[pousr]_[A-Za-z0-9_]{30,})"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(re.escape(str(ROOT.parent.parent).encode() + b"/")),
    re.compile(rb"https?://[^/\s]+:[^/@\s]+@"),
]


def _forbidden(path: Path) -> bool:
    return (
        bool(FORBIDDEN.intersection(path.parts))
        or (path.name.startswith(".env.") and path.name != ".env.example")
        or any(
            path.name.endswith(suffix)
            for suffix in (
                ".db",
                ".db-wal",
                ".db-shm",
                ".sqlite",
                ".sqlite3",
                ".sqlite-wal",
                ".sqlite-shm",
                ".sqlite3-wal",
                ".sqlite3-shm",
                ".log",
                ".key",
                ".pem",
                ".token",
                ".pt",
                ".safetensors",
            )
        )
    )


def _sensitive(name: str, content: bytes) -> bool:
    if name == "tests/test_adapters.py":
        # Exact synthetic invalid-URL fixture, rejected by the adapter test.
        content = content.replace(b"http://" + b"user:secret@agent.test", b"[INVALID_URL_FIXTURE]")
    return any(pattern.search(content) for pattern in PATTERNS)


def audit() -> list[str]:
    issues = []
    indexed = subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=ROOT)
    for entry in indexed.decode().split("\0"):
        if not entry:
            continue
        info, name = entry.split("\t", 1)
        mode, oid, stage = info.split()
        if stage != "0" or mode not in {"100644", "100755"}:
            issues.append(f"unmerged or non-regular index entry: {name}")
        if _forbidden(Path(name)):
            issues.append(f"forbidden indexed file: {name}")
        content = subprocess.check_output(["git", "cat-file", "blob", oid], cwd=ROOT)
        if _sensitive(name, content):
            issues.append(f"sensitive index pattern: {name} (content withheld)")
    paths = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
        )
        .decode()
        .split("\0")
    )
    for name in sorted(set(paths) - {""}):
        path = Path(name)
        if _forbidden(path):
            issues.append(f"forbidden working file: {name}")
            continue
        full_path = ROOT / path
        if full_path.is_symlink():
            issues.append(f"symlink requires manual review: {name}")
        elif full_path.is_file() and _sensitive(name, full_path.read_bytes()):
            issues.append(f"sensitive working pattern: {name} (content withheld)")
    return sorted(set(issues))


def main() -> None:
    issues = audit()
    if issues:
        print("\n".join(issues))
        raise SystemExit(1)
    print("Privacy audit passed: index and working files contain no prohibited patterns.")


if __name__ == "__main__":
    main()

"""Open a "bump: version X.Y.Z" PR for the next release (`make release`).

commitizen works out the version from the Conventional Commits since the last tag and updates pyproject.toml, uv.lock
and CHANGELOG.md; merging the PR publishes it (.github/workflows/release.yml). main only takes PRs, so nothing is
tagged here. A script rather than make recipe lines, so it runs the same from cmd, PowerShell or a POSIX shell.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUMPED = ["pyproject.toml", "uv.lock", "CHANGELOG.md"]


def run(*args: str, capture: bool = False) -> str:
    print("$", " ".join(args), flush=True)
    exe = shutil.which(args[0]) or args[0]  # resolves git.exe, uvx.exe, gh.exe on Windows
    done = subprocess.run([exe, *args[1:]], cwd=ROOT, check=True, text=True, capture_output=capture)  # noqa: S603
    return done.stdout if capture else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="bump and commit on a local branch; no push, no PR")
    dry_run = parser.parse_args().dry_run

    if run("git", "status", "--porcelain", capture=True).strip():
        print("make release needs a clean working tree", file=sys.stderr)
        return 1
    run("git", "switch", "main")
    run("git", "pull", "--ff-only")
    run("uvx", "--from", "commitizen", "cz", "bump", "--files-only", "--changelog", "--yes")
    # read after the bump, from the file it just wrote
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    branch, title = f"release/v{version}", f"bump: version {version}"
    run("git", "switch", "-c", branch)
    run("git", "add", *BUMPED)
    run("git", "commit", "-m", title)
    if dry_run:
        print(f"\nDry run: {branch} is committed locally. Look at it, then: git switch main && git branch -D {branch}")
        return 0
    run("git", "push", "-u", "origin", branch)
    body = f"Release v{version}. Merging publishes it to PyPI and creates the tag and GitHub Release."
    run("gh", "pr", "create", "--base", "main", "--title", title, "--body", body)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as exc:  # the command's own output already says why
        print(f"release stopped: {' '.join(map(str, exc.cmd))} failed (exit {exc.returncode})", file=sys.stderr)
        sys.exit(1)

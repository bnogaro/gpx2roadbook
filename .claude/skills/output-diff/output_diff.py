"""Render the samples under the settings matrix at a git ref and in the working tree, and diff the HTML.

    uv run --no-sync python .claude/skills/output-diff/output_diff.py [REF] [--keep DIR]

REF defaults to main. Both sides run on the working tree's environment and samples; only the `roadbook` package
differs: REF's is checked out in a temporary worktree and put first on PYTHONPATH. Rendering goes through
`api.preview`, offline, so both sides read the same caches. Exit 0 if every file is identical, 1 if any differs, 2 if a
render failed.
"""

from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GIT = shutil.which("git") or "git"
SAMPLES = ("entrainement_ubf", "paris_le_mans", "ubf_300_poi")

# Each one a change to the default settings (the dict load_config gives, as the GUI and api.preview take it).
MATRIX = """
def hours(c):
    c["hours"]["enabled"] = True
    c["ride"].update(date="2026-10-11", start="07:30", speed_kmh=24, breaks=[[95, 45]])

CONFIGS = {
    "defaults": lambda c: None,
    "a6": lambda c: c["booklet"].update(format="A6"),
    "off": lambda c: c["booklet"].update(format="off"),
    "off_line": lambda c: (c["booklet"].update(format="off"), c["render"].update(layout="line")),
    "hours": hours,
    "climb_pages": lambda c: c["climb_pages"].update(enabled=True),
    "topo": lambda c: c["map"].update(style="topo", contours=False),
    "cp50": lambda c: c["checkpoints"].update(every_km=50),
    "pois": lambda c: c["pois"].update(enabled=["water", "bakery"]),
    # in French, with hours and climb pages for the most words; a ref without render.language renders it in English
    "fr": lambda c: (hours(c), c["climb_pages"].update(enabled=True), c["render"].update(language="fr")),
}
"""

RENDER = (
    MATRIX
    + """
import sys
from pathlib import Path

from roadbook.api import preview
from roadbook.config import load_config

samples, out = Path(sys.argv[1]), Path(sys.argv[2])
for gpx in sys.argv[3:]:
    for name, change in CONFIGS.items():
        cfg = load_config()
        change(cfg)
        shot = preview(samples / f"{gpx}.gpx", cfg)
        (out / f"{gpx}.{name}.html").write_text(shot.html, encoding="utf-8")
        if shot.booklet is not None:
            (out / f"{gpx}.{name}.booklet.html").write_text(shot.booklet, encoding="utf-8")
"""
)


def render(src: Path, out: Path) -> subprocess.Popen[bytes]:
    """Start rendering the matrix with the `roadbook` package under `src` into `out`."""
    out.mkdir(parents=True)
    script = out.parent / "render.py"
    script.write_text(RENDER, encoding="utf-8")
    env = os.environ | {"PYTHONPATH": str(src)}
    command = [sys.executable, str(script), str(ROOT / "samples"), str(out), *SAMPLES]
    return subprocess.Popen(command, env=env, cwd=ROOT)  # noqa: S603  this interpreter, the skill's own script


def first_difference(a: Path, b: Path) -> str:
    """The first lines that differ, with a little context."""
    lines = difflib.unified_diff(
        a.read_text(encoding="utf-8").splitlines(),
        b.read_text(encoding="utf-8").splitlines(),
        str(a.name),
        str(b.name),
        n=1,
        lineterm="",
    )
    return "\n".join(line[:200] for _, line in zip(range(12), lines, strict=False))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]  # a diff's "≥" on a cp1252 console
    args = sys.argv[1:]
    keep = None
    if "--keep" in args:
        i = args.index("--keep")
        keep = Path(args[i + 1])
        del args[i : i + 2]
    ref = args[0] if args else "main"
    tmp = Path(tempfile.mkdtemp(prefix="output-diff-"))
    tree = tmp / "ref"
    try:
        subprocess.run([GIT, "worktree", "add", "--detach", "--quiet", str(tree), ref], cwd=ROOT, check=True)  # noqa: S603
        print(f"rendering {ref} and the working tree side by side ...", flush=True)
        sides = [render(tree / "src", tmp / "base" / "html"), render(ROOT / "src", tmp / "head" / "html")]
        if any(side.wait() for side in sides):
            print("a render failed", file=sys.stderr)
            return 2
        base, head = tmp / "base" / "html", tmp / "head" / "html"
        names = sorted({p.name for p in base.iterdir()} | {p.name for p in head.iterdir()})
        changed = []
        for name in names:
            a, b = base / name, head / name
            if not (a.exists() and b.exists()):
                changed.append(name)
                print(f"\n{name}: only in {'the working tree' if b.exists() else ref}")
            elif a.read_bytes() != b.read_bytes():
                changed.append(name)
                print(f"\n{name} differs:\n{first_difference(a, b)}")
        print(f"\n{len(names) - len(changed)}/{len(names)} identical to {ref}")
        if keep:
            shutil.copytree(tmp / "base" / "html", keep / "base", dirs_exist_ok=True)
            shutil.copytree(tmp / "head" / "html", keep / "head", dirs_exist_ok=True)
            print(f"kept in {keep}")
        return 1 if changed else 0
    finally:
        subprocess.run([GIT, "worktree", "remove", "--force", str(tree)], cwd=ROOT, check=False)  # noqa: S603
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

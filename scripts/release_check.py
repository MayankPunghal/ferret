"""Release decision for .github/workflows/release.yml (run with: uv run --no-project python scripts/release_check.py).

The version in pyproject.toml is the source of truth: a release happens when it names a version that has no
v<version> tag yet. `--version X` (and `--crawler Y`) first write the new version(s) into the files, which is
what the "Run workflow" button does. Refuses a release whose crawler pin is wrong, whose crawler code changed
since the last release without a new crawler version, or whose version is not newer than the last release.
Writes version, release (true/false) to $GITHUB_OUTPUT when set.
"""

import argparse
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP, CRAWLER = ROOT / "pyproject.toml", ROOT / "site2md" / "pyproject.toml"


def version_of(path: Path) -> str:
    return tomllib.loads(path.read_text(encoding="utf-8"))["project"]["version"]


def parts(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def set_version(path: Path, v: str) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(re.sub(r'(?m)^version = ".*"$', f'version = "{v}"', text, count=1), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="", help="new ferret-search version to write first")
    ap.add_argument("--crawler", default="", help="new ferret-crawler version to write first")
    ap.add_argument("--next", action="store_true",
                    help="Run workflow: with no --version, the next patch version when the current one is released; "
                         "with no --crawler, the next crawler patch version when the crawler changed")
    a = ap.parse_args()
    tags = git("tag", "-l", "v*", "--sort=-v:refname").split()
    if a.next and tags:
        bump = lambda v: ".".join(map(str, (*parts(v)[:2], parts(v)[2] + 1)))  # noqa: E731
        if not a.version and f"v{version_of(APP)}" in tags:
            a.version = bump(tags[0])
        old = tomllib.loads(git("show", f"{tags[0]}:site2md/pyproject.toml"))["project"]["version"]
        if not a.crawler and old == version_of(CRAWLER) and git(
                "diff", "--name-only", tags[0], "HEAD", "--", "site2md/src", "site2md/pyproject.toml"):
            a.crawler = bump(old)
    if a.version:
        set_version(APP, a.version)
    if a.crawler:
        set_version(CRAWLER, a.crawler)
        APP.write_text(re.sub(r'"ferret-crawler==[^"]*"', f'"ferret-crawler=={a.crawler}"',
                              APP.read_text(encoding="utf-8")), encoding="utf-8")

    version, crawler = version_of(APP), version_of(CRAWLER)
    tags = git("tag", "-l", "v*", "--sort=-v:refname").split()
    release = f"v{version}" not in tags
    errors = []
    if release:
        pin = re.search(r'"ferret-crawler==([^"]*)"', APP.read_text(encoding="utf-8"))
        if not pin or pin.group(1) != crawler:
            errors.append(f"pyproject.toml pins ferret-crawler=={pin and pin.group(1)}, but the crawler is {crawler}")
        if tags:
            last = tags[0]
            if parts(version) <= parts(last):
                errors.append(f"version {version} is not newer than the last release {last}")
            crawler_changed = git("diff", "--name-only", last, "HEAD", "--", "site2md/src", "site2md/pyproject.toml")
            old = tomllib.loads(git("show", f"{last}:site2md/pyproject.toml"))["project"]["version"]
            if crawler_changed and old == crawler:
                errors.append(f"the crawler changed since {last} but is still version {crawler}: give it a new version "
                              "(the crawler version box of Run workflow), or PyPI keeps the old one")
    for e in errors:
        print(f"::error::{e}")
    print(f"ferret-search {version}, ferret-crawler {crawler}: "
          + ("release" if release and not errors else "already released, nothing to publish" if not release else "refused"))
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"version={version}\nrelease={str(release).lower()}\n")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()

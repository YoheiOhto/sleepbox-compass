#!/usr/bin/env python3
"""Check whether upstream nerolis-lab has game-data updates under common/."""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Callable

IGNORED_COMMON_FILES = {"common/package.json", "common/package-lock.json"}
IGNORED_SUBJECT_PREFIXES = ("chore(release):", "build(deps):", "build(deps-dev):")

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def default_runner(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def parse_game_data_commits(log_output: str) -> list[dict]:
    """Parse git log --format=COMMIT\t%h\t%ad\t%s --name-only output for non-dependency common/ changes."""
    commits: list[dict] = []
    current: dict | None = None
    for raw in log_output.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("COMMIT\t"):
            if current is not None:
                commits.append(current)
            _, sha, date, subject = line.split("\t", 3)
            current = {"sha": sha, "date": date, "subject": subject, "files": []}
        elif current is not None:
            current["files"].append(line)
    if current is not None:
        commits.append(current)

    relevant = []
    for item in commits:
        if any(item["subject"].startswith(prefix) for prefix in IGNORED_SUBJECT_PREFIXES):
            continue
        data_files = [f for f in item["files"] if f.startswith("common/") and f not in IGNORED_COMMON_FILES]
        if data_files:
            relevant.append({**item, "files": data_files})
    return relevant


def check_updates(vendor_dir: Path, *, fetch: bool = False, base_ref: str = "HEAD", target_ref: str = "origin/main",
                  run: Runner = default_runner) -> list[dict]:
    if fetch:
        proc = run(["git", "-C", str(vendor_dir), "fetch", "origin"])
        if proc.returncode != 0:
            raise RuntimeError(f"git fetch failed: {proc.stderr.strip()}")
    proc = run([
        "git", "-C", str(vendor_dir), "log", "--no-merges", "--date=short",
        "--format=COMMIT\t%h\t%ad\t%s", "--name-only",
        f"{base_ref}..{target_ref}", "--", "common/",
    ])
    if proc.returncode != 0:
        raise RuntimeError(f"git log failed: {proc.stderr.strip()}")
    return parse_game_data_commits(proc.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check nerolis-lab for game-data updates under common/.")
    parser.add_argument("--vendor-dir", type=Path, default=Path("engine/vendor/nerolis-lab"))
    parser.add_argument("--fetch", action="store_true", help="Run git fetch origin before checking")
    args = parser.parse_args(argv)

    updates = check_updates(args.vendor_dir, fetch=args.fetch)
    if not updates:
        print("No game-data updates under common/ in nerolis-lab.")
        return 0
    print(f"Found {len(updates)} game-data update(s) in nerolis-lab:")
    for item in updates:
        print(f"- {item['date']} {item['sha']} {item['subject']} ({len(item['files'])} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

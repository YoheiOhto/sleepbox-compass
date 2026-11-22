from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from tools.check_nerolis_updates import check_updates, parse_game_data_commits


class CheckNerolisUpdatesTests(unittest.TestCase):
    def test_filters_out_dependabot_and_release_commits(self):
        sample = "\n".join([
            "COMMIT\taaa1111\t2026-11-16\tchore(release): 2.74.1 [skip ci]",
            "common/package.json",
            "",
            "COMMIT\tbbb2222\t2026-11-15\tbuild(deps-dev): bump vite in /common",
            "common/package.json",
            "common/package-lock.json",
            "",
            "COMMIT\tccc3333\t2026-11-15\tfeat: add cottonee and whimsicott",
            "common/src/domain/pokemon/berry-pokemon.ts",
            "common/src/domain/pokemon/index.ts",
        ])
        updates = parse_game_data_commits(sample)
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["sha"], "ccc3333")
        self.assertEqual(len(updates[0]["files"]), 2)

    def test_check_updates_runs_git_log(self):
        def fake_run(cmd: list[str]) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        self.assertEqual(check_updates(Path("engine/vendor/nerolis-lab"), run=fake_run), [])


if __name__ == "__main__":
    unittest.main()

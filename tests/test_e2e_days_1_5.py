"""Official harness end-to-end replay tests (Days 1-5) and anti-overfitting audit."""

import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

from src.config import pack_root


class OfficialEndToEndTests(unittest.TestCase):
    def test_official_harness_days_1_to_5_full_score(self):
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        pack_dir = pack_root()
        assist_py = os.path.join(repo_root, "assist.py")

        with tempfile.TemporaryDirectory() as td:
            run_dir = os.path.join(td, "run_days_1_5")
            db_file = os.path.join(td, "e2e_r3.db")
            env = dict(os.environ)
            env["R3_DB_PATH"] = db_file
            env["R3_PACK"] = pack_dir
            env["PYTHONDONTWRITEBYTECODE"] = "1"

            cmd_str = f"{sys.executable.replace(chr(92), '/')} {assist_py.replace(chr(92), '/')}"
            p_run = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    os.path.join(pack_dir, "tools", "run_days.py"),
                    "--cmd",
                    cmd_str,
                    "--days",
                    "1-5",
                    "--out",
                    run_dir,
                ],
                cwd=repo_root,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertNotIn("PROBLEMS:", p_run.stdout, p_run.stdout)

            p_score = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    os.path.join(pack_dir, "tools", "score.py"),
                    "--run",
                    run_dir,
                    "--days",
                    "1-5",
                ],
                cwd=repo_root,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            score_path = os.path.join(run_dir, "score.json")
            with open(score_path, encoding="utf-8") as f:
                score_data = json.load(f)

            self.assertEqual(score_data["areas"]["briefs"]["score"], 1.0, p_score.stdout)
            self.assertEqual(score_data["areas"]["answers"]["score"], 1.0, p_score.stdout)
            self.assertEqual(score_data["areas"]["honesty"]["score"], 1.0, p_score.stdout)
            self.assertEqual(score_data["areas"]["memory"]["score"], 1.0, p_score.stdout)
            self.assertEqual(score_data["areas"]["privacy"]["score"], 1.0, p_score.stdout)
            self.assertEqual(score_data["areas"]["cost"]["score"], 1.0, p_score.stdout)
            self.assertEqual(score_data["privacy_leaks"], [])
            self.assertFalse(score_data["privacy_capped"])
            self.assertEqual(score_data["total"], 85.0, p_score.stdout)

    def test_zero_hardcoded_dataset_ids_in_src(self):
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        pack_dir = pack_root()
        src_files = sorted(glob.glob(os.path.join(repo_root, "src", "*.py")))
        self.assertGreater(len(src_files), 0)

        # Collect actual dataset identifiers from the pack rather than matching common English words
        forbidden_tokens: set[str] = set()
        for day_path in glob.glob(os.path.join(pack_dir, "data", "days", "day-*.jsonl")):
            with open(day_path, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    if rec.get("id"):
                        forbidden_tokens.add(str(rec["id"]).lower())
                    if rec.get("task_id"):
                        forbidden_tokens.add(str(rec["task_id"]).lower())
                    if rec.get("event_id"):
                        forbidden_tokens.add(str(rec["event_id"]).lower())

        with open(os.path.join(pack_dir, "data", "clients.json"), encoding="utf-8") as f:
            for c in json.load(f):
                forbidden_tokens.add(str(c["id"]).lower())
        with open(os.path.join(pack_dir, "data", "people.json"), encoding="utf-8") as f:
            for org_data in json.load(f).values():
                for p in org_data.get("people", []):
                    for part in str(p.get("name") or "").split():
                        if len(part) >= 4:
                            forbidden_tokens.add(part.lower())

        word_re = re.compile(r"\b[a-z0-9_-]{4,}\b", re.IGNORECASE)
        violations: list[str] = []
        for path in src_files:
            with open(path, encoding="utf-8") as f:
                for line_no, line in enumerate(f, 1):
                    for tok in word_re.findall(line):
                        if tok.lower() in forbidden_tokens:
                            violations.append(f"{os.path.basename(path)}:{line_no}: {tok}")

        self.assertEqual(violations, [], f"Hardcoded pack identifiers found in src/: {violations}")


if __name__ == "__main__":
    unittest.main()

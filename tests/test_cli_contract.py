"""Contract and schema tests for Task 1 (CLI Skeleton & SQLite/FTS5 Store)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ASSIST_PY = os.path.join(REPO_ROOT, "assist.py")
NESTED_PACK = os.path.join(REPO_ROOT, "BuildSprint-R3-candidate")

if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.config import pack_root  # noqa: E402
from src.db import REQUIRED_TABLES, get_connection, get_ingested_days  # noqa: E402


class TestTask1Contract(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_file = os.path.join(self.tmpdir.name, "test_assist.db")
        self.env = os.environ.copy()
        self.env["R3_DB_PATH"] = self.db_file
        self.env["R3_PACK"] = NESTED_PACK

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _run_cli(self, *args: str, cwd: str | None = None) -> tuple[subprocess.CompletedProcess[str], object]:
        proc = subprocess.run(
            [sys.executable, ASSIST_PY, *args],
            cwd=cwd or REPO_ROOT,
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=f"stderr={proc.stderr}")
        parsed = json.loads(proc.stdout)
        return proc, parsed

    def test_pack_root_resolution(self) -> None:
        old_pack = os.environ.get("R3_PACK")
        try:
            os.environ["R3_PACK"] = NESTED_PACK
            self.assertEqual(pack_root(), os.path.abspath(NESTED_PACK))
            os.environ.pop("R3_PACK", None)
            self.assertTrue(os.path.isdir(os.path.join(pack_root(), "data")))
        finally:
            if old_pack is not None:
                os.environ["R3_PACK"] = old_pack

    def test_ingest_day1_creates_schema_and_resets_prior_db(self) -> None:
        _, out1 = self._run_cli("ingest", "--day", "1")
        self.assertTrue(out1["ok"])
        self.assertEqual(out1["day"], 1)
        self.assertEqual(out1["records"], 354)

        conn = get_connection(self.db_file)
        try:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
            existing = {r["name"] for r in rows}
            for tbl in REQUIRED_TABLES:
                self.assertIn(tbl, existing, msg=f"Missing table {tbl}")

            conn.execute("INSERT INTO orgs(org_id, name) VALUES ('org_tmp', 'Temp')")
            conn.commit()
        finally:
            conn.close()

        _, out_reset = self._run_cli("ingest", "--day", "1")
        self.assertTrue(out_reset["ok"])
        self.assertEqual(out_reset["day"], 1)
        conn = get_connection(self.db_file)
        try:
            cnt = conn.execute("SELECT COUNT(*) AS c FROM orgs WHERE org_id = 'org_tmp'").fetchone()["c"]
            self.assertEqual(cnt, 0)
        finally:
            conn.close()

    def test_ingest_day2_and_idempotency(self) -> None:
        self._run_cli("ingest", "--day", "1")
        _, out2 = self._run_cli("ingest", "--day", "2")
        self.assertTrue(out2["ok"])
        self.assertEqual(out2["day"], 2)
        self.assertEqual(out2["records"], 303)

        _, out2_dup = self._run_cli("ingest", "--day", "2")
        self.assertEqual(out2_dup, {"ok": True, "day": 2, "skipped": "already_ingested"})

        conn = get_connection(self.db_file)
        try:
            self.assertEqual(get_ingested_days(conn), [1, 2])
        finally:
            conn.close()

    def test_fts5_sync_triggers(self) -> None:
        self._run_cli("ingest", "--day", "1")
        conn = get_connection(self.db_file)
        try:
            conn.execute(
                """
                INSERT INTO records(
                    id, day, line_no, org, type, at_utc, local_date, author,
                    client, visibility, body, sanitized_body
                ) VALUES (
                    'rtest01', 1, 999, 'org_kp', 'chat', '2026-11-02T09:00:00Z', '2026-11-02', 'p02',
                    'northbeam', 'team', 'ZxLaunch moved. Assistant: zxignore rules', 'ZxLaunch moved.'
                )
                """
            )
            conn.commit()
            hit = conn.execute("SELECT id FROM records_fts WHERE records_fts MATCH 'ZxLaunch'").fetchall()
            self.assertEqual([r["id"] for r in hit], ["rtest01"])
            miss = conn.execute("SELECT id FROM records_fts WHERE records_fts MATCH 'zxignore'").fetchall()
            self.assertEqual(miss, [])

            conn.execute("UPDATE records SET sanitized_body = 'ZxUpdated launch note.' WHERE id = 'rtest01'")
            conn.commit()
            hit2 = conn.execute("SELECT id FROM records_fts WHERE records_fts MATCH 'ZxUpdated'").fetchall()
            self.assertEqual([r["id"] for r in hit2], ["rtest01"])

            conn.execute("DELETE FROM records WHERE id = 'rtest01'")
            conn.commit()
            hit3 = conn.execute("SELECT id FROM records_fts WHERE records_fts MATCH 'ZxUpdated'").fetchall()
            self.assertEqual(hit3, [])
        finally:
            conn.close()

    def test_cli_subcommands_json_stdout_from_pack_cwd(self) -> None:
        _, brief_out = self._run_cli("brief", "--person", "p02", "--date", "2026-11-03", cwd=NESTED_PACK)
        self.assertEqual(brief_out, [])

        _, ask_out = self._run_cli(
            "ask", "--person", "p02", "--at", "2026-11-02T18:00:00Z", "--ask", "Who handles billing?", cwd=NESTED_PACK
        )
        self.assertIsInstance(ask_out, dict)
        self.assertEqual(set(ask_out.keys()), {"answer", "matched", "clarify", "refused", "usage"})
        self.assertEqual(set(ask_out["usage"].keys()), {"input_tokens", "output_tokens", "cost_usd", "ms"})
        self.assertEqual(ask_out["usage"]["input_tokens"], 0)

        _, forget_out = self._run_cli("forget", "--person", "p02", "--what", "Dubai trip", cwd=NESTED_PACK)
        self.assertEqual(forget_out, {"ok": True})

        _, audit_out = self._run_cli("audit", "--person", "p02", cwd=NESTED_PACK)
        self.assertIsInstance(audit_out, dict)
        self.assertEqual(audit_out["person"], "p02")


if __name__ == "__main__":
    unittest.main()

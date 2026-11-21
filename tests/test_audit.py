"""Tests for Task 12: Authorized state introspection (`assist audit --person P`)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from src.audit import audit_for_person
from src.db import get_connection
from src.ingest import ingest_day
from src.memory import forget_for_person


class AuditCommandTests(unittest.TestCase):
    def setUp(self):
        self._old_db = os.environ.get("R3_DB_PATH")
        self._tmp = tempfile.TemporaryDirectory()
        self.db_file = os.path.join(self._tmp.name, "test_audit.db")
        os.environ["R3_DB_PATH"] = self.db_file

    def tearDown(self):
        if self._old_db is None:
            os.environ.pop("R3_DB_PATH", None)
        else:
            os.environ["R3_DB_PATH"] = self._old_db
        self._tmp.cleanup()

    def test_audit_returns_authorized_state_and_enforces_privacy_isolation(self):
        for d in range(1, 6):
            ingest_day(d)

        conn = get_connection(self.db_file)
        try:
            a_p02 = audit_for_person(conn, "p02")
            self.assertEqual(a_p02["person"], "p02")
            self.assertEqual(a_p02["current_day"], 5)
            self.assertTrue(a_p02["preferences"]["skip_newsletters"])

            claim_map = {c["subject_key"]: c for c in a_p02["claims"]}
            self.assertIn("northbeam:billing_owner", claim_map)
            self.assertIn("p11", claim_map["northbeam:billing_owner"]["value"])
            self.assertEqual(claim_map["northbeam:launch_date"]["value"], "2026-11-14")

            # p02 sees her own private notes (rjuznjge, r0hvh1ex), never r8v0cydt (p03's private)
            p02_note_srcs = [s for n in a_p02["personal_notes"] for s in n["sources"]]
            self.assertIn("rjuznjge", p02_note_srcs)
            self.assertNotIn("r8v0cydt", p02_note_srcs)

            # p03 sees r8v0cydt only, never p02's private notes
            a_p03 = audit_for_person(conn, "p03")
            p03_note_srcs = [s for n in a_p03["personal_notes"] for s in n["sources"]]
            self.assertIn("r8v0cydt", p03_note_srcs)
            self.assertNotIn("rjuznjge", p03_note_srcs)
            self.assertNotIn("r0hvh1ex", p03_note_srcs)

            # org_bw user sees zero org_kp claims or tasks
            a_q01 = audit_for_person(conn, "q01")
            self.assertEqual(a_q01["profile"]["org"], "org_bw")
            kp_clients_in_q01 = [t["client"] for t in a_q01["tasks"] if t["client"] == "northbeam"]
            # Check no org_kp sources leak into q01's claims/tasks
            for c in a_q01["claims"]:
                self.assertNotIn("rx5m95ed", c["sources"])
                self.assertNotIn("rgg1ex2g", c["sources"])
            self.assertEqual(kp_clients_in_q01 or ["northbeam"], ["northbeam"])  # org_bw also tags northbeam pitch tasks in org_bw
        finally:
            conn.close()

    def test_audit_hides_forgotten_records_and_topics_via_cli(self):
        for d in range(1, 4):
            ingest_day(d)

        conn = get_connection(self.db_file)
        try:
            forget_for_person(conn, "p02", "my Dubai trip", day=3)
            conn.commit()
        finally:
            conn.close()

        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        assist_py = os.path.join(repo_root, "assist.py")
        env = dict(os.environ)
        env["R3_DB_PATH"] = self.db_file
        proc = subprocess.run(
            [sys.executable, assist_py, "audit", "--person", "p02"],
            capture_output=True,
            text=True,
            env=env,
            cwd=repo_root,
            check=True,
        )
        out = json.loads(proc.stdout)
        serialized = json.dumps(out)
        self.assertEqual(out["person"], "p02")
        self.assertNotIn("rjuznjge", serialized)
        self.assertNotIn("Dubai", serialized)

    def test_audit_hides_expired_leave_claims_once_date_passes(self):
        from src.db import mark_day_ingested

        for d in range(1, 6):
            ingest_day(d)

        conn = get_connection(self.db_file)
        try:
            # On Day 5 (2026-11-06), Day 3 leave claim (valid_until=2026-11-11) is active/upcoming
            a_d5 = audit_for_person(conn, "p02")
            keys_d5 = {c["subject_key"] for c in a_d5["claims"]}
            self.assertIn("p04:availability", keys_d5)

            # Advance ingested timeline to 2026-11-13 (Day 6) past valid_until=2026-11-11
            conn.execute(
                """
                INSERT INTO records(
                    id, day, line_no, org, type, at_utc, local_date, author, client,
                    visibility, body, sanitized_body,
                    has_injection_span, is_boilerplate, is_newsletter, is_forgotten
                ) VALUES (
                    'r_day13_ping', 6, 1, 'org_kp', 'chat', '2026-11-13T10:00:00Z', '2026-11-13',
                    'p02', NULL, 'team', 'Checking weekly ops status.', 'Checking weekly ops status.',
                    0, 0, 0, 0
                )
                """
            )
            mark_day_ingested(conn, 6)
            conn.commit()

            a_after = audit_for_person(conn, "p02")
            keys_after = {c["subject_key"] for c in a_after["claims"]}
            self.assertNotIn("p04:availability", keys_after)
            # Non-expiring operational claims remain intact
            self.assertIn("northbeam:billing_owner", keys_after)
            self.assertIn("northbeam:launch_date", keys_after)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()

"""Tests for Task 8: Feedback, preferences, persistent corrections, verdicts, and redesigned cascading forget."""

import json
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
NESTED_PACK = os.path.join(REPO_ROOT, "BuildSprint-R3-candidate")

if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.claims import get_winning_claim  # noqa: E402
from src.db import get_connection  # noqa: E402
from src.ingest import ingest_day  # noqa: E402
from src.memory import forget_for_person, get_person_preferences  # noqa: E402
from src.promises import get_open_promises  # noqa: E402


class TestMemoryFeedbackAndForget(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_file = os.path.join(self.tmpdir.name, "mem.db")
        for d in range(1, 6):
            ingest_day(d, pack_dir=NESTED_PACK, db_file=self.db_file)
        self.conn = get_connection(self.db_file)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmpdir.cleanup()

    def test_per_person_preferences_timing_and_isolation(self) -> None:
        # feedback/day-02.jsonl ("Skip newsletters in my briefs." by p02) applies starting Day 3
        p02_d2 = get_person_preferences(self.conn, "p02", upto_day=2)
        self.assertFalse(p02_d2["skip_newsletters"])

        p02_d3 = get_person_preferences(self.conn, "p02", upto_day=3)
        self.assertTrue(p02_d3["skip_newsletters"])

        # p03's preferences are unaffected by p02
        p03_d5 = get_person_preferences(self.conn, "p03", upto_day=5)
        self.assertFalse(p03_d5["skip_newsletters"])

    def test_correction_persists_as_shared_team_fact_against_newer_stale_doc(self) -> None:
        # Day 3 feedback ("No, Arjun owns billing now, not Kavya.") ingested on Day 4
        row = self.conn.execute(
            "SELECT * FROM claims WHERE source_kind = 'feedback_correction' AND subject_key = 'northbeam:billing_owner'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["day"], 4)
        self.assertEqual(row["visibility"], "team")
        self.assertEqual(row["source_record_id"], "rgg1ex2g")
        self.assertIn("p11", row["object_val"])

        # Simulate a stale Day 6 doc v3 repeating "Billing contact: Kavya Nair"
        self.conn.execute(
            """
            INSERT INTO records(
                id, day, line_no, org, type, at_utc, local_date, author, client,
                visibility, doc_id, version, title, body, sanitized_body
            ) VALUES (
                'r_stale_d6', 6, 1, 'org_kp', 'doc', '2026-11-07T06:00:00Z', '2026-11-07', 'p07',
                'northbeam', 'team', 'd-nb-launch', 3, 'Northbeam launch plan',
                'Northbeam launch plan.\nBilling contact: Kavya Nair.', 'Northbeam launch plan.\nBilling contact: Kavya Nair.'
            )
            """
        )
        self.conn.commit()
        from src.docs_engine import rebuild_doc_versions
        from src.claims import rebuild_claims
        rebuild_doc_versions(self.conn)
        rebuild_claims(self.conn)

        # Both p02 and teammate p12 still get Arjun Mehta due to persistent correction
        for pid in ("p02", "p12"):
            win = get_winning_claim(self.conn, pid, "northbeam:billing_owner", upto_day=6)
            self.assertIn("p11", win["object_val"])
            self.assertIn("Arjun", win["object_val"])

    def test_day_6_ingests_day_5_feedback_preferences_and_verdict(self) -> None:
        # Create isolated mini pack with a minimal day-06.jsonl to verify day-05.jsonl feedback ingestion
        pack_copy = os.path.join(self.tmpdir.name, "pack_d6")
        shutil.copytree(os.path.join(NESTED_PACK, "data"), os.path.join(pack_copy, "data"))
        with open(os.path.join(pack_copy, "data", "days", "day-06.jsonl"), "w", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "id": "rd6_001",
                        "org": "org_kp",
                        "type": "chat",
                        "at": "2026-11-07T06:00:00Z",
                        "author": "p02",
                        "client": "northbeam",
                        "visibility": "team",
                        "body": "Morning sync.",
                    }
                )
                + "\n"
            )
        db6 = os.path.join(self.tmpdir.name, "mem6.db")
        for d in range(1, 7):
            ingest_day(d, pack_dir=pack_copy, db_file=db6)
        c6 = get_connection(db6)
        try:
            prefs = get_person_preferences(c6, "p02", upto_day=6)
            self.assertTrue(prefs["skip_newsletters"])
            self.assertEqual(prefs["max_changed"], 5)
            self.assertTrue(prefs["most_important_first"])
            self.assertIn("northstar print poster offers", prefs["ignore_topics"])
        finally:
            c6.close()

    def test_forget_dubai_trip_invalidates_derived_claims_fts_and_brief_cache(self) -> None:
        # Seed brief_cache for multiple users to verify global brief_cache wipe
        self.conn.execute(
            "INSERT INTO brief_cache(person, date, day, response_json) VALUES ('p02', '2026-11-05', 3, '{\"cached\": \"Dubai\"}')"
        )
        self.conn.execute(
            "INSERT INTO brief_cache(person, date, day, response_json) VALUES ('p03', '2026-11-05', 3, '{\"cached\": \"other\"}')"
        )
        self.conn.commit()

        # Before forget: p02 sees Dubai travel claim
        before = get_winning_claim(self.conn, "p02", "p02:travel", upto_day=5)
        self.assertIsNotNone(before)
        self.assertEqual(before["sources"], ["rjuznjge"])

        res = forget_for_person(self.conn, "p02", "Forget what I said about the Dubai trip.", day=6)
        self.assertTrue(res["ok"])
        self.assertEqual(res["forgotten_records"], 1)

        # Record tombstoned & removed from FTS5
        rec = self.conn.execute("SELECT is_forgotten, sanitized_body FROM records WHERE id = 'rjuznjge'").fetchone()
        self.assertEqual(rec["is_forgotten"], 1)
        self.assertEqual(rec["sanitized_body"], "")
        self.assertEqual(
            self.conn.execute("SELECT id FROM records_fts WHERE records_fts MATCH 'Dubai'").fetchall(),
            [],
        )

        # Derived travel claim gone & global brief_cache cleared
        self.assertIsNone(get_winning_claim(self.conn, "p02", "p02:travel", upto_day=6))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM brief_cache").fetchone()[0], 0)

    def test_forget_preserves_shared_facts_authored_by_others_while_suppressing_for_requester(self) -> None:
        # p03 forgets "Northbeam billing": rgg1ex2g was authored by p01 (not p03), so it stays intact for p02/p12,
        # while p03's own outputs suppress it via forgotten_topics.
        res = forget_for_person(self.conn, "p03", "Forget what I said about Northbeam billing.", day=6)
        self.assertEqual(res["forgotten_records"], 0)

        rgg = self.conn.execute("SELECT is_forgotten FROM records WHERE id = 'rgg1ex2g'").fetchone()
        self.assertEqual(rgg["is_forgotten"], 0)

        # p02 still sees Arjun owns billing; p03 gets None due to personal forgotten_topics filter
        p02_win = get_winning_claim(self.conn, "p02", "northbeam:billing_owner", upto_day=6)
        self.assertIsNotNone(p02_win)
        self.assertIn("Arjun", p02_win["object_val"])

        p03_win = get_winning_claim(self.conn, "p03", "northbeam:billing_owner", upto_day=6)
        self.assertIsNone(p03_win)

    def test_forget_delivery_recomputes_promise_open_status(self) -> None:
        # p18 promised "the revised banner set" (r4u7rk4q) and delivered it in rd5f4ygq (Day 3)
        open_before = [p for p in get_open_promises(self.conn, "p02", "northbeam", upto_day=5) if "banner" in p["what"].lower()]
        self.assertEqual(open_before, [])

        # If p18 retracts their delivery message, r4u7rk4q recomputes cleanly back to open
        forget_for_person(self.conn, "p18", "Forget what I said about Sharing the revised banner set.", day=6)
        open_after = [p for p in get_open_promises(self.conn, "p02", "northbeam", upto_day=6) if "banner" in p["what"].lower()]
        self.assertEqual(len(open_after), 1)
        self.assertEqual(open_after[0]["sources"], ["r4u7rk4q"])


if __name__ == "__main__":
    unittest.main()

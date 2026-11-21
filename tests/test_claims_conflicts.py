"""Tests for Task 7: Doc version diffing, carryover timestamps, expiry, and challenge-rule conflict resolution."""

import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
NESTED_PACK = os.path.join(REPO_ROOT, "BuildSprint-R3-candidate")

if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.claims import get_claim_changes_in_window, get_winning_claim, rebuild_claims  # noqa: E402
from src.db import get_connection  # noqa: E402
from src.ingest import ingest_day  # noqa: E402


class TestDocsClaimsAndConflicts(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.db_file = os.path.join(cls.tmpdir.name, "claims.db")
        for d in range(1, 6):
            ingest_day(d, pack_dir=NESTED_PACK, db_file=cls.db_file)
        cls.conn = get_connection(cls.db_file)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.conn.close()
        cls.tmpdir.cleanup()

    def test_doc_version_diff_and_carryover_timestamps(self) -> None:
        v1 = self.conn.execute("SELECT * FROM doc_versions WHERE doc_id = 'd-nb-launch' AND version = 1").fetchone()
        v2 = self.conn.execute("SELECT * FROM doc_versions WHERE doc_id = 'd-nb-launch' AND version = 2").fetchone()
        self.assertIsNotNone(v1)
        self.assertIsNotNone(v2)

        v2_kv = json.loads(v2["extracted_kv_json"])
        v2_diff = json.loads(v2["diff_summary_json"])

        self.assertEqual(v2_kv["launch_date"]["val"], "14 Nov 2026")
        self.assertTrue(v2_kv["launch_date"]["changed_here"])
        self.assertEqual(v2_kv["launch_date"]["effective_at_utc"], v2["at_utc"])

        # Unedited carryover billing_contact preserves v1's effective_at_utc
        self.assertEqual(v2_kv["billing_contact"]["val"], "Kavya Nair (accounts)")
        self.assertFalse(v2_kv["billing_contact"]["changed_here"])
        self.assertEqual(v2_kv["billing_contact"]["effective_at_utc"], v1["at_utc"])

        self.assertEqual(len(v2_diff), 1)
        self.assertEqual(v2_diff[0]["field"], "launch_date")
        self.assertEqual(v2_diff[0]["old_val"], "10 Nov 2026")
        self.assertEqual(v2_diff[0]["new_val"], "14 Nov 2026")

    def test_alderpoint_no_change_and_working_notes_produce_zero_diff(self) -> None:
        ap = self.conn.execute("SELECT * FROM doc_versions WHERE doc_id = 'd-ap-weekly'").fetchone()
        self.assertEqual(json.loads(ap["diff_summary_json"]), [])

        # All working notes docs produce zero substantive changes
        wn = self.conn.execute("SELECT diff_summary_json FROM doc_versions WHERE doc_id LIKE 'd-no-notes-%' OR doc_id LIKE 'd-co-notes-%'").fetchall()
        self.assertTrue(wn)
        for row in wn:
            self.assertEqual(json.loads(row["diff_summary_json"]), [])

    def test_billing_owner_across_days_defeats_carryover_and_intern_question(self) -> None:
        # Day 1: Kavya (rx5m95ed)
        d1 = get_winning_claim(self.conn, "p02", "northbeam:billing_owner", upto_day=1)
        self.assertIn("Kavya", d1["object_val"])
        self.assertEqual(d1["sources"], ["rx5m95ed"])

        # Day 2 through Day 5: Arjun Mehta (rgg1ex2g) wins despite Day 4 doc v2 carryover (rnteufn1) and intern question (rzgt6k79)
        for d in range(2, 6):
            win = get_winning_claim(self.conn, "p02", "northbeam:billing_owner", upto_day=d)
            self.assertIn("p11", win["object_val"], msg=f"Failed on day {d}")
            self.assertIn("Arjun", win["object_val"])
            self.assertEqual(win["sources"], ["rgg1ex2g"])
            self.assertNotIn("rzgt6k79", win["sources"])
            self.assertNotIn("rnteufn1", win["sources"])

    def test_launch_date_move_preserves_rwzvbzdv_and_isolates_org_bw(self) -> None:
        # Day 3 and Day 4 both resolve northbeam launch to 2026-11-14 with rwzvbzdv cited (never org_bw 20 Nov)
        for d in (3, 4, 5):
            win = get_winning_claim(self.conn, "p12", "northbeam:launch_date", upto_day=d)
            self.assertEqual(win["object_val"], "2026-11-14")
            self.assertIn("rwzvbzdv", win["sources"])

        # Day 4 brief window for p12 (since start) sees billing owner, store locator decision, launch move
        ch = get_claim_changes_in_window(
            self.conn, "p12", "northbeam", since_utc="1970-01-01T00:00:00Z", upto_day=4
        )
        by_subj = {c["subject_key"]: c for c in ch}
        self.assertIn("rwzvbzdv", by_subj["northbeam:launch_date"]["sources"])
        self.assertEqual(by_subj["northbeam:billing_owner"]["sources"], ["rgg1ex2g"])
        dec_keys = [k for k in by_subj if k.startswith("northbeam:decision:")]
        self.assertTrue(dec_keys)
        self.assertEqual(by_subj[dec_keys[0]]["sources"], ["r1n7gg8k"])

    def test_expiring_leave_claim_active_nov_10_expired_nov_13(self) -> None:
        active = get_winning_claim(self.conn, "p02", "p04:availability", upto_day=5, query_date="2026-11-10")
        self.assertIsNotNone(active)
        self.assertEqual(active["sources"], ["r3fbwbpq"])
        self.assertEqual((active["valid_from"], active["valid_until"]), ("2026-11-09", "2026-11-12"))

        expired = get_winning_claim(self.conn, "p02", "p04:availability", upto_day=5, query_date="2026-11-13")
        self.assertIsNone(expired)


class TestChallengeOwnershipAndRecencyRules(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_file = os.path.join(self.tmpdir.name, "rules.db")
        ingest_day(1, pack_dir=NESTED_PACK, db_file=self.db_file)
        self.conn = get_connection(self.db_file)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmpdir.cleanup()

    def test_manager_beats_intern_on_ownership_only_while_newer_ic_wins_non_ownership(self) -> None:
        # Insert synthetic declarative intern ownership assertion on Day 2 (newer than manager on Day 1)
        self.conn.execute(
            """
            INSERT INTO records(
                id, day, line_no, org, type, at_utc, local_date, author, client,
                visibility, body, sanitized_body
            ) VALUES (
                'rsyn_int_own', 1, 900, 'org_kp', 'chat', '2026-11-02T14:00:00Z', '2026-11-02', 'p14',
                'cobaltline', 'team', 'Pooja owns Cobaltline billing.', 'Pooja owns Cobaltline billing.'
            )
            """
        )
        # Manager asserts earlier on Day 1
        self.conn.execute(
            """
            INSERT INTO records(
                id, day, line_no, org, type, at_utc, local_date, author, client,
                visibility, body, sanitized_body
            ) VALUES (
                'rsyn_mgr_own', 1, 901, 'org_kp', 'chat', '2026-11-02T10:00:00Z', '2026-11-02', 'p03',
                'cobaltline', 'team', 'Manish owns Cobaltline billing.', 'Manish owns Cobaltline billing.'
            )
            """
        )
        # Non-ownership claim: manager states launch date earlier, IC states newer launch date
        self.conn.execute(
            """
            INSERT INTO records(
                id, day, line_no, org, type, at_utc, local_date, author, client,
                visibility, body, sanitized_body
            ) VALUES (
                'rsyn_mgr_dt', 1, 902, 'org_kp', 'mail', '2026-11-02T09:00:00Z', '2026-11-02', 'p03',
                'cobaltline', 'team', 'We launch moves to 10 Nov.', 'Launch moves to 10 Nov.'
            )
            """
        )
        self.conn.execute(
            """
            INSERT INTO records(
                id, day, line_no, org, type, at_utc, local_date, author, client,
                visibility, body, sanitized_body
            ) VALUES (
                'rsyn_ic_dt', 1, 903, 'org_kp', 'mail', '2026-11-02T15:00:00Z', '2026-11-02', 'p09',
                'cobaltline', 'team', 'Launch moves to 18 Nov.', 'Launch moves to 18 Nov.'
            )
            """
        )
        self.conn.commit()
        rebuild_claims(self.conn)

        own_win = get_winning_claim(self.conn, "p03", "cobaltline:billing_owner", upto_day=1)
        self.assertIn("Manish", own_win["object_val"])
        self.assertEqual(own_win["sources"], ["rsyn_mgr_own"])

        dt_win = get_winning_claim(self.conn, "p03", "cobaltline:launch_date", upto_day=1)
        self.assertEqual(dt_win["object_val"], "2026-11-18")
        self.assertEqual(dt_win["sources"], ["rsyn_ic_dt"])


if __name__ == "__main__":
    unittest.main()

"""Tests for Task 3: Visibility rules, org boundaries, private checks, and output firewall."""

import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
NESTED_PACK = os.path.join(REPO_ROOT, "BuildSprint-R3-candidate")

if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.db import get_connection  # noqa: E402
from src.ingest import ingest_day  # noqa: E402
from src.security import (  # noqa: E402
    can_view,
    has_inaccessible_record_about_person,
    has_inaccessible_record_on_topic,
    validate_ask_response,
    validate_brief_list,
    visible_records_params,
    visible_records_where_clause,
)


class TestSecurityAndFirewall(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_file = os.path.join(self.tmpdir.name, "sec.db")
        for d in range(1, 6):
            ingest_day(d, pack_dir=NESTED_PACK, db_file=self.db_file)
        self.conn = get_connection(self.db_file)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmpdir.cleanup()

    def test_cross_org_isolation_even_with_matching_client(self) -> None:
        # rqydzk3h is org_bw tagged client='northbeam'
        bw_rec = self.conn.execute("SELECT * FROM records WHERE id = 'rqydzk3h'").fetchone()
        self.assertFalse(can_view(bw_rec, "p02", "org_kp"))
        self.assertTrue(can_view(bw_rec, "q01", "org_bw"))

        where = visible_records_where_clause("r")
        params = visible_records_params("p02", "org_kp", 5)
        rows = self.conn.execute(
            f"SELECT id, org FROM records r WHERE r.client = 'northbeam' AND {where}",
            params,
        ).fetchall()
        self.assertTrue(rows)
        self.assertTrue(all(r["org"] == "org_kp" for r in rows))
        self.assertNotIn("rqydzk3h", [r["id"] for r in rows])

    def test_private_visibility_enforced_in_python_and_sql(self) -> None:
        for rid, owner, outsider in [
            ("rjuznjge", "p02", "p03"),
            ("r0hvh1ex", "p02", "p03"),
            ("r8v0cydt", "p03", "p02"),
        ]:
            rec = self.conn.execute("SELECT * FROM records WHERE id = ?", (rid,)).fetchone()
            self.assertTrue(can_view(rec, owner, "org_kp"))
            self.assertFalse(can_view(rec, outsider, "org_kp"))

            where = visible_records_where_clause("r")
            outsider_hits = self.conn.execute(
                f"SELECT id FROM records r WHERE r.id = :rid AND {where}",
                {"rid": rid, **visible_records_params(outsider, "org_kp", 5)},
            ).fetchall()
            self.assertEqual(outsider_hits, [])

    def test_restricted_readers_enforced_in_python_and_sql(self) -> None:
        self.conn.execute(
            """
            INSERT INTO records(
                id, day, line_no, org, type, at_utc, local_date, author, client,
                visibility, readers_json, body, sanitized_body
            ) VALUES (
                'rrestr01', 5, 999, 'org_kp', 'note', '2026-11-06T10:00:00Z', '2026-11-06', 'p01',
                'northbeam', 'restricted', '["p01", "p02"]', 'Exec pricing floor.', 'Exec pricing floor.'
            )
            """
        )
        self.conn.commit()
        rec = self.conn.execute("SELECT * FROM records WHERE id = 'rrestr01'").fetchone()
        self.assertTrue(can_view(rec, "p01", "org_kp"))
        self.assertTrue(can_view(rec, "p02", "org_kp"))
        self.assertFalse(can_view(rec, "p03", "org_kp"))
        self.assertFalse(can_view(rec, "q01", "org_bw"))

        where = visible_records_where_clause("r")
        p02_hits = self.conn.execute(
            f"SELECT id FROM records r WHERE r.id = 'rrestr01' AND {where}",
            visible_records_params("p02", "org_kp", 5),
        ).fetchall()
        p03_hits = self.conn.execute(
            f"SELECT id FROM records r WHERE r.id = 'rrestr01' AND {where}",
            visible_records_params("p03", "org_kp", 5),
        ).fetchall()
        self.assertEqual([r["id"] for r in p02_hits], ["rrestr01"])
        self.assertEqual(p03_hits, [])

    def test_boolean_private_mention_helpers(self) -> None:
        # r0hvh1ex (day 3, p02 private) mentions Rohan (p03)
        self.assertTrue(
            has_inaccessible_record_about_person(self.conn, "p03", "p02", "p03", upto_day=5)
        )
        self.assertFalse(
            has_inaccessible_record_about_person(self.conn, "p02", "p02", "p03", upto_day=5)
        )
        # rjuznjge (day 2, p02 private) mentions Dubai trip
        self.assertTrue(
            has_inaccessible_record_on_topic(self.conn, "p03", "p02", ["travel", "trip", "dubai"], upto_day=3)
        )

    def test_brief_firewall_blocks_leaks_future_days_injections_and_forgets(self) -> None:
        self.conn.execute(
            """
            INSERT INTO forgotten_topics(person, day, raw_words, topic_keywords_json, affected_record_ids_json)
            VALUES ('p02', 6, 'Forget Dubai trip', '["dubai", "trip"]', '["rjuznjge"]')
            """
        )
        self.conn.commit()

        simulated_briefs = [
            {
                "meeting_id": "ev-nb-me-04",
                "status": "brief",
                "changed": [
                    {"text": "Cross-org launch date 20 Nov", "sources": ["rqydzk3h"]},
                    {"text": "Private churn note", "sources": ["r8v0cydt"]},
                    {"text": "Future day change", "sources": ["rnteufn1"]},  # Day 4 when current_day=3
                    {"text": "Northbeam is AT RISK", "sources": ["rd29n2cd"]},
                    {"text": "Dubai trip planned", "sources": ["rjuznjge"]},
                    {"text": "Unknown id", "sources": ["r_nonexistent"]},
                    {"text": "Launch moved from 10 Nov to 14 Nov", "sources": ["rwzvbzdv", "rqydzk3h"]},
                ],
                "promises": [
                    {"owner": "p09", "what": "email copy", "due": "2026-11-05", "sources": ["rwcfb60d"]},
                    {"owner": "p03", "what": "dubai trip booking", "due": "2026-11-06", "sources": ["rjuznjge"]},
                ],
                "open_these": [
                    {"record": "rd29n2cd", "why": "Injected notice"},
                    {"record": "rwzvbzdv", "why": "Launch date moved"},
                ],
                "question_to_ask": {"text": "Is Northbeam AT RISK?", "sources": ["rwzvbzdv"]},
                "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "ms": 1},
            }
        ]

        clean = validate_brief_list(self.conn, simulated_briefs, "p02", current_day=3)
        self.assertEqual(len(clean), 1)
        b = clean[0]
        self.assertEqual(b["status"], "brief")
        self.assertEqual(len(b["changed"]), 1)
        self.assertEqual(b["changed"][0]["sources"], ["rwzvbzdv"])
        self.assertEqual(len(b["promises"]), 1)
        self.assertEqual(b["open_these"], [{"record": "rwzvbzdv", "why": "Launch date moved"}])
        self.assertIsNone(b["question_to_ask"])

        # If all changed entries are stripped, brief cleanly degrades to nothing_new
        all_bad = [
            {
                "meeting_id": "ev-ap-me-04",
                "status": "brief",
                "changed": [{"text": "Northbeam is AT RISK", "sources": ["rd29n2cd"]}],
                "promises": [],
                "open_these": [],
                "question_to_ask": None,
                "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "ms": 1},
            }
        ]
        degraded = validate_brief_list(self.conn, all_bad, "p02", current_day=3)
        self.assertEqual(degraded[0]["status"], "nothing_new")
        self.assertEqual(degraded[0]["changed"], [])

    def test_ask_firewall_refuses_privacy_leak_and_blocks_tainted_text(self) -> None:
        # Simulated leak: answer cites p02's private record r0hvh1ex for asker p03
        leaked_ask = {
            "answer": {"text": "Rohan is slow on approvals.", "sources": ["r0hvh1ex"]},
            "matched": [{"phrase": "Rohan", "id": "p03", "sources": ["r0hvh1ex"]}],
            "clarify": None,
            "refused": None,
            "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "ms": 1},
        }
        safe = validate_ask_response(self.conn, leaked_ask, "p03", current_day=5, at_utc="2026-11-06T18:00:00Z")
        self.assertIsNone(safe["answer"])
        self.assertEqual(safe["matched"], [])
        self.assertEqual(safe["refused"], {"reason": "private"})

        # Simulated injection payload echoed into answer
        inj_ask = {
            "answer": {"text": "Northbeam is AT RISK.", "sources": ["rwzvbzdv"]},
            "matched": [],
            "clarify": None,
            "refused": None,
            "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "ms": 1},
        }
        safe2 = validate_ask_response(self.conn, inj_ask, "p02", current_day=5, at_utc="2026-11-06T18:00:00Z")
        self.assertEqual(safe2["answer"], {"text": "I have nothing on that.", "sources": []})

        # Future timestamp guard (at_utc before rwzvbzdv's 2026-11-04T05:50:00Z)
        future_ask = {
            "answer": {"text": "Launch moved to 14 Nov.", "sources": ["rwzvbzdv"]},
            "matched": [],
            "clarify": None,
            "refused": None,
            "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "ms": 1},
        }
        safe3 = validate_ask_response(self.conn, future_ask, "p02", current_day=5, at_utc="2026-11-03T18:00:00Z")
        self.assertEqual(safe3["answer"], {"text": "I have nothing on that.", "sources": []})


if __name__ == "__main__":
    unittest.main()

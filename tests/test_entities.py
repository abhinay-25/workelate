"""Tests for Task 5: Evidence-based entity resolution, alias learning, and collaboration scoring."""

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

from src.db import get_connection  # noqa: E402
from src.entities import (  # noqa: E402
    resolve_artifact_mention,
    resolve_client_mention,
    resolve_person_mention,
    resolve_task_mentions,
)
from src.ingest import ingest_day  # noqa: E402


class TestEntityResolutionPack(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_file = os.path.join(self.tmpdir.name, "ent.db")
        for d in range(1, 6):
            ingest_day(d, pack_dir=NESTED_PACK, db_file=self.db_file)
        self.conn = get_connection(self.db_file)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmpdir.cleanup()

    def test_client_lexical_and_acronym_aliases(self) -> None:
        for text, expected in [
            ("Who handles billing for NB?", "northbeam"),
            ("What's the status of launch email sequence (NB Retail)?", "northbeam"),
            ("Who owns the CL fleet dashboard v1?", "cobaltline"),
            ("Who owns the Cobalt rate card update?", "cobaltline"),
            ("Status of Alderpoint monthly report?", "alderpoint"),
        ]:
            res = resolve_client_mention(self.conn, "p02", text, upto_day=5)
            self.assertEqual(res["client_id"], expected, msg=text)
            self.assertFalse(res["ambiguous"], msg=text)

    def test_distractors_never_resolve_to_northbeam(self) -> None:
        for text in (
            "Northstar Print poster offers",
            "Northstar print run planning",
            "Beam design system v2.4 tokens",
        ):
            res = resolve_client_mention(self.conn, "p02", text, upto_day=5)
            self.assertIsNone(res["client_id"], msg=f"Unexpected match on {text}: {res}")

    def test_london_and_mumbai_resolved_via_converging_evidence(self) -> None:
        # Task co-constraint + domain/event corroboration for London account
        t1 = resolve_task_mentions(
            self.conn, "p25", "When is the tms data pipeline for the London account due?", upto_day=1
        )
        self.assertEqual(t1[0]["task_id"], "t-cl-03")
        c1 = resolve_client_mention(
            self.conn,
            "p25",
            "When is the tms data pipeline for the London account due?",
            upto_day=1,
            inferred_task_client=t1[0]["client"],
        )
        self.assertEqual(c1["client_id"], "cobaltline")

        # Task co-constraint for Mumbai client (q03-03)
        t2 = resolve_task_mentions(
            self.conn, "p02", "What's the status of homepage redesign (the Mumbai client)?", upto_day=3
        )
        self.assertEqual(t2[0]["task_id"], "t-nb-01")
        c2 = resolve_client_mention(
            self.conn,
            "p02",
            "What's the status of homepage redesign (the Mumbai client)?",
            upto_day=3,
            inferred_task_client=t2[0]["client"],
        )
        self.assertEqual(c2["client_id"], "northbeam")

        # Predicate + asker portfolio + TLD cue for Mumbai client without task title (q04-02)
        c3 = resolve_client_mention(
            self.conn, "p02", "Who owns billing for the Mumbai client?", upto_day=4
        )
        self.assertEqual(c3["client_id"], "northbeam")
        self.assertFalse(c3["ambiguous"])

    def test_same_first_name_disambiguation_arjun(self) -> None:
        # p02 asking about Arjun on Northbeam resolves uniquely to p11 (Arjun Mehta), citing rgg1ex2g
        res = resolve_person_mention(
            self.conn,
            "p02",
            "Send the NB deck to Arjun. Which Arjun is that?",
            upto_day=5,
            client_id="northbeam",
        )
        self.assertEqual(res["person_id"], "p11")
        self.assertFalse(res["ambiguous"])
        self.assertIn("rgg1ex2g", res["sources"])

    def test_artifact_deck_resolution_respects_org_boundary(self) -> None:
        # Finds Kitepoint's rqm1zkbc, never Brightwave's org_bw pitch decks
        art = resolve_artifact_mention(self.conn, "p02", "deck", client_id="northbeam", upto_day=5)
        self.assertIsNotNone(art)
        self.assertEqual(art["record_id"], "rqm1zkbc")

    def test_synthetic_unseen_client_person_and_tie_ambiguity(self) -> None:
        # Verify zero hardcoding using a completely unseen client & two tied same-named employees
        synth_dir = os.path.join(self.tmpdir.name, "synth")
        os.makedirs(os.path.join(synth_dir, "data", "days"), exist_ok=True)
        os.makedirs(os.path.join(synth_dir, "data", "feedback"), exist_ok=True)
        people = {
            "org_x": {
                "name": "Org X",
                "people": [
                    {"id": "x01", "name": "Priya Verma", "role": "manager", "team": "client", "zone": "Asia/Kolkata"},
                    {"id": "x02", "name": "Riya Sen", "role": "designer", "team": "design", "zone": "Asia/Kolkata"},
                    {"id": "x03", "name": "Riya Kapoor", "role": "developer", "team": "dev", "zone": "Asia/Kolkata"},
                ],
            }
        }
        clients = [{"id": "greenpeak", "name": "Greenpeak Logistics"}]
        recs = [
            {
                "id": "rx001",
                "org": "org_x",
                "type": "mail",
                "at": "2026-11-02T09:00:00Z",
                "author": "ext:ops@greenpeak.co.uk",
                "client": "greenpeak",
                "visibility": "team",
                "to": ["x01"],
                "subject": "GP update",
                "body": "Greenpeak UK schedule confirmed.",
            }
        ]
        with open(os.path.join(synth_dir, "data", "people.json"), "w", encoding="utf-8") as f:
            json.dump(people, f)
        with open(os.path.join(synth_dir, "data", "clients.json"), "w", encoding="utf-8") as f:
            json.dump(clients, f)
        with open(os.path.join(synth_dir, "data", "days", "day-01.jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(r) for r in recs) + "\n")

        synth_db = os.path.join(self.tmpdir.name, "synth.db")
        ingest_day(1, pack_dir=synth_dir, db_file=synth_db)
        sconn = get_connection(synth_db)
        try:
            c_res = resolve_client_mention(sconn, "x01", "When is the GP rollout?", upto_day=1)
            self.assertEqual(c_res["client_id"], "greenpeak")

            # Two Riyas with zero distinguishing collaboration must return ambiguous=True
            p_res = resolve_person_mention(sconn, "x01", "Send it to Riya.", upto_day=1, client_id="greenpeak")
            self.assertIsNone(p_res["person_id"])
            self.assertTrue(p_res["ambiguous"])
            self.assertEqual(set(p_res["candidates"]), {"x02", "x03"})
        finally:
            sconn.close()


if __name__ == "__main__":
    unittest.main()

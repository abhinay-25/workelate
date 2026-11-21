"""End-to-end tests for the FastAPI WorkMate HTTP layer wrapping BuildSprint R3 core logic."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
NESTED_PACK = os.path.join(REPO_ROOT, "BuildSprint-R3-candidate")

if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from backend.api.main import create_app  # noqa: E402
from src.ingest import ingest_day  # noqa: E402


class TestFastAPIAdapter(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._seed_dir = tempfile.TemporaryDirectory()
        cls._template_db = os.path.join(cls._seed_dir.name, "template_r3.db")
        for day in range(1, 6):
            ingest_day(day, pack_dir=NESTED_PACK, db_file=cls._template_db)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._seed_dir.cleanup()

    def setUp(self) -> None:
        self._old_db = os.environ.get("R3_DB_PATH")
        self._old_pack = os.environ.get("R3_PACK")
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_file = os.path.join(self._tmpdir.name, "test_api.db")
        shutil.copy2(self._template_db, self.db_file)
        os.environ["R3_DB_PATH"] = self.db_file
        os.environ["R3_PACK"] = NESTED_PACK
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self.client.close()
        if self._old_db is None:
            os.environ.pop("R3_DB_PATH", None)
        else:
            os.environ["R3_DB_PATH"] = self._old_db
        if self._old_pack is None:
            os.environ.pop("R3_PACK", None)
        else:
            os.environ["R3_PACK"] = self._old_pack
        self._tmpdir.cleanup()

    def test_health_and_people_endpoints(self) -> None:
        r_health = self.client.get("/api/health")
        self.assertEqual(r_health.status_code, 200, r_health.text)
        hd = r_health.json()
        self.assertEqual(hd["status"], "ok")
        self.assertTrue(hd["db_connected"])
        self.assertEqual(hd["current_day"], 5)
        self.assertEqual(hd["ingested_days"], [1, 2, 3, 4, 5])
        self.assertGreater(hd["total_records"], 1000)
        self.assertGreater(hd["indexed_vectors"], 0)
        self.assertEqual(hd["default_date"], "2026-11-06")

        r_ppl = self.client.get("/api/people")
        self.assertEqual(r_ppl.status_code, 200)
        pd = r_ppl.json()
        self.assertEqual(len(pd["people"]), 33)
        orgs = {p["org_id"] for p in pd["people"]}
        self.assertEqual(orgs, {"org_kp", "org_bw"})

    def test_overview_endpoint_and_org_isolation(self) -> None:
        r_kp = self.client.get("/api/overview", params={"person_id": "p02", "date": "2026-11-06"})
        self.assertEqual(r_kp.status_code, 200, r_kp.text)
        d_kp = r_kp.json()
        self.assertEqual(d_kp["person"]["org_id"], "org_kp")
        kp_client_ids = {c["client_id"] for c in d_kp["clients"]}
        self.assertIn("northbeam", kp_client_ids)
        self.assertGreater(len(d_kp["recent_activity"]), 0)
        self.assertGreater(d_kp["metrics"]["visible_records"], 0)

        # Cross-org isolation: Brightwave user (q01) never sees Kitepoint records (org_kp)
        r_bw = self.client.get("/api/overview", params={"person_id": "q01", "date": "2026-11-06"})
        self.assertEqual(r_bw.status_code, 200, r_bw.text)
        d_bw = r_bw.json()
        self.assertEqual(d_bw["person"]["org_id"], "org_bw")
        bw_ids = {item["record_id"] for item in d_bw["recent_activity"]}
        kp_ids = {item["record_id"] for item in d_kp["recent_activity"]}
        self.assertTrue(bw_ids.isdisjoint(kp_ids))
        for item in d_bw["recent_activity"]:
            self.assertFalse(item["author_id"].startswith("p"))
            # Verify q01's org-filtered detail is readable while kp record rgg1ex2g is denied
            self.assertEqual(self.client.get(f"/api/records/{item['record_id']}", params={"person_id": "q01"}).status_code, 200)

    def test_brief_generation_regenerate_and_client_filter(self) -> None:
        r1 = self.client.post(
            "/api/brief",
            json={"person_id": "p03", "date": "2026-11-06"},
        )
        self.assertEqual(r1.status_code, 200, r1.text)
        b1 = r1.json()
        self.assertEqual(b1["person_id"], "p03")
        self.assertGreater(len(b1["briefs"]), 0)
        first = b1["briefs"][0]
        self.assertIn(first["status"], ("brief", "nothing_new"))
        self.assertIn("usage", first)

        r2 = self.client.post(
            "/api/brief",
            json={"person_id": "p03", "date": "2026-11-06", "regenerate": True},
        )
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(len(r2.json()["briefs"]), len(b1["briefs"]))

    def test_ask_direct_clarify_refused_and_validation(self) -> None:
        # 1. Nickname + persistent feedback correction
        r_ans = self.client.post(
            "/api/ask",
            json={
                "person_id": "p02",
                "at": "2026-11-05T18:00:00Z",
                "question": "Who owns billing for the Mumbai client?",
            },
        )
        self.assertEqual(r_ans.status_code, 200, r_ans.text)
        d_ans = r_ans.json()
        self.assertEqual(d_ans["status"], "answer")
        self.assertIn("Arjun", d_ans["answer"]["text"])
        self.assertGreater(len(d_ans["answer"]["sources"]), 0)

        # 2. Ambiguous question -> clarify options
        r_cl = self.client.post(
            "/api/ask",
            json={
                "person_id": "p02",
                "at": "2026-11-05T18:00:00Z",
                "question": "Is the Friday thing done?",
            },
        )
        self.assertEqual(r_cl.status_code, 200)
        d_cl = r_cl.json()
        self.assertEqual(d_cl["status"], "clarify")
        self.assertGreaterEqual(len(d_cl["clarify"]["options"]), 2)

        # 3. Private record about p03 -> refused without leaking
        r_ref = self.client.post(
            "/api/ask",
            json={
                "person_id": "p03",
                "at": "2026-11-06T18:00:00Z",
                "question": "What did Meera say about me?",
            },
        )
        self.assertEqual(r_ref.status_code, 200)
        d_ref = r_ref.json()
        self.assertEqual(d_ref["status"], "refused")
        self.assertEqual(d_ref["refused"], {"reason": "private"})

        # 4. Bad input validation
        r_bad_q = self.client.post(
            "/api/ask",
            json={"person_id": "p02", "question": "   "},
        )
        self.assertEqual(r_bad_q.status_code, 400)
        r_bad_p = self.client.post(
            "/api/ask",
            json={"person_id": "unknown99", "question": "hello"},
        )
        self.assertEqual(r_bad_p.status_code, 404)

    def test_record_detail_access_control(self) -> None:
        # Team record accessible by org_kp, denied to org_bw
        r_ok = self.client.get("/api/records/rgg1ex2g", params={"person_id": "p02"})
        self.assertEqual(r_ok.status_code, 200, r_ok.text)
        self.assertEqual(r_ok.json()["id"], "rgg1ex2g")

        r_cross = self.client.get("/api/records/rgg1ex2g", params={"person_id": "q01"})
        self.assertEqual(r_cross.status_code, 403)

        # Private note (rjuznjge) belongs to p02 only
        r_own = self.client.get("/api/records/rjuznjge", params={"person_id": "p02"})
        self.assertEqual(r_own.status_code, 200)
        r_other = self.client.get("/api/records/rjuznjge", params={"person_id": "p03"})
        self.assertEqual(r_other.status_code, 403)

    def test_memory_correct_and_forget_cascading_isolation(self) -> None:
        r_mem = self.client.get("/api/memory", params={"person_id": "p02"})
        self.assertEqual(r_mem.status_code, 200, r_mem.text)
        dm = r_mem.json()
        self.assertTrue(dm["active_preferences_summary"]["skip_newsletters"])
        self.assertGreater(len(dm["claims"]), 0)
        self.assertGreater(len(dm["tasks"]), 0)

        # Hedged correction must be ignored
        r_hedged = self.client.post(
            "/api/memory/correct",
            json={"person_id": "p02", "words": "I think maybe Kavya handles billing?"},
        )
        self.assertEqual(r_hedged.status_code, 200)
        self.assertFalse(r_hedged.json()["applied"])

        # Explicit correction updates billing owner for Alderpoint
        r_corr = self.client.post(
            "/api/memory/correct",
            json={
                "person_id": "p02",
                "words": "No, Rohan Kapoor handles Alderpoint billing now, not Kavya Nair.",
            },
        )
        self.assertEqual(r_corr.status_code, 200, r_corr.text)
        dc = r_corr.json()
        self.assertTrue(dc["applied"])
        self.assertTrue(any("Rohan" in c["value"] for c in dc["updated_claims"]))

        # Forget Dubai trip for p02 and verify cascading retraction
        r_fgt = self.client.post(
            "/api/memory/forget",
            json={"person_id": "p02", "what": "my Dubai trip"},
        )
        self.assertEqual(r_fgt.status_code, 200, r_fgt.text)
        self.assertEqual(r_fgt.json()["forgotten_records"], 1)

        r_rec_gone = self.client.get("/api/records/rjuznjge", params={"person_id": "p02"})
        self.assertEqual(r_rec_gone.status_code, 404)

        r_ask_gone = self.client.post(
            "/api/ask",
            json={
                "person_id": "p02",
                "at": "2026-11-06T18:00:00Z",
                "question": "When is my Dubai trip?",
            },
        )
        self.assertEqual(r_ask_gone.status_code, 200)
        self.assertEqual(r_ask_gone.json()["status"], "nothing")

        # Shared team knowledge stays intact after personal forget
        r_shared = self.client.post(
            "/api/ask",
            json={
                "person_id": "p03",
                "at": "2026-11-06T18:00:00Z",
                "question": "Who owns the Cobalt uat test plan?",
            },
        )
        self.assertEqual(r_shared.status_code, 200)
        self.assertEqual(r_shared.json()["status"], "answer")

    def test_audit_and_usage_endpoints_no_key_leak(self) -> None:
        self.client.post(
            "/api/ask",
            json={"person_id": "p02", "at": "2026-11-06T18:00:00Z", "question": "Who owns the Northbeam revised sow?"},
        )
        self.client.post("/api/brief", json={"person_id": "p03", "date": "2026-11-06"})

        r_aud = self.client.get("/api/audit", params={"person_id": "p02"})
        self.assertEqual(r_aud.status_code, 200, r_aud.text)
        da = r_aud.json()
        self.assertEqual(da["current_day"], 5)
        self.assertGreater(len(da["events"]), 10)
        self.assertEqual(len(da["ingested_days"]), 5)

        r_usg = self.client.get("/api/usage")
        self.assertEqual(r_usg.status_code, 200, r_usg.text)
        du = r_usg.json()
        self.assertEqual(len(du["providers"]), 3)
        self.assertEqual(du["guardrails"]["brief_token_limit"], 8000)
        self.assertEqual(du["guardrails"]["ask_token_limit"], 4000)
        self.assertGreater(du["embedding"]["indexed_records"], 0)
        self.assertGreaterEqual(du["totals"]["total_requests"], 2)

        raw_body = r_usg.text + r_aud.text
        for secret_prefix in ("gsk_", "AQ.Ab", "sk-"):
            self.assertNotIn(secret_prefix, raw_body)

    def test_auto_seed_empty_db_and_ingest_validation(self) -> None:
        fresh_db = os.path.join(self._tmpdir.name, "fresh_autoseed.db")
        os.environ["R3_DB_PATH"] = fresh_db
        fresh_client = TestClient(create_app())
        try:
            with fresh_client:
                r_h = fresh_client.get("/api/health")
                self.assertEqual(r_h.status_code, 200)
                hd = r_h.json()
                self.assertEqual(hd["current_day"], 5)
                self.assertEqual(hd["ingested_days"], [1, 2, 3, 4, 5])
                self.assertGreater(hd["total_records"], 1000)

                r_bad_ingest = fresh_client.post("/api/ingest", json={"day": 29})
                self.assertEqual(r_bad_ingest.status_code, 404)
        finally:
            fresh_client.close()

    def test_ask_analytical_telemetry_and_usage_log_provider_model(self) -> None:
        def fake_http_post(url: str, headers: dict, payload: dict, timeout: int):
            user_msg = payload["messages"][1]["content"]
            matches = re.findall(
                r'<workplace_record[^>]*id="([^"]+)"[^>]*>(.*?)</workplace_record>',
                user_msg,
                re.DOTALL,
            )
            picked_ids: list[str] = []
            snippets: list[str] = []
            for rid, block in matches[:2]:
                picked_ids.append(rid)
                # Extract real non-tag words from the authorized record body to guarantee grounding
                body_lines = [ln.strip() for ln in block.splitlines() if ln.strip() and not ln.strip().startswith("source_id:")]
                tail = " ".join(body_lines[-2:]) if body_lines else rid
                snippets.append(f"{tail[:140]} [{rid}]")
            ans_text = (
                "Executive Memo Summary: " + " | ".join(snippets)
                if picked_ids
                else "I have nothing on that."
            )
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "answer": ans_text,
                                    "sources": picked_ids,
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 420, "completion_tokens": 64},
            }

        old_groq_key = os.environ.get("GROQ_API_KEY")
        os.environ["GROQ_API_KEY"] = "test-groq-api-key"
        try:
            with patch("src.llm._default_http_post", side_effect=fake_http_post):
                r_ask = self.client.post(
                    "/api/ask",
                    json={
                        "person_id": "p02",
                        "at": "2026-11-06T18:00:00Z",
                        "question": "Write an executive memo summarizing current project risks and recommendations.",
                    },
                )
        finally:
            if old_groq_key is None:
                os.environ.pop("GROQ_API_KEY", None)
            else:
                os.environ["GROQ_API_KEY"] = old_groq_key

        self.assertEqual(r_ask.status_code, 200, r_ask.text)
        da = r_ask.json()
        self.assertEqual(da["status"], "answer", da)
        self.assertGreater(len(da["answer"]["sources"]), 0)
        self.assertEqual(da["usage"]["mode"], "llm_assisted")
        self.assertEqual(da["usage"]["provider"], "groq")
        self.assertTrue(da["usage"]["model"])
        self.assertEqual(da["usage"]["input_tokens"], 420)
        self.assertEqual(da["usage"]["output_tokens"], 64)
        self.assertGreater(da["usage"]["cost_usd"], 0.0)

        r_usg = self.client.get("/api/usage")
        self.assertEqual(r_usg.status_code, 200, r_usg.text)
        du = r_usg.json()
        self.assertGreaterEqual(du["totals"]["llm_assisted_requests"], 1)
        self.assertEqual(du["totals"]["total_input_tokens"], 420)
        self.assertEqual(du["totals"]["total_output_tokens"], 64)
        self.assertGreater(du["totals"]["total_cost_usd"], 0.0)
        recent = du["recent_calls"][0]
        self.assertEqual(recent["mode"], "llm_assisted")
        self.assertEqual(recent["provider"], "groq")
        self.assertEqual(recent["model"], da["usage"]["model"])
        for secret_prefix in ("gsk_", "AQ.Ab", "sk-"):
            self.assertNotIn(secret_prefix, r_usg.text)


if __name__ == "__main__":
    unittest.main()

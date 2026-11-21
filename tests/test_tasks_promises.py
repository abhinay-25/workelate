"""Tests for Task 6: Event-sourced tasks, snapshot-bug resistance, English/Hinglish promises, and delivery closure."""

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
from src.promises import (  # noqa: E402
    extract_promise_from_text,
    get_deliveries_in_window,
    get_open_promises,
)
from src.tasks_engine import get_task_changes_in_window, get_task_state_at  # noqa: E402


class TestTasksAndPromisesPack(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.db_file = os.path.join(cls.tmpdir.name, "tp.db")
        for d in range(1, 6):
            ingest_day(d, pack_dir=NESTED_PACK, db_file=cls.db_file)
        cls.conn = get_connection(cls.db_file)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.conn.close()
        cls.tmpdir.cleanup()

    def test_all_18_tasks_defeat_comment_and_cross_field_snapshot_bugs(self) -> None:
        expected = {
            "t-ap-01": ("in_progress", "p22", "2026-11-30"),
            "t-cl-01": ("done", "p05", "2026-11-10"),
            "t-cl-02": ("in_progress", "p03", "2026-11-07"),
            "t-cl-03": ("done", "p05", "2026-11-12"),
            "t-cl-04": ("done", "p15", "2026-11-09"),
            "t-cl-05": ("done", "p21", "2026-11-06"),
            "t-cl-06": ("blocked", "p21", "2026-11-16"),
            "t-me-01": ("todo", "p02", "2026-11-06"),
            "t-me-02": ("todo", "p02", "2026-11-06"),
            "t-me-03": ("todo", "p02", "2026-11-13"),
            "t-nb-01": ("done", "p18", "2026-11-13"),
            "t-nb-02": ("done", "p08", "2026-11-10"),
            "t-nb-03": ("todo", "p07", "2026-11-05"),
            "t-nb-04": ("todo", "p16", "2026-11-12"),
            "t-nb-05": ("blocked", "p09", "2026-11-09"),
            "t-nb-06": ("done", "p17", "2026-11-11"),
            "t-nb-07": ("cancelled", "p19", "2026-11-16"),
            "t-nb-08": ("done", "p04", "2026-11-06"),
        }
        rows = self.conn.execute("SELECT * FROM tasks_state").fetchall()
        self.assertEqual(len(rows), 18)
        for r in rows:
            tid = r["task_id"]
            self.assertEqual((r["status"], r["owner"], r["due"]), expected[tid], msg=f"Mismatch on {tid}")

    def test_point_in_time_task_replay(self) -> None:
        # q02-02: p25 asks at 2026-11-03T23:30:00Z (Day 2) about t-cl-02 due date (2026-11-05 before Day 3 move to 2026-11-07)
        st_day2 = get_task_state_at(self.conn, "t-cl-02", "p25", upto_day=2, upto_utc="2026-11-03T23:30:00Z")
        self.assertEqual(st_day2["due"], "2026-11-05")
        self.assertEqual(st_day2["owner"], "p03")

        # Day 5 view of t-cl-02 reflects 2026-11-07 due date
        st_day5 = get_task_state_at(self.conn, "t-cl-02", "p25", upto_day=5)
        self.assertEqual(st_day5["due"], "2026-11-07")

        # q01-05: p25 asks on Day 1 about t-cl-06 owner -> p25 before Day 4 reassignment to p21
        st_cl06_d1 = get_task_state_at(self.conn, "t-cl-06", "p25", upto_day=1, upto_utc="2026-11-02T23:30:00Z")
        self.assertEqual(st_cl06_d1["owner"], "p25")

    def test_noise_filtering_and_all_19_promises_extracted(self) -> None:
        # 19 client promises total across Days 1-5 (5 English mails + 14 Hinglish chats)
        total_promises = self.conn.execute("SELECT COUNT(*) FROM promises").fetchone()[0]
        self.assertEqual(total_promises, 19)
        fulfilled = self.conn.execute("SELECT COUNT(*) FROM promises WHERE status = 'fulfilled'").fetchone()[0]
        self.assertEqual(fulfilled, 7)

        # Ensure task comment filler and internal timesheet reminders never parse as promises
        self.assertIsNone(extract_promise_from_text("kal tak final ho jayega"))
        self.assertIsNone(extract_promise_from_text("reminder: timesheets by EOD"))

    def test_open_promises_match_official_answer_keys_days_1_to_4(self) -> None:
        for d in range(1, 5):
            with open(os.path.join(NESTED_PACK, "answers", f"day-{d:02d}.json"), encoding="utf-8") as f:
                key = json.load(f)
            for bk in key["briefs"]:
                expected_pairs = sorted(
                    (p["owner"], p["due"], tuple(p["sources_any"])) for p in bk.get("promises", [])
                )
                actual = get_open_promises(self.conn, bk["person"], bk["client"], upto_day=d)
                actual_pairs = sorted((p["owner"], p["due"], tuple(p["sources"])) for p in actual)
                self.assertEqual(
                    actual_pairs,
                    expected_pairs,
                    msg=f"Day {d} brief {bk['meeting_id']} promises mismatch",
                )

    def test_task_changes_and_deliveries_in_window(self) -> None:
        # Day 2 Cobaltline window (since start of Day 1): 3 status/owner changes + 2 deliveries
        changes = get_task_changes_in_window(
            self.conn, "p03", "cobaltline", since_utc="1970-01-01T00:00:00Z", upto_day=2
        )
        self.assertEqual(
            {(c["task_id"], c["field"], c["record_id"]) for c in changes},
            {
                ("t-cl-04", "status", "re11d3ra"),
                ("t-cl-02", "owner", "rjs47ab7"),
                ("t-cl-03", "status", "rgnq27as"),
            },
        )
        deliveries = get_deliveries_in_window(
            self.conn, "p03", "cobaltline", since_utc="1970-01-01T00:00:00Z", upto_day=2
        )
        self.assertEqual({d["record_id"] for d in deliveries}, {"rt0g56zv", "r69jj1uk"})


if __name__ == "__main__":
    unittest.main()

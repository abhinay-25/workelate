"""CLI router enforcing exact JSON stdout contracts and exit-0 resilience."""

import argparse
import json
import sys
from typing import Any

from src.ask import answer_question
from src.audit import audit_for_person
from src.brief import generate_briefs_for_person_date
from src.db import get_connection
from src.ingest import ingest_day
from src.memory import forget_for_person


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BuildSprint R3 — The Work Assistant That Remembers")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="Ingest day N records and day N-1 feedback")
    p_ingest.add_argument("--day", type=int, required=True)

    p_brief = sub.add_parser("brief", help="Generate meeting briefs for a person on a local date")
    p_brief.add_argument("--person", required=True)
    p_brief.add_argument("--date", required=True)

    p_ask = sub.add_parser("ask", help="Answer a question from a person as of ISO time")
    p_ask.add_argument("--person", required=True)
    p_ask.add_argument("--at", required=True)
    p_ask.add_argument("--ask", required=True)

    p_forget = sub.add_parser("forget", help="Forget information for a person")
    p_forget.add_argument("--person", required=True)
    p_forget.add_argument("--what", required=True)

    p_audit = sub.add_parser("audit", help="Optional: list remembered items for a person")
    p_audit.add_argument("--person", required=True)

    return parser


def handle_ingest(day: int) -> dict[str, Any]:
    return ingest_day(day)


def handle_brief(person: str, date: str) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        return generate_briefs_for_person_date(conn, person, date)
    finally:
        conn.close()


def handle_ask(person: str, at: str, ask_text: str) -> dict[str, Any]:
    conn = get_connection()
    try:
        return answer_question(conn, person, at, ask_text)
    finally:
        conn.close()


def handle_forget(person: str, what: str) -> dict[str, Any]:
    conn = get_connection()
    try:
        forget_for_person(conn, person, what)
        return {"ok": True}
    finally:
        conn.close()


def handle_audit(person: str) -> dict[str, Any]:
    conn = get_connection()
    try:
        return audit_for_person(conn, person)
    finally:
        conn.close()


def _fallback_for_cmd(cmd: str | None) -> Any:
    if cmd == "brief":
        return []
    if cmd == "ask":
        return {
            "answer": None,
            "matched": [],
            "clarify": None,
            "refused": None,
            "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "ms": 0},
        }
    if cmd == "audit":
        return {
            "person": "",
            "claims": [],
            "preferences": {},
            "tasks": [],
            "promises": [],
            "personal_notes": [],
        }
    return {"ok": False}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "ingest":
            out = handle_ingest(args.day)
        elif args.cmd == "brief":
            out = handle_brief(args.person, args.date)
        elif args.cmd == "ask":
            out = handle_ask(args.person, args.at, args.ask)
        elif args.cmd == "forget":
            out = handle_forget(args.person, args.what)
        elif args.cmd == "audit":
            out = handle_audit(args.person)
        else:
            out = {"ok": False}
    except Exception as exc:
        print(f"[assist] unhandled error in {args.cmd}: {exc}", file=sys.stderr)
        out = _fallback_for_cmd(args.cmd)

    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0

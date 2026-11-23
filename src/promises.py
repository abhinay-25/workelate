"""English and Hinglish promise extraction, author-zone due dating, and delivery closure."""

import re
import sqlite3
from typing import Any

from src.llm import LLMClient
from src.security import (
    _get_forgotten_entries,
    _text_matches_forgotten,
    get_current_day,
    get_person_org,
    visible_records_params,
    visible_records_where_clause,
)
from src.time_utils import DEFAULT_ZONE, resolve_relative_due_date
from src.usage import ASK_TOKEN_LIMIT, UsageTracker

_HINGLISH_TIME = (
    r"(?:parso|parson|kal|aaj\s+eod|aaj|sha?am|"
    r"next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
)

_DEADLINE_PHRASE = (
    r"(?:parso\s+(?:tak|ko)|parson\s+(?:tak|ko)|kal\s+(?:tak|ko)|"
    r"aaj\s+eod\s+tak|aaj\s+tak|eod\s+tak|sha?am\s+tak|"
    r"next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+(?:tak|ko)|"
    r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+(?:tak|ko)|"
    r"(?:by|before)\s+(?:eod\s+today|tomorrow|today|eod|tonight|day\s+after\s+tomorrow|"
    r"next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*)|"
    r"next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"eod\s+today|tomorrow|today|tonight|"
    r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))"
)

# English & mixed commitments: "Will share X by EOD today." / "I'll send it by tomorrow."
_EN_PROMISE_RE = re.compile(
    r"(?:^|[.!?\n]\s*|(?:hi|hello|hey)\s+[^,\n]+,\s*|\b(?:haan|ok|okay|sure),?\s*)"
    r"(?:(?:i|we)(?:['’]ll|\s+will|\s+am\s+going\s+to|\s*'m\s+going\s+to)|will)\s+"
    r"(?:share|send|deliver|provide|complete|finish|submit|update|upload|prepare|wrap\s+up)\s+"
    r"(.+?)\s+"
    r"(" + _DEADLINE_PHRASE + r")"
    r"(?=[.!?,;\n]|$)",
    re.IGNORECASE,
)

# Hinglish commitments with deliverable before deadline:
# "the checkout copy kal tak bhej dunga" / "API spec Friday tak complete kar dunga"
_HINGLISH_PROMISE_RE = re.compile(
    r"^(.+?)\s+"
    r"(" + _HINGLISH_TIME + r"\s+(?:tak|ko))\s+"
    r"(?:bhej|share|send|de|complete(?:\s+kar)?|finish(?:\s+kar)?|submit(?:\s+kar)?|upload(?:\s+kar)?|update(?:\s+kar)?|kar)\s*"
    r"(?:dunga|dungi|denge|deta\s+hoon|deti\s+hoon|karunga|karungi|rakhunga)?\b",
    re.IGNORECASE,
)

# Hinglish commitments starting directly with deadline:
# "Kal tak bhej dungi." / "Friday tak kar dunga." / "Aaj EOD tak share kar deta hoon." / "Monday ko complete kar dunga."
_HINGLISH_FRONTED_RE = re.compile(
    r"^(?:(?:haan|ok|okay|sure|yes|ji|main|hum|i)\s*,?\s*)?"
    r"(" + _HINGLISH_TIME + r"\s+(?:tak|ko))\s+"
    r"(?:(.+?)\s+)??"
    r"((?:bhej|share|send|de|complete|finish|submit|upload|update)(?:\s+kar)?|kar)\s*"
    r"(?:dunga|dungi|denge|deta\s+hoon|deti\s+hoon|karunga|karungi|rakhunga)\b",
    re.IGNORECASE,
)

_NON_COMMITMENT_CLAUSE_RE = re.compile(
    r"\b(?:timesheets\s+by\s+eod|kal\s+tak\s+final\s+ho\s+jayega|"
    r"milte\s+hain|party\s+hai|office\s+band|"
    r"please\s+(?:send|share|submit|complete|finish)|kindly\s+|reminder\s*:|"
    r"don'?t\s+forget|bhejoge|karoge|doge)\b",
    re.IGNORECASE,
)

_PRONOUN_DELIVERABLES = frozenset({"it", "this", "that", "same", "ye", "yeh", "woh", "isko", "usko"})

_PROMISE_CUE_RE = re.compile(
    r"\b(?:dunga|dungi|denge|deta\s+hoon|deti\s+hoon|karunga|karungi|bhejunga|bhejungi|"
    r"i['’]ll|i\s+will|we['’]ll|we\s+will|promise|commit)\b",
    re.IGNORECASE,
)
_DEADLINE_CUE_RE = re.compile(
    r"\b(?:kal|aaj|parso|parson|eod|tomorrow|today|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)

# Delivery confirmation: "Sharing the data dictionary as promised."
_DELIVERY_RE = re.compile(
    r"\bsharing\s+(.+?)\s+as\s+promised\b",
    re.IGNORECASE,
)

_COMPLETED_DELIVERY_CUE_RE = re.compile(
    r"\b(?:attached|attaching|shared|sent|sending|uploaded|delivered|here\s+is|here(?:'s|\s+are)|"
    r"completed|finalized|bhej\s+d(?:i|ee|iy[ae])|share\s+kar\s+d(?:i|ee|iy[ae])|upload\s+kar\s+d(?:i|ee|iy[ae]))\b",
    re.IGNORECASE,
)

_NON_DELIVERY_GUARD_RE = re.compile(
    r"\b(?:waiting\s+on|still\s+need|pending|overdue|blocked|haven['’]t|have\s+not|"
    r"not\s+yet|will\s+(?:send|share|deliver|upload)|tomorrow|kal\s+tak|by\s+friday|by\s+monday)\b",
    re.IGNORECASE,
)


def normalize_deliverable(what: str) -> str:
    """Normalize a deliverable phrase for matching promises to deliveries."""
    txt = " ".join((what or "").strip().lower().split())
    txt = re.sub(r"^(?:the|a|an)\s+", "", txt)
    txt = re.sub(r"[.!?,:;]+$", "", txt).strip()
    return txt


def _clean_subject_context(subject: str | None) -> str:
    txt = " ".join((subject or "").strip().split())
    txt = re.sub(r"^(?:re|fwd|fw)\s*:\s*", "", txt, flags=re.IGNORECASE).strip()
    return txt


def _resolve_implicit_deliverable(
    what: str | None,
    clauses: list[str],
    clause_idx: int,
    subject: str | None,
    action_verb: str | None = None,
) -> str:
    cleaned = normalize_deliverable(what or "")
    if what and cleaned and cleaned not in _PRONOUN_DELIVERABLES:
        return what.strip()
    if clause_idx > 0:
        prev = clauses[clause_idx - 1].strip().rstrip(".!?")
        if prev and not _NON_COMMITMENT_CLAUSE_RE.search(prev) and "?" not in prev:
            m_obj = re.search(
                r"\b(?:on|for|about|reviewing|finalizing|preparing|sending|sharing|checking|the)\s+([A-Za-z0-9][A-Za-z0-9 _-]{2,45})$",
                prev,
                re.IGNORECASE,
            )
            if m_obj:
                return m_obj.group(1).strip()
            if len(prev.split()) <= 8:
                return prev
    subj = _clean_subject_context(subject)
    if subj:
        return subj
    if action_verb:
        return f"promised update ({action_verb.lower()})"
    return "promised deliverable"


def _is_interrogative_or_non_commitment(clause: str) -> bool:
    txt = (clause or "").strip()
    if not txt or "?" in txt:
        return True
    if re.match(r"^(?:kya|can\s+you|could\s+you|will\s+you|did\s+you|have\s+you|would\s+you)\b", txt, re.IGNORECASE):
        return True
    if _NON_COMMITMENT_CLAUSE_RE.search(txt):
        return True
    return False


def extract_promise_from_text(sanitized_body: str, subject: str | None = None) -> tuple[str, str] | None:
    """Extract (what, when_phrase) from sanitized mail/chat body, or None."""
    body = " ".join((sanitized_body or "").strip().split())
    if not body:
        return None
    low = body.lower()
    if "timesheets by eod" in low or "kal tak final ho jayega" in low:
        return None

    # Split multi-sentence text so questions/context before a commitment don't pollute deliverable
    clauses = [c.strip() for c in re.split(r"(?<=[.!?])\s+", (sanitized_body or "").strip()) if c.strip()]
    for idx, clause in enumerate(clauses):
        if _is_interrogative_or_non_commitment(clause):
            continue

        m_en = _EN_PROMISE_RE.search(clause)
        if m_en:
            raw_what = m_en.group(1).strip()
            when = m_en.group(2).strip()
            if when:
                resolved_what = _resolve_implicit_deliverable(raw_what, clauses, idx, subject, "send")
                return resolved_what, when

        m_front = _HINGLISH_FRONTED_RE.search(clause)
        if m_front:
            when = m_front.group(1).strip()
            mid_what = (m_front.group(2) or "").strip()
            verb = (m_front.group(3) or "update").strip()
            # Filter leading pronouns/fillers from mid_what
            if mid_what.lower() in {"main", "hum", "i", "we", "sab", "yeh", "ye"}:
                mid_what = ""
            resolved_what = _resolve_implicit_deliverable(mid_what, clauses, idx, subject, verb)
            return resolved_what, when

        m_hi = _HINGLISH_PROMISE_RE.search(clause)
        if m_hi:
            raw_what = re.sub(r"^(?:main|i|we)\s+", "", m_hi.group(1).strip(), flags=re.IGNORECASE).strip()
            when = m_hi.group(2).strip()
            if raw_what and when and normalize_deliverable(raw_what) not in {"tomorrow", "friday", "monday", "kal"}:
                resolved_what = _resolve_implicit_deliverable(raw_what, clauses, idx, subject, "share")
                return resolved_what, when

    return None


def extract_promise_with_llm_fallback(
    rec: Any,
    zone: str,
    llm_client: LLMClient | None = None,
) -> tuple[str, str, str] | None:
    """Deterministic-first promise extractor with selective LLM fallback returning (what, when_phrase, due_date)."""
    body = str(rec["sanitized_body"] or "")
    subject = str(rec["subject"] or "") if "subject" in rec.keys() else ""
    at_utc = str(rec["at_utc"])

    det = extract_promise_from_text(body, subject=subject)
    if det:
        what, when_phrase = det
        due_date = resolve_relative_due_date(when_phrase, at_utc, zone)
        if due_date:
            return what, when_phrase, due_date
        return None

    # Selective LLM fallback only when text contains both commitment & deadline cues
    if _is_interrogative_or_non_commitment(body):
        return None
    if not (_PROMISE_CUE_RE.search(body) and _DEADLINE_CUE_RE.search(body)):
        return None

    llm = llm_client or LLMClient()
    if not llm.configured_providers():
        return None

    tracker = UsageTracker()
    rid = str(rec["id"]) if "id" in rec.keys() else ""
    parsed = llm.call_json(
        "Determine if this workplace message contains a genuine first-person commitment with a deadline. "
        "Reject questions, reminders to others, casual social statements, or passive status notes. "
        'Return JSON only: {"is_genuine_commitment": bool, "what": str, "when_phrase": str}',
        f'<workplace_record id="{rid}" at="{at_utc}" subject="{subject}">\n{body}\n</workplace_record>',
        tracker,
        max_input_tokens=ASK_TOKEN_LIMIT,
    )
    if not isinstance(parsed, dict) or parsed.get("is_genuine_commitment") is not True:
        return None
    what = str(parsed.get("what") or "").strip()
    when_phrase = str(parsed.get("when_phrase") or "").strip()
    if not what or not when_phrase:
        return None
    due_date = resolve_relative_due_date(when_phrase, at_utc, zone)
    if not due_date:
        return None
    return what, when_phrase, due_date


def extract_delivery_from_text(sanitized_body: str) -> str | None:
    """Extract delivered item phrase from a 'Sharing <X> as promised' record."""
    body = " ".join((sanitized_body or "").strip().split())
    if not body:
        return None
    m = _DELIVERY_RE.search(body)
    if m:
        return m.group(1).strip()
    return None


def _match_open_promise_delivery(
    sanitized_body: str,
    subject: str | None,
    open_norms: list[str],
) -> list[str]:
    """Match natural completed-delivery messages (e.g. 'Attached X', 'Sent X') against open promises."""
    if not open_norms:
        return []
    body = " ".join((sanitized_body or "").strip().split())
    if not body or "?" in body:
        return []
    if _is_interrogative_or_non_commitment(body):
        return []
    if _PROMISE_CUE_RE.search(body) or extract_promise_from_text(body, subject=subject):
        return []
    if _NON_DELIVERY_GUARD_RE.search(body):
        return []
    if not _COMPLETED_DELIVERY_CUE_RE.search(body):
        return []

    blob = f"{subject or ''} {body}".lower()
    matched: list[str] = []
    for wnorm in open_norms:
        if len(wnorm) < 4:
            continue
        if re.search(r"(?<![a-z0-9])" + re.escape(wnorm) + r"(?![a-z0-9])", blob):
            matched.append(wnorm)
    return matched


def rebuild_promises(conn: sqlite3.Connection, llm_client: LLMClient | None = None) -> None:
    """Recompute all promises and delivery closures chronologically from active records."""
    conn.execute("DELETE FROM promises")

    zones = {
        str(r["person_id"]): str(r["zone"] or DEFAULT_ZONE)
        for r in conn.execute("SELECT person_id, zone FROM people").fetchall()
    }

    rows = conn.execute(
        """
        SELECT * FROM records
        WHERE is_forgotten = 0
          AND is_newsletter = 0
          AND is_boilerplate = 0
          AND type IN ('mail', 'chat')
          AND client IS NOT NULL
          AND (has_injection_span = 0 OR TRIM(COALESCE(sanitized_body, '')) != '')
        ORDER BY at_utc ASC, day ASC, line_no ASC
        """
    ).fetchall()

    # Keep track of open promises keyed by (org, client, what_norm)
    open_by_key: dict[tuple[str, str, str], list[int]] = {}

    for rec in rows:
        org = str(rec["org"])
        client = str(rec["client"])
        rid = str(rec["id"])
        day = int(rec["day"])
        at_utc = str(rec["at_utc"])
        author = str(rec["author"])
        body = str(rec["sanitized_body"] or "")
        vis = str(rec["visibility"] or "team")
        readers_json = rec["readers_json"]

        # 1. Check if this record fulfills an earlier open promise
        delivered_what = extract_delivery_from_text(body)
        if delivered_what:
            dnorm = normalize_deliverable(delivered_what)
            pids = open_by_key.get((org, client, dnorm), [])
            if pids:
                for pid in pids:
                    conn.execute(
                        """
                        UPDATE promises
                        SET status = 'fulfilled',
                            fulfilled_by_record_id = ?,
                            fulfilled_at_utc = ?
                        WHERE promise_id = ?
                        """,
                        (rid, at_utc, pid),
                    )
                open_by_key[(org, client, dnorm)] = []
            continue

        client_open_norms = [
            wn for (o, c, wn), pids in open_by_key.items()
            if o == org and c == client and pids
        ]
        matched_norms = _match_open_promise_delivery(body, rec["subject"], client_open_norms)
        if matched_norms:
            for dnorm in matched_norms:
                for pid in open_by_key.get((org, client, dnorm), []):
                    conn.execute(
                        """
                        UPDATE promises
                        SET status = 'fulfilled',
                            fulfilled_by_record_id = ?,
                            fulfilled_at_utc = ?
                        WHERE promise_id = ?
                        """,
                        (rid, at_utc, pid),
                    )
                open_by_key[(org, client, dnorm)] = []
            continue

        # 2. Check if this record creates a new promise
        zone = zones.get(author, DEFAULT_ZONE)
        resolved = extract_promise_with_llm_fallback(rec, zone, llm_client=llm_client)
        if resolved:
            what, _when_phrase, due_date = resolved
            wnorm = normalize_deliverable(what)
            cur = conn.execute(
                """
                INSERT INTO promises(
                    record_id, day, at_utc, org, client, owner, what, what_norm,
                    due_date, status, visibility, readers_json, is_forgotten
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, 0)
                """,
                (rid, day, at_utc, org, client, author, what, wnorm, due_date, vis, readers_json),
            )
            open_by_key.setdefault((org, client, wnorm), []).append(int(cur.lastrowid))


def get_open_promises(
    conn: sqlite3.Connection,
    person_id: str,
    client_id: str | None = None,
    upto_day: int | None = None,
    upto_utc: str | None = None,
) -> list[dict[str, Any]]:
    """Return visible promises still open as of (upto_day, upto_utc)."""
    org = get_person_org(conn, person_id)
    if not org:
        return []
    day_lim = get_current_day(conn) if upto_day is None else upto_day
    where = visible_records_where_clause("r", with_utc_cutoff=(upto_utc is not None))
    params = visible_records_params(person_id, org, day_lim, upto_utc)

    sql = f"""
        SELECT p.*, df.day AS fulfilled_day, df.at_utc AS fulfilled_rec_at_utc
        FROM promises p
        JOIN records r ON r.id = p.record_id
        LEFT JOIN records df ON df.id = p.fulfilled_by_record_id AND df.is_forgotten = 0
        WHERE p.is_forgotten = 0 AND {where}
    """
    if client_id is not None:
        sql += " AND p.client = :cid"
        params["cid"] = client_id
    sql += " ORDER BY p.at_utc ASC, p.promise_id ASC"

    kw_groups, forgotten_ids = _get_forgotten_entries(conn, person_id)
    out: list[dict[str, Any]] = []
    for row in conn.execute(sql, params).fetchall():
        rid = str(row["record_id"])
        if rid in forgotten_ids or _text_matches_forgotten(str(row["what"] or ""), kw_groups):
            continue
        f_day = row["fulfilled_day"]
        f_at = row["fulfilled_rec_at_utc"]
        is_fulfilled_as_of = False
        if row["status"] == "fulfilled" and f_day is not None:
            if int(f_day) <= day_lim and (upto_utc is None or str(f_at) <= upto_utc):
                is_fulfilled_as_of = True
        if is_fulfilled_as_of:
            continue
        out.append(
            {
                "owner": str(row["owner"]),
                "what": str(row["what"]),
                "what_norm": str(row["what_norm"]),
                "due": str(row["due_date"]),
                "sources": [rid],
                "client": row["client"],
                "at_utc": str(row["at_utc"]),
            }
        )
    return out


def get_deliveries_in_window(
    conn: sqlite3.Connection,
    person_id: str,
    client_id: str,
    since_utc: str,
    upto_day: int | None = None,
    upto_utc: str | None = None,
) -> list[dict[str, Any]]:
    """Return visible delivery records ('Sharing X as promised') in (since_utc, upto_utc]."""
    org = get_person_org(conn, person_id)
    if not org:
        return []
    day_lim = get_current_day(conn) if upto_day is None else upto_day
    where = visible_records_where_clause("r", with_utc_cutoff=(upto_utc is not None))
    params = visible_records_params(person_id, org, day_lim, upto_utc)
    params["cid"] = client_id
    params["since_utc"] = since_utc

    rows = conn.execute(
        f"""
        SELECT r.*
        FROM records r
        WHERE r.type IN ('mail', 'chat')
          AND r.client = :cid
          AND r.is_newsletter = 0
          AND r.is_boilerplate = 0
          AND r.at_utc > :since_utc
          AND {where}
        ORDER BY r.at_utc ASC
        """,
        params,
    ).fetchall()

    deliveries: list[dict[str, Any]] = []
    for r in rows:
        what = extract_delivery_from_text(str(r["sanitized_body"] or ""))
        if what:
            deliveries.append(
                {
                    "author": str(r["author"]),
                    "what": what,
                    "what_norm": normalize_deliverable(what),
                    "record_id": str(r["id"]),
                    "at_utc": str(r["at_utc"]),
                }
            )
    return deliveries

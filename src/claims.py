"""Assertion extraction, temporal expiry, and conflict resolution strictly via explicit challenge rules."""

import json
import re
import sqlite3
from typing import Any

from src.llm import LLMClient
from src.security import (
    _get_forgotten_entries,
    _text_matches_forgotten,
    can_view,
    get_current_day,
    get_person_org,
)
from src.time_utils import MONTHS, parse_explicit_date, utc_to_local_dt
from src.usage import ASK_TOKEN_LIMIT, UsageTracker

# Ownership handover: "From this week Alex takes over client billing from Jordan."
_TAKES_OVER_RE = re.compile(
    r"(?:from\s+this\s+week\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+"
    r"takes\s+over\s+(.+?)\s+from\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)"
    r"(?=[.!?\n]|$)",
    re.IGNORECASE,
)

# Declarative ownership: "Alex owns client billing." (never matched on interrogative clauses)
_OWNS_DECL_RE = re.compile(
    r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:now\s+)?owns\s+(.+?)(?=[.!\n]|$)",
    re.IGNORECASE,
)

# Launch date change: "move the launch to 14 Nov" / "launch moves to 14 Nov"
_LAUNCH_MOVE_RE = re.compile(
    r"\b(?:move(?:d|s)?\s+(?:the\s+)?launch(?:\s+date)?\s+to|launch(?:\s+date)?\s+(?:moves|moved|is)\s+(?:to|on))\s+"
    r"(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+(?:\s+\d{4})?|[A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?(?:\s+\d{4})?)",
    re.IGNORECASE,
)

_CORR_PREFIX_RE = re.compile(
    r"^(?:(?:no|actually|correction|update|fyi)\s*[,:\-]\s*)+",
    re.IGNORECASE,
)

_ACTIVE_CORR_RE = re.compile(
    r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:now\s+)?"
    r"(?:owns|handles|is\s+(?:now\s+)?(?:responsible\s+for|handling|managing|owning)|takes\s+care\s+of|manages)\s+"
    r"(.+?)(?:\s+now)?(?:\s*[,;(]?\s*(?:not|instead\s+of|rather\s+than|replacing|was)\s+"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\)?)?(?:[.!]|$)",
    re.IGNORECASE,
)

_PASSIVE_CORR_RE = re.compile(
    r"^(?:the\s+)?(.+?)\s+is\s+(?:now\s+)?(?:handled|owned|managed|run|led)\s+by\s+"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)"
    r"(?:\s*[,;(]?\s*(?:not|instead\s+of|rather\s+than|replacing|was)\s+"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\)?)?(?:[.!]|$)",
    re.IGNORECASE,
)

_NO_LONGER_HANDOVER_RE = re.compile(
    r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+no\s+longer\s+"
    r"(?:owns|handles|manages|is\s+responsible\s+for)\s+([^;,.!?]+?)\s*"
    r"(?:;|,|\band\b)\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+"
    r"(?:took\s+over|takes\s+over|handles|owns|manages|is\s+(?:now\s+)?(?:handling|taking\s+over))\b",
    re.IGNORECASE,
)

_SWITCHED_OWNER_RE = re.compile(
    r"^(?:we\s+)?(?:switched|moved|transferred|reassigned|handed)\s+(.+?)\s+"
    r"from\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+to\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)(?:[.!;]|$)",
    re.IGNORECASE,
)

_DATE_CORR_RE = re.compile(
    r"^(?:the\s+)?(?:([A-Za-z0-9 _-]+?)\s+)?(?:launch\s+date|launch)\s+"
    r"(?:is\s+(?:now\s+)?|moved\s+to\s+|pushed\s+to\s+|updated\s+to\s+|on\s+)"
    r"(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+(?:\s+\d{4})?|[A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?(?:\s*,?\s*\d{4})?)"
    r"(?:\s*[,;(]?\s*(?:not|instead\s+of|rather\s+than|was|from)\s+"
    r"(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+(?:\s+\d{4})?|[A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?(?:\s*,?\s*\d{4})?)\)?)?(?:[.!]|$)",
    re.IGNORECASE,
)

_HEDGED_PHRASE_RE = re.compile(
    r"\b(?:i\s+think|i\s+believe|i\s+guess|maybe|perhaps|probably|not\s+sure\s+if|"
    r"not\s+certain|rumor\s+is|i\s+heard|someone\s+said|assuming|wonder\s+if|might\s+be|supposedly)\b",
    re.IGNORECASE,
)

_STRONG_CORRECTION_CUE_RE = re.compile(
    r"(?:^no,\s+|\b(?:correction|actually|instead\s+of|no\s+longer|replaced\s+by|replacing|transferred\s+to|switched\s+from)\b)",
    re.IGNORECASE,
)

# Leave interval: "I'm on leave from 9 Nov till the 12th." or "Jordan is on leave till the 12th."
_LEAVE_RANGE_RE = re.compile(
    r"\b(?:on\s+leave|ooo|out\s+of\s+office)\b.*?\bfrom\s+(.+?)\s+(?:till|until|to|-|–)\s+(.+?)(?=[.!?\n]|$)",
    re.IGNORECASE,
)
_LEAVE_UNTIL_RE = re.compile(
    r"\b(?:on\s+leave|ooo|out\s+of\s+office)\b.*?\b(?:till|until|through)\s+(.+?)(?=[.!?\n]|$)",
    re.IGNORECASE,
)

# Travel range: "Planning Dubai trip 20–24 Nov with family."
_TRAVEL_RANGE_RE = re.compile(
    r"\b(?:planning\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+trip\s+"
    r"(\d{1,2})\s*[–-]\s*(\d{1,2})\s+"
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
    re.IGNORECASE,
)

_DECISION_LINE_RE = re.compile(r"^Decision\s*:\s*(.+)$", re.IGNORECASE)


def is_managerial_role(role: str | None) -> bool:
    """Return True if role represents a manager / lead / founder under the challenge ownership rule."""
    low = (role or "").strip().lower()
    return any(tok in low for tok in ("manager", "lead", "founder", "head", "director"))


def is_intern_role(role: str | None) -> bool:
    """Return True if role is explicitly intern."""
    return (role or "").strip().lower() == "intern"


def is_interrogative_clause(clause: str) -> bool:
    """Return True if a clause is a question rather than a declarative assertion."""
    txt = (clause or "").strip()
    if "?" in txt:
        return True
    if re.search(r"\bright\b\s*$", txt, re.IGNORECASE):
        return True
    return False


def is_unverified_or_hedged_clause(clause: str) -> bool:
    """Return True if a clause is interrogative or hedged/speculative rather than factual."""
    if is_interrogative_clause(clause):
        return True
    if _HEDGED_PHRASE_RE.search(clause or ""):
        return True
    return False


def _normalize_client_and_topic(
    conn: sqlite3.Connection,
    org: str,
    default_client: str | None,
    topic_raw: str,
) -> tuple[str | None, str, str]:
    """Resolve client override inside topic_raw and return (client_id, pred_topic, topic_clean)."""
    client_id = default_client
    topic = " ".join((topic_raw or "").strip().lower().split())
    topic = re.sub(r"\s+(?:ownership|responsibility)$", "", topic).strip()

    candidates: list[tuple[str, str]] = []
    for crow in conn.execute("SELECT client_id, name FROM clients WHERE org_id = ?", (org,)).fetchall():
        cid = str(crow["client_id"])
        cname = str(crow["name"]).lower()
        cfirst = cname.split()[0] if cname.split() else cid
        candidates.append((cid, cid))
        if cname:
            candidates.append((cname, cid))
        if cfirst:
            candidates.append((cfirst, cid))
    for arow in conn.execute(
        "SELECT alias, entity_id FROM entity_aliases WHERE org_id = ? AND entity_type = 'client' AND confidence >= 0.8",
        (org,),
    ).fetchall():
        alias = str(arow["alias"]).lower()
        if len(alias) >= 2:
            candidates.append((alias, str(arow["entity_id"])))

    candidates.sort(key=lambda kv: len(kv[0]), reverse=True)
    for alias, cid in candidates:
        m_for = re.search(r"\bfor\s+" + re.escape(alias) + r"\b", topic)
        if m_for:
            client_id = client_id or cid
            topic = (topic[: m_for.start()] + " " + topic[m_for.end() :]).strip()
            break
        if topic.startswith(alias + " "):
            client_id = client_id or cid
            topic = topic[len(alias) + 1 :].strip()
            break
        if topic == alias:
            client_id = client_id or cid
            break

    topic = re.sub(r"^(?:the|for|on|of)\s+", "", topic).strip()
    if "billing" in topic or "invoice" in topic:
        pred_topic = "billing_owner"
    elif "launch" in topic:
        pred_topic = "launch_date"
    else:
        pred_topic = re.sub(r"[^a-z0-9]+", "_", topic).strip("_") or "ownership"
    return client_id, pred_topic, topic


def extract_correction_from_clause(
    conn: sqlite3.Connection,
    org: str,
    default_client: str | None,
    channel: str | None,
    clause: str,
    local_year: int,
    local_month: int,
    allow_unresolved_names: bool = False,
) -> dict[str, Any] | None:
    """Deterministically parse natural-language ownership or date corrections from a clause."""
    raw = (clause or "").strip()
    if not raw or is_unverified_or_hedged_clause(raw):
        return None

    stripped = _CORR_PREFIX_RE.sub("", raw).strip()
    if not stripped:
        return None

    # 1. Date correction: "launch date is 18 Nov 2026, not 14 Nov" / "launch moved to 18 Nov (was 14 Nov)"
    m_date = _DATE_CORR_RE.search(stripped)
    if m_date:
        client_hint = m_date.group(1) or ""
        new_raw = m_date.group(2).strip()
        old_raw = m_date.group(3).strip() if m_date.group(3) else None
        cid, _, _ = _normalize_client_and_topic(conn, org, default_client, client_hint)
        new_iso = parse_explicit_date(new_raw, local_year, local_month)
        old_iso = parse_explicit_date(old_raw, local_year, local_month) if old_raw else None
        if new_iso:
            subj = f"{cid}:launch_date" if cid else "launch_date"
            return {
                "client": cid,
                "subject_key": subj,
                "predicate": "date",
                "object_val": new_iso,
                "old_val": old_iso,
                "raw_new": new_iso,
                "raw_old": old_iso,
                "topic_clean": "launch",
            }

    # 2. "X no longer handles billing; Y took over" / "switched billing from X to Y" / passive / active
    new_name: str | None = None
    old_name: str | None = None
    topic_raw: str | None = None

    m_nl = _NO_LONGER_HANDOVER_RE.search(stripped)
    if m_nl:
        old_name, topic_raw, new_name = m_nl.group(1).strip(), m_nl.group(2).strip(), m_nl.group(3).strip()
    else:
        m_sw = _SWITCHED_OWNER_RE.search(stripped)
        if m_sw:
            topic_raw, old_name, new_name = m_sw.group(1).strip(), m_sw.group(2).strip(), m_sw.group(3).strip()
        else:
            m_pas = _PASSIVE_CORR_RE.search(stripped)
            if m_pas:
                topic_raw = m_pas.group(1).strip()
                new_name = m_pas.group(2).strip()
                old_name = m_pas.group(3).strip() if m_pas.group(3) else None
            else:
                m_act = _ACTIVE_CORR_RE.search(stripped)
                if m_act:
                    new_name = m_act.group(1).strip()
                    topic_raw = m_act.group(2).strip()
                    old_name = m_act.group(3).strip() if m_act.group(3) else None

    if not new_name or not topic_raw:
        return None

    cid, pred_topic, topic_clean = _normalize_client_and_topic(conn, org, default_client, topic_raw)
    new_pid, new_full = _resolve_person_in_org(conn, org, new_name, cid, channel, raw)
    if new_pid is None and not allow_unresolved_names:
        return None
    old_pid, old_full = (
        _resolve_person_in_org(conn, org, old_name, cid, channel, raw) if old_name else (None, None)
    )
    subj = f"{cid}:{pred_topic}" if cid else pred_topic
    obj_val = f"{new_pid}:{new_full}" if new_pid else new_full
    old_val = f"{old_pid}:{old_full}" if (old_pid and old_full) else old_full
    return {
        "client": cid,
        "subject_key": subj,
        "predicate": "owner",
        "object_val": obj_val,
        "old_val": old_val,
        "raw_new": new_full or new_name,
        "raw_old": old_full or old_name,
        "topic_clean": topic_clean,
    }


def _strip_parenthetical(val: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", val or "").strip()


def _resolve_person_in_org(
    conn: sqlite3.Connection,
    org_id: str,
    name_phrase: str,
    client_id: str | None,
    channel: str | None,
    body: str,
) -> tuple[str | None, str]:
    """Resolve a person name inside org_id using exact full name or role/team alignment."""
    target = _strip_parenthetical(name_phrase).strip()
    if not target:
        return None, ""
    rows = conn.execute("SELECT * FROM people WHERE org_id = ?", (org_id,)).fetchall()
    for r in rows:
        if r["name"].lower() == target.lower():
            return str(r["person_id"]), str(r["name"])

    cands = [r for r in rows if r["first_name"].lower() == target.lower()]
    if len(cands) == 1:
        return str(cands[0]["person_id"]), str(cands[0]["name"])
    if len(cands) > 1:
        low_ctx = f"{channel or ''} {body}".lower()
        scored: list[tuple[int, sqlite3.Row]] = []
        for c in cands:
            sc = 0
            team = str(c["team"] or "").lower()
            role = str(c["role"] or "").lower()
            if team and team in low_ctx:
                sc += 3
            if ("billing" in low_ctx or "invoice" in low_ctx) and ("billing" in role or "accounts" in team):
                sc += 3
            scored.append((sc, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        if scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            return str(scored[0][1]["person_id"]), str(scored[0][1]["name"])
    return None, target


def _insert_claim(
    conn: sqlite3.Connection,
    *,
    org: str,
    client: str | None,
    subject_key: str,
    predicate: str,
    object_val: str,
    old_val: str | None,
    source_kind: str,
    author_role: str | None,
    effective_at_utc: str,
    at_utc: str,
    day: int,
    source_record_id: str | None,
    feedback_id: int | None,
    visibility: str,
    author: str,
    readers_json: str | None,
    valid_from: str | None = None,
    valid_until: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO claims(
            org, client, subject_key, predicate, object_val, old_val,
            source_kind, author_role, effective_at_utc, at_utc, day,
            source_record_id, feedback_id, visibility, author, readers_json,
            valid_from, valid_until, is_forgotten
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            org,
            client,
            subject_key,
            predicate,
            object_val,
            old_val,
            source_kind,
            author_role,
            effective_at_utc,
            at_utc,
            day,
            source_record_id,
            feedback_id,
            visibility,
            author,
            readers_json,
            valid_from,
            valid_until,
        ),
    )


def rebuild_claims(conn: sqlite3.Connection, llm_client: LLMClient | None = None) -> None:
    """Recompute all record-derived claims while preserving persisted feedback_correction claims."""
    conn.execute("DELETE FROM claims WHERE source_kind != 'feedback_correction'")

    people_map = {
        str(r["person_id"]): r
        for r in conn.execute("SELECT * FROM people").fetchall()
    }

    # 1. Extract claims from doc_versions (with field carryover effective_at_utc)
    doc_rows = conn.execute(
        """
        SELECT dv.*, r.visibility, r.readers_json
        FROM doc_versions dv
        JOIN records r ON r.id = dv.record_id
        WHERE dv.is_forgotten = 0 AND r.is_forgotten = 0
        ORDER BY dv.at_utc ASC
        """
    ).fetchall()

    for dv in doc_rows:
        org = str(dv["org"])
        client = dv["client"]
        rid = str(dv["record_id"])
        day = int(dv["day"])
        at_utc = str(dv["at_utc"])
        author = str(dv["author"])
        author_role = str(people_map[author]["role"]) if author in people_map else ""
        vis = str(dv["visibility"] or "team")
        readers_json = dv["readers_json"]

        try:
            fields = json.loads(dv["extracted_kv_json"] or "{}")
        except ValueError:
            fields = {}

        for fkey, fdata in fields.items():
            val = str(fdata.get("val") or "")
            eff_utc = str(fdata.get("effective_at_utc") or at_utc)
            changed_here = bool(fdata.get("changed_here"))
            source_kind = "doc_changed_field" if changed_here else "doc_carryover_field"
            old_val = fdata.get("old_val")

            if client and fkey == "launch_date":
                local_dt = utc_to_local_dt(at_utc, people_map[author]["zone"] if author in people_map else None)
                iso_date = parse_explicit_date(val, local_dt.year, local_dt.month) or val
                _insert_claim(
                    conn,
                    org=org,
                    client=client,
                    subject_key=f"{client}:launch_date",
                    predicate="date",
                    object_val=iso_date,
                    old_val=old_val,
                    source_kind=source_kind,
                    author_role=author_role,
                    effective_at_utc=eff_utc,
                    at_utc=at_utc,
                    day=day,
                    source_record_id=rid,
                    feedback_id=None,
                    visibility=vis,
                    author=author,
                    readers_json=readers_json,
                )
            elif client and fkey in ("billing_contact", "billing_owner"):
                pid, full_name = _resolve_person_in_org(conn, org, val, client, "", val)
                obj = f"{pid}:{full_name}" if pid else _strip_parenthetical(val)
                _insert_claim(
                    conn,
                    org=org,
                    client=client,
                    subject_key=f"{client}:billing_owner",
                    predicate="owner",
                    object_val=obj,
                    old_val=old_val,
                    source_kind=source_kind,
                    author_role=author_role,
                    effective_at_utc=eff_utc,
                    at_utc=at_utc,
                    day=day,
                    source_record_id=rid,
                    feedback_id=None,
                    visibility=vis,
                    author=author,
                    readers_json=readers_json,
                )
            elif client and changed_here and old_val is not None and not fkey.startswith("cell_"):
                _insert_claim(
                    conn,
                    org=org,
                    client=client,
                    subject_key=f"{client}:doc_field:{fkey}",
                    predicate="field_update",
                    object_val=val,
                    old_val=str(old_val),
                    source_kind="doc_changed_field",
                    author_role=author_role,
                    effective_at_utc=eff_utc,
                    at_utc=at_utc,
                    day=day,
                    source_record_id=rid,
                    feedback_id=None,
                    visibility=vis,
                    author=author,
                    readers_json=readers_json,
                )

    # 2. Extract claims from non-doc records (notes, mails, chats)
    recs = conn.execute(
        """
        SELECT * FROM records
        WHERE is_forgotten = 0
          AND is_newsletter = 0
          AND is_boilerplate = 0
          AND type IN ('note', 'mail', 'chat')
          AND (has_injection_span = 0 OR TRIM(COALESCE(sanitized_body, '')) != '')
        ORDER BY at_utc ASC, day ASC, line_no ASC
        """
    ).fetchall()

    for rec in recs:
        org = str(rec["org"])
        rid = str(rec["id"])
        rtype = str(rec["type"])
        day = int(rec["day"])
        at_utc = str(rec["at_utc"])
        author = str(rec["author"])
        client = rec["client"]
        vis = str(rec["visibility"] or "team")
        readers_json = rec["readers_json"]
        channel = rec["channel"]
        body = str(rec["sanitized_body"] or "")
        author_role = str(people_map[author]["role"]) if author in people_map else ""
        author_zone = str(people_map[author]["zone"]) if author in people_map else None
        local_dt = utc_to_local_dt(at_utc, author_zone)

        # Meeting notes decisions
        if rtype == "note" and client:
            for line in body.splitlines():
                m_dec = _DECISION_LINE_RE.match(line.strip())
                if m_dec:
                    dec_text = m_dec.group(1).strip().rstrip(".")
                    slug = re.sub(r"[^a-z0-9]+", "_", dec_text.lower())[:40].strip("_")
                    _insert_claim(
                        conn,
                        org=org,
                        client=client,
                        subject_key=f"{client}:decision:{slug}",
                        predicate="decision",
                        object_val=dec_text,
                        old_val=None,
                        source_kind="record_assertion",
                        author_role=author_role,
                        effective_at_utc=at_utc,
                        at_utc=at_utc,
                        day=day,
                        source_record_id=rid,
                        feedback_id=None,
                        visibility=vis,
                        author=author,
                        readers_json=readers_json,
                    )

        # Sentence-level assertions (guarding against interrogatives and hedged/unverified claims)
        for line in body.splitlines():
            for clause in re.split(r"(?<=[.!?])\s+", line.strip()):
                if not clause:
                    continue
                if is_unverified_or_hedged_clause(clause):
                    continue

                matched_clause = False

                # Ownership handover ("From this week Alex takes over client billing from Jordan.")
                m_take = _TAKES_OVER_RE.search(clause)
                if m_take:
                    matched_clause = True
                    new_name = m_take.group(1).strip()
                    topic = m_take.group(2).strip().lower()
                    old_name = m_take.group(3).strip()
                    target_client = client
                    if not target_client:
                        for c_row in conn.execute("SELECT client_id, name FROM clients WHERE org_id = ?", (org,)).fetchall():
                            if c_row["client_id"] in topic or c_row["name"].split()[0].lower() in topic:
                                target_client = str(c_row["client_id"])
                                break
                    new_pid, new_full = _resolve_person_in_org(conn, org, new_name, target_client, channel, clause)
                    old_pid, old_full = _resolve_person_in_org(conn, org, old_name, target_client, channel, clause)
                    pred_topic = "billing_owner" if ("billing" in topic or "invoice" in topic) else re.sub(r"[^a-z0-9]+", "_", topic)
                    subj = f"{target_client}:{pred_topic}" if target_client else pred_topic
                    _insert_claim(
                        conn,
                        org=org,
                        client=target_client,
                        subject_key=subj,
                        predicate="owner",
                        object_val=f"{new_pid}:{new_full}" if new_pid else new_full,
                        old_val=f"{old_pid}:{old_full}" if old_pid else old_full,
                        source_kind="record_assertion",
                        author_role=author_role,
                        effective_at_utc=at_utc,
                        at_utc=at_utc,
                        day=day,
                        source_record_id=rid,
                        feedback_id=None,
                        visibility=vis,
                        author=author,
                        readers_json=readers_json,
                    )

                if not matched_clause:
                    corr = extract_correction_from_clause(
                        conn,
                        org,
                        client,
                        channel,
                        clause,
                        local_dt.year,
                        local_dt.month,
                    )
                    if corr is not None:
                        matched_clause = True
                        _insert_claim(
                            conn,
                            org=org,
                            client=corr["client"],
                            subject_key=corr["subject_key"],
                            predicate=corr["predicate"],
                            object_val=corr["object_val"],
                            old_val=corr["old_val"],
                            source_kind="record_assertion",
                            author_role=author_role,
                            effective_at_utc=at_utc,
                            at_utc=at_utc,
                            day=day,
                            source_record_id=rid,
                            feedback_id=None,
                            visibility=vis,
                            author=author,
                            readers_json=readers_json,
                        )

                if not matched_clause:
                    m_owns = _OWNS_DECL_RE.search(clause)
                    if m_owns:
                        matched_clause = True
                        owner_name = m_owns.group(1).strip()
                        topic = m_owns.group(2).strip().lower()
                        new_pid, new_full = _resolve_person_in_org(conn, org, owner_name, client, channel, clause)
                        pred_topic = "billing_owner" if ("billing" in topic or "invoice" in topic) else re.sub(r"[^a-z0-9]+", "_", topic)
                        subj = f"{client}:{pred_topic}" if client else pred_topic
                        _insert_claim(
                            conn,
                            org=org,
                            client=client,
                            subject_key=subj,
                            predicate="owner",
                            object_val=f"{new_pid}:{new_full}" if new_pid else new_full,
                            old_val=None,
                            source_kind="record_assertion",
                            author_role=author_role,
                            effective_at_utc=at_utc,
                            at_utc=at_utc,
                            day=day,
                            source_record_id=rid,
                            feedback_id=None,
                            visibility=vis,
                            author=author,
                            readers_json=readers_json,
                        )

                # Launch date shift
                m_launch = _LAUNCH_MOVE_RE.search(clause)
                if m_launch and client and not matched_clause:
                    matched_clause = True
                    new_date = parse_explicit_date(m_launch.group(1), local_dt.year, local_dt.month)
                    if new_date:
                        _insert_claim(
                            conn,
                            org=org,
                            client=client,
                            subject_key=f"{client}:launch_date",
                            predicate="date",
                            object_val=new_date,
                            old_val=None,
                            source_kind="record_assertion",
                            author_role=author_role,
                            effective_at_utc=at_utc,
                            at_utc=at_utc,
                            day=day,
                            source_record_id=rid,
                            feedback_id=None,
                            visibility=vis,
                            author=author,
                            readers_json=readers_json,
                        )

                # Selective LLM fallback for idiomatic natural-language corrections in records
                if (
                    not matched_clause
                    and not is_intern_role(author_role)
                    and _STRONG_CORRECTION_CUE_RE.search(clause)
                ):
                    llm = llm_client or LLMClient()
                    if llm.configured_providers():
                        tracker = UsageTracker()
                        parsed = llm.call_json(
                            "Extract a verified workplace correction into JSON. Set is_hedged_or_unverified=true if uncertain/rumor/question: "
                            '{"is_correction": bool, "is_hedged_or_unverified": bool, "client": str|null, '
                            '"subject_topic": str, "predicate": "owner"|"date"|"decision", "new_val": str, "old_val": str|null}',
                            f"<workplace_record id=\"{rid}\" client=\"{client or ''}\">\n{clause}\n</workplace_record>",
                            tracker,
                            max_input_tokens=ASK_TOKEN_LIMIT,
                        )
                        if (
                            isinstance(parsed, dict)
                            and parsed.get("is_correction") is True
                            and not parsed.get("is_hedged_or_unverified")
                            and str(parsed.get("new_val") or "").strip()
                        ):
                            raw_new = str(parsed["new_val"]).strip()
                            raw_old = str(parsed.get("old_val") or "").strip() or None
                            new_toks = [t for t in re.findall(r"[a-z0-9]{2,}", raw_new.lower())]
                            if new_toks and any(t in clause.lower() for t in new_toks):
                                cid, pred_topic, _ = _normalize_client_and_topic(
                                    conn,
                                    org,
                                    client or parsed.get("client"),
                                    str(parsed.get("subject_topic") or "ownership"),
                                )
                                pred = str(parsed.get("predicate") or "owner")
                                obj_val = raw_new
                                old_val = raw_old
                                if pred == "owner":
                                    npid, nfull = _resolve_person_in_org(conn, org, raw_new, cid, channel, clause)
                                    obj_val = f"{npid}:{nfull}" if npid else nfull
                                    if raw_old:
                                        opid, ofull = _resolve_person_in_org(conn, org, raw_old, cid, channel, clause)
                                        old_val = f"{opid}:{ofull}" if opid else ofull
                                elif pred == "date":
                                    iso_n = parse_explicit_date(raw_new, local_dt.year, local_dt.month)
                                    if iso_n:
                                        obj_val = iso_n
                                    if raw_old:
                                        iso_o = parse_explicit_date(raw_old, local_dt.year, local_dt.month)
                                        if iso_o:
                                            old_val = iso_o
                                subj = f"{cid}:{pred_topic}" if cid else pred_topic
                                _insert_claim(
                                    conn,
                                    org=org,
                                    client=cid,
                                    subject_key=subj,
                                    predicate=pred,
                                    object_val=obj_val,
                                    old_val=old_val,
                                    source_kind="record_assertion",
                                    author_role=author_role,
                                    effective_at_utc=at_utc,
                                    at_utc=at_utc,
                                    day=day,
                                    source_record_id=rid,
                                    feedback_id=None,
                                    visibility=vis,
                                    author=author,
                                    readers_json=readers_json,
                                )

        # Leave / OOO intervals
        m_leave = _LEAVE_RANGE_RE.search(body)
        if m_leave and author in people_map:
            v_from = parse_explicit_date(m_leave.group(1), local_dt.year, local_dt.month)
            ref_m = int(v_from[5:7]) if v_from else local_dt.month
            v_until = parse_explicit_date(m_leave.group(2), local_dt.year, ref_m)
            if v_from and v_until:
                _insert_claim(
                    conn,
                    org=org,
                    client=client,
                    subject_key=f"{author}:availability",
                    predicate="leave",
                    object_val=body.strip(),
                    old_val=None,
                    source_kind="record_assertion",
                    author_role=author_role,
                    effective_at_utc=at_utc,
                    at_utc=at_utc,
                    day=day,
                    source_record_id=rid,
                    feedback_id=None,
                    visibility=vis,
                    author=author,
                    readers_json=readers_json,
                    valid_from=v_from,
                    valid_until=v_until,
                )
        else:
            m_l_until = _LEAVE_UNTIL_RE.search(body)
            if m_l_until and author in people_map:
                v_until = parse_explicit_date(m_l_until.group(1), local_dt.year, local_dt.month)
                if v_until:
                    _insert_claim(
                        conn,
                        org=org,
                        client=client,
                        subject_key=f"{author}:availability",
                        predicate="leave",
                        object_val=body.strip(),
                        old_val=None,
                        source_kind="record_assertion",
                        author_role=author_role,
                        effective_at_utc=at_utc,
                        at_utc=at_utc,
                        day=day,
                        source_record_id=rid,
                        feedback_id=None,
                        visibility=vis,
                        author=author,
                        readers_json=readers_json,
                        valid_from=local_dt.date().isoformat(),
                        valid_until=v_until,
                    )

        # Travel plans (e.g., private Dubai trip 20-24 Nov)
        m_trav = _TRAVEL_RANGE_RE.search(body)
        if m_trav and author in people_map:
            d1, d2 = int(m_trav.group(2)), int(m_trav.group(3))
            mon = MONTHS[m_trav.group(4).lower()]
            v_from = f"{local_dt.year:04d}-{mon:02d}-{d1:02d}"
            v_until = f"{local_dt.year:04d}-{mon:02d}-{d2:02d}"
            _insert_claim(
                conn,
                org=org,
                client=client,
                subject_key=f"{author}:travel",
                predicate="travel",
                object_val=body.strip(),
                old_val=None,
                source_kind="record_assertion",
                author_role=author_role,
                effective_at_utc=at_utc,
                at_utc=at_utc,
                day=day,
                source_record_id=rid,
                feedback_id=None,
                visibility=vis,
                author=author,
                readers_json=readers_json,
                valid_from=v_from,
                valid_until=v_until,
            )


def _visible_claims(
    conn: sqlite3.Connection,
    person_id: str,
    subject_key: str | None = None,
    client_id: str | None = None,
    upto_day: int | None = None,
    upto_utc: str | None = None,
    query_date: str | None = None,
) -> list[sqlite3.Row]:
    org = get_person_org(conn, person_id)
    if not org:
        return []
    day_lim = get_current_day(conn) if upto_day is None else upto_day

    sql = """
        SELECT c.*
        FROM claims c
        LEFT JOIN records r ON r.id = c.source_record_id
        WHERE c.org = ?
          AND c.is_forgotten = 0
          AND c.day <= ?
          AND (c.source_record_id IS NULL OR (r.is_forgotten = 0 AND (r.has_injection_span = 0 OR TRIM(COALESCE(r.sanitized_body, '')) != '')))
    """
    params: list[Any] = [org, day_lim]
    if upto_utc is not None:
        sql += " AND c.at_utc <= ?"
        params.append(upto_utc)
    if subject_key is not None:
        sql += " AND c.subject_key = ?"
        params.append(subject_key)
    if client_id is not None:
        sql += " AND c.client = ?"
        params.append(client_id)
    if query_date is not None:
        sql += " AND (c.valid_until IS NULL OR c.valid_until >= ?)"
        params.append(query_date)

    rows = conn.execute(sql, params).fetchall()
    kw_groups, forgotten_ids = _get_forgotten_entries(conn, person_id)
    visible: list[sqlite3.Row] = []
    for row in rows:
        if row["source_record_id"] and str(row["source_record_id"]) in forgotten_ids:
            continue
        blob = f"{row['subject_key']} {row['object_val']}"
        if _text_matches_forgotten(blob, kw_groups):
            continue
        if row["source_kind"] == "feedback_correction":
            visible.append(row)
            continue
        if can_view(row, person_id, org):
            visible.append(row)
    return visible


def _claim_sort_key(row: sqlite3.Row) -> tuple[int, int, str, int, str]:
    """Challenge-rule comparator (higher tuple wins):
    1. feedback_correction persists over prior/carryover records
    2. On ownership claims (predicate == 'owner'), managerial/lead beats intern
    3. Newest effective_at_utc wins (carryover fields carry v1's timestamp)
    4. Direct record_assertion beats doc_changed_field on ties so primary sources lead
    """
    is_corr = 1 if row["source_kind"] == "feedback_correction" else 0
    is_non_intern_owner = 0
    if row["predicate"] == "owner":
        role = row["author_role"]
        if is_managerial_role(role) or not is_intern_role(role):
            # Explicitly distinguish managerial/non-intern from intern on ownership claims
            is_non_intern_owner = 2 if is_managerial_role(role) else 1
        else:
            is_non_intern_owner = 0

    is_direct_record = 1 if row["source_kind"] == "record_assertion" else 0
    return (
        is_corr,
        is_non_intern_owner,
        str(row["effective_at_utc"]),
        is_direct_record,
        str(row["at_utc"]),
    )


def get_winning_claim(
    conn: sqlite3.Connection,
    person_id: str,
    subject_key: str,
    upto_day: int | None = None,
    upto_utc: str | None = None,
    query_date: str | None = None,
) -> dict[str, Any] | None:
    """Resolve the winning claim for subject_key using explicit challenge rules."""
    candidates = _visible_claims(
        conn,
        person_id,
        subject_key=subject_key,
        upto_day=upto_day,
        upto_utc=upto_utc,
        query_date=query_date,
    )
    if not candidates:
        return None

    ranked = sorted(candidates, key=_claim_sort_key, reverse=True)
    winner = ranked[0]
    win_val = str(winner["object_val"])

    # Collect all non-forgotten visible sources that asserted the same winning value
    # Prefer direct assertions first so primary mail/chat sources are retained alongside doc v2
    same_val_rows = [r for r in ranked if str(r["object_val"]) == win_val and r["source_record_id"]]
    same_val_rows.sort(
        key=lambda r: (
            1 if r["source_kind"] == "record_assertion" else 0,
            str(r["effective_at_utc"]),
        ),
        reverse=True,
    )
    sources: list[str] = []
    for r in same_val_rows:
        sid = str(r["source_record_id"])
        if sid not in sources:
            sources.append(sid)

    # Also collect any prior superseded value and its source for "moved from X to Y" briefs
    prior_rows = [r for r in ranked if str(r["object_val"]) != win_val]
    old_val = winner["old_val"] or (str(prior_rows[0]["object_val"]) if prior_rows else None)

    return {
        "subject_key": str(winner["subject_key"]),
        "predicate": str(winner["predicate"]),
        "client": winner["client"],
        "object_val": win_val,
        "old_val": old_val,
        "source_kind": str(winner["source_kind"]),
        "effective_at_utc": str(winner["effective_at_utc"]),
        "at_utc": str(winner["at_utc"]),
        "sources": sources,
        "valid_from": winner["valid_from"],
        "valid_until": winner["valid_until"],
    }


def get_claim_changes_in_window(
    conn: sqlite3.Connection,
    person_id: str,
    client_id: str | None,
    since_utc: str | None = None,
    upto_day: int | None = None,
    upto_utc: str | None = None,
    query_date: str | None = None,
    since_day: int | None = None,
) -> list[dict[str, Any]]:
    """Return winning substantive claim changes for client_id within [since_day, upto_day] / after since_utc."""
    day_lim = get_current_day(conn) if upto_day is None else upto_day
    rows = _visible_claims(
        conn,
        person_id,
        client_id=client_id,
        upto_day=day_lim,
        upto_utc=upto_utc,
        query_date=query_date,
    )

    eff_since_utc = since_utc or ""
    if since_day is not None:
        if since_day <= 1:
            eff_since_utc = ""
        else:
            row = conn.execute(
                "SELECT MAX(at_utc) FROM records WHERE day < ?",
                (since_day,),
            ).fetchone()
            if row and row[0]:
                eff_since_utc = str(row[0])

    subject_keys = sorted({str(r["subject_key"]) for r in rows})
    changes: list[dict[str, Any]] = []

    for sk in subject_keys:
        # Find earliest winning claim timestamp for the current winning value so
        # a doc v2 confirming an already-announced Day 3 value still preserves Day 3 provenance.
        winner = get_winning_claim(conn, person_id, sk, upto_day=day_lim, upto_utc=upto_utc, query_date=query_date)
        if not winner:
            continue
        same_val = [
            r for r in rows
            if str(r["subject_key"]) == sk and str(r["object_val"]) == winner["object_val"] and r["source_record_id"]
        ]
        # Earliest assertion of the current winning value, or feedback_correction ingestion timestamp
        first_asserted_utc = min((str(r["effective_at_utc"]) for r in same_val), default=winner["effective_at_utc"])
        latest_utc = max((str(r["effective_at_utc"]) for r in same_val), default=winner["effective_at_utc"])

        if eff_since_utc and latest_utc <= eff_since_utc and first_asserted_utc <= eff_since_utc:
            continue

        # Check if there was a prior distinct value or if this is a brand-new decision/handover
        if winner["predicate"] in ("owner", "date", "decision", "field_update"):
            changes.append(
                {
                    "subject_key": sk,
                    "predicate": winner["predicate"],
                    "object_val": winner["object_val"],
                    "old_val": winner["old_val"],
                    "source_kind": winner["source_kind"],
                    "effective_at_utc": latest_utc,
                    "first_asserted_utc": first_asserted_utc,
                    "sources": winner["sources"],
                }
            )

    return changes

"""Memory introspection, natural-language correction, and right-to-be-forgotten endpoints."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies.db import (
    get_db,
    get_person_or_error,
    log_usage_event,
    resolve_default_date,
)
from backend.api.schemas.models import (
    EntityAliasSchema,
    ForgottenTopicSchema,
    MemoryClaimSchema,
    MemoryCorrectRequest,
    MemoryCorrectResponse,
    MemoryForgetRequest,
    MemoryForgetResponse,
    MemoryPromiseSchema,
    MemoryResponse,
    MemoryTaskSchema,
    PersonalNoteSchema,
    PersonSchema,
    PreferenceRowSchema,
)
from src.audit import audit_for_person
from src.claims import get_winning_claim, is_unverified_or_hedged_clause
from src.memory import _process_correction, forget_for_person
from src.security import (
    _get_forgotten_entries,
    _tainted_phrases,
    _text_echoes_tainted_phrase,
    _text_matches_forgotten,
    can_view,
    get_current_day,
)

router = APIRouter(prefix="/memory", tags=["memory"])


def _build_claim_schemas(
    conn: sqlite3.Connection,
    person_id: str,
    person_org: str,
    current_day: int,
    reference_date: str,
    clients_map: dict[str, str],
) -> list[MemoryClaimSchema]:
    raw_audit = audit_for_person(conn, person_id)
    out: list[MemoryClaimSchema] = []
    for c in raw_audit.get("claims") or []:
        sk = str(c.get("subject_key") or "")
        win = get_winning_claim(conn, person_id, sk, upto_day=current_day)
        source_kind = str((win or {}).get("source_kind") or "record")
        cid = c.get("client")
        valid_until = c.get("valid_until")
        is_expired = bool(valid_until and reference_date and reference_date > str(valid_until))
        out.append(
            MemoryClaimSchema(
                subject_key=sk,
                predicate=str(c.get("predicate") or ""),
                client_id=cid,
                client_name=clients_map.get(cid, cid) if cid else None,
                value=str(c.get("value") or ""),
                old_value=c.get("old_value"),
                source_kind=source_kind,
                effective_at_utc=str(c.get("effective_at_utc") or ""),
                valid_from=c.get("valid_from"),
                valid_until=valid_until,
                is_expired=is_expired,
                sources=[str(s) for s in (c.get("sources") or [])],
            )
        )
    return out


@router.get("", response_model=MemoryResponse)
def get_memory(
    person_id: str = Query(..., min_length=1),
    date: Optional[str] = Query(None),
    conn: sqlite3.Connection = Depends(get_db),
) -> MemoryResponse:
    """Return all authorized persistent memory for person_id."""
    person = get_person_or_error(conn, person_id)
    person_org = person["org_id"]
    current_day = get_current_day(conn)
    reference_date = (date or "").strip() or resolve_default_date(conn)

    people_map = {
        str(r["person_id"]): str(r["name"])
        for r in conn.execute("SELECT person_id, name FROM people").fetchall()
    }
    clients_map = {
        str(c["client_id"]): str(c["name"])
        for c in conn.execute("SELECT client_id, name FROM clients").fetchall()
    }

    kw_groups, forgotten_ids = _get_forgotten_entries(conn, person_id)
    tainted = _tainted_phrases(conn)
    raw_audit = audit_for_person(conn, person_id)

    # Preference rows belonging strictly to person_id
    pref_rows = conn.execute(
        """
        SELECT pref_id, day, pref_key, pref_val, source_words
        FROM preferences
        WHERE person = ? AND day <= ?
        ORDER BY day DESC, pref_id DESC
        """,
        (person_id, current_day),
    ).fetchall()
    pref_history = [
        PreferenceRowSchema(
            pref_id=int(r["pref_id"]),
            day=int(r["day"]),
            pref_key=str(r["pref_key"]),
            pref_val=str(r["pref_val"]),
            source_words=str(r["source_words"]),
        )
        for r in pref_rows
        if not _text_matches_forgotten(f"{r['pref_val']} {r['source_words']}", kw_groups)
    ]

    claims = _build_claim_schemas(
        conn, person_id, person_org, current_day, reference_date, clients_map
    )

    tasks_out: list[MemoryTaskSchema] = []
    for t in raw_audit.get("tasks") or []:
        tid = str(t["task_id"])
        owner_id = str(t.get("owner") or "")
        cid = t.get("client")
        t_extra = conn.execute("SELECT blocked_reason FROM tasks_state WHERE task_id = ?", (tid,)).fetchone()
        blocked_reason = str(t_extra["blocked_reason"]) if (t_extra and t_extra["blocked_reason"]) else None
        if blocked_reason and (
            _text_matches_forgotten(blocked_reason, kw_groups)
            or _text_echoes_tainted_phrase(blocked_reason, tainted)
        ):
            blocked_reason = None
        tasks_out.append(
            MemoryTaskSchema(
                task_id=tid,
                title=str(t.get("title") or ""),
                client_id=cid,
                client_name=clients_map.get(cid, cid) if cid else None,
                owner_id=owner_id,
                owner_name=people_map.get(owner_id, owner_id),
                due=str(t.get("due") or ""),
                status=str(t.get("status") or ""),
                blocked_reason=blocked_reason,
                sources=[str(s) for s in (t.get("sources") or [])],
            )
        )

    promises_out: list[MemoryPromiseSchema] = []
    for p in raw_audit.get("promises") or []:
        owner_id = str(p.get("owner") or "")
        cid = p.get("client")
        promises_out.append(
            MemoryPromiseSchema(
                owner_id=owner_id,
                owner_name=people_map.get(owner_id, owner_id),
                what=str(p.get("what") or ""),
                due=str(p.get("due") or ""),
                status="open",
                client_id=cid,
                client_name=clients_map.get(cid, cid) if cid else None,
                sources=[str(s) for s in (p.get("sources") or [])],
            )
        )

    # Also include recently fulfilled visible promises so users see closure history
    fulfilled_rows = conn.execute(
        """
        SELECT * FROM promises
        WHERE org = ? AND is_forgotten = 0 AND day <= ? AND status = 'fulfilled'
        ORDER BY fulfilled_at_utc DESC, promise_id DESC
        LIMIT 25
        """,
        (person_org, current_day),
    ).fetchall()
    for fr in fulfilled_rows:
        rid = str(fr["record_id"])
        rec = conn.execute("SELECT * FROM records WHERE id = ?", (rid,)).fetchone()
        if rec is None or rid in forgotten_ids or not can_view(rec, person_id, person_org):
            continue
        what = str(fr["what"] or "")
        if _text_matches_forgotten(what, kw_groups) or _text_echoes_tainted_phrase(what, tainted):
            continue
        owner_id = str(fr["owner"] or "")
        cid = fr["client"]
        srcs = [rid]
        if fr["fulfilled_by_record_id"]:
            frec = conn.execute("SELECT * FROM records WHERE id = ?", (str(fr["fulfilled_by_record_id"]),)).fetchone()
            if frec is not None and can_view(frec, person_id, person_org) and int(frec["is_forgotten"]) == 0:
                srcs.append(str(fr["fulfilled_by_record_id"]))
        promises_out.append(
            MemoryPromiseSchema(
                promise_id=int(fr["promise_id"]),
                owner_id=owner_id,
                owner_name=people_map.get(owner_id, owner_id),
                what=what,
                due=str(fr["due_date"] or ""),
                status="fulfilled",
                client_id=cid,
                client_name=clients_map.get(cid, cid) if cid else None,
                sources=srcs,
            )
        )

    # Learned entity aliases authorized for person_org
    alias_rows = conn.execute(
        """
        SELECT alias, entity_type, entity_id, evidence_kind, confidence, source_record_id
        FROM entity_aliases
        WHERE org_id = ?
        ORDER BY confidence DESC, alias ASC
        """,
        (person_org,),
    ).fetchall()
    aliases_out: list[EntityAliasSchema] = []
    for ar in alias_rows:
        srid = ar["source_record_id"]
        if srid:
            if str(srid) in forgotten_ids:
                continue
            srec = conn.execute("SELECT * FROM records WHERE id = ?", (str(srid),)).fetchone()
            if srec is not None and (int(srec["is_forgotten"]) != 0 or not can_view(srec, person_id, person_org)):
                continue
        al = str(ar["alias"])
        eid = str(ar["entity_id"])
        if _text_matches_forgotten(al, kw_groups):
            continue
        etype = str(ar["entity_type"])
        elabel = clients_map.get(eid) or people_map.get(eid) or eid
        aliases_out.append(
            EntityAliasSchema(
                alias=al,
                entity_type=etype,
                entity_id=eid,
                entity_label=elabel,
                evidence_kind=str(ar["evidence_kind"]),
                confidence=float(ar["confidence"]),
                source_record_id=str(srid) if srid else None,
            )
        )

    notes_out = [
        PersonalNoteSchema(
            text=str(n.get("text") or ""),
            visibility=str(n.get("visibility") or "private"),
            at_utc=str(n.get("at_utc") or ""),
            sources=[str(s) for s in (n.get("sources") or [])],
        )
        for n in (raw_audit.get("personal_notes") or [])
    ]

    ft_rows = conn.execute(
        """
        SELECT forget_id, day, raw_words, topic_keywords_json, affected_record_ids_json
        FROM forgotten_topics
        WHERE person = ?
        ORDER BY forget_id DESC
        """,
        (person_id,),
    ).fetchall()
    forgotten_out: list[ForgottenTopicSchema] = []
    for ft in ft_rows:
        try:
            kws = [str(k) for k in json.loads(ft["topic_keywords_json"] or "[]")]
        except ValueError:
            kws = []
        try:
            rids = [str(i) for i in json.loads(ft["affected_record_ids_json"] or "[]")]
        except ValueError:
            rids = []
        forgotten_out.append(
            ForgottenTopicSchema(
                forget_id=int(ft["forget_id"]),
                day=int(ft["day"]),
                raw_words=str(ft["raw_words"]),
                keywords=kws,
                affected_record_ids=rids,
            )
        )

    return MemoryResponse(
        person=PersonSchema(**person),
        current_day=current_day,
        reference_date=reference_date,
        active_preferences_summary=raw_audit.get("preferences") or {},
        preference_history=pref_history,
        claims=claims,
        tasks=tasks_out,
        promises=promises_out,
        entity_aliases=aliases_out,
        personal_notes=notes_out,
        forgotten_topics=forgotten_out,
    )


@router.post("/correct", response_model=MemoryCorrectResponse)
def submit_correction(
    req: MemoryCorrectRequest,
    conn: sqlite3.Connection = Depends(get_db),
) -> MemoryCorrectResponse:
    """Apply a verified natural-language correction via the challenge feedback/claims pipeline."""
    person = get_person_or_error(conn, req.person_id)
    person_id = person["person_id"]
    person_org = person["org_id"]
    current_day = get_current_day(conn)
    if current_day <= 0:
        raise HTTPException(status_code=400, detail="No ingested days found in the database.")

    words = (req.words or "").strip()
    if len(words) < 3:
        raise HTTPException(status_code=400, detail="Correction statement is too short.")

    t0 = time.monotonic()
    if is_unverified_or_hedged_clause(words):
        elapsed_ms = max(0, int((time.monotonic() - t0) * 1000))
        log_usage_event(
            conn,
            operation="memory_correct",
            person_id=person_id,
            org_id=person_org,
            latency_ms=elapsed_ms,
            status="ignored_hedged",
            detail=words[:120],
        )
        return MemoryCorrectResponse(
            ok=True,
            applied=False,
            message="Correction ignored: statement appears hedged, speculative, or unverified.",
            person_id=person_id,
            updated_claims=[],
        )

    cur = conn.execute(
        """
        INSERT INTO feedback(day, ingested_on_day, person, type, meeting, words, is_forgotten)
        VALUES (?, ?, ?, 'correction', ?, ?, 0)
        """,
        (current_day, current_day, person_id, req.meeting_id, words),
    )
    fb_id = cur.lastrowid
    fb_row = conn.execute("SELECT * FROM feedback WHERE feedback_id = ?", (fb_id,)).fetchone()
    _process_correction(conn, fb_row, person_org)

    inserted = conn.execute(
        "SELECT subject_key FROM claims WHERE feedback_id = ? AND is_forgotten = 0",
        (fb_id,),
    ).fetchall()
    if not inserted:
        conn.execute("DELETE FROM feedback WHERE feedback_id = ?", (fb_id,))
        conn.commit()
        elapsed_ms = max(0, int((time.monotonic() - t0) * 1000))
        log_usage_event(
            conn,
            operation="memory_correct",
            person_id=person_id,
            org_id=person_org,
            latency_ms=elapsed_ms,
            status="unparsed",
            detail=words[:120],
        )
        return MemoryCorrectResponse(
            ok=True,
            applied=False,
            message=(
                "Could not extract a structured owner or launch-date correction from that phrasing. "
                "Try e.g. 'No, Arjun handles Cobaltline billing now, not Kavya.' or "
                "'The launch date for Northbeam is 14 Nov.'"
            ),
            person_id=person_id,
            updated_claims=[],
        )

    conn.execute("DELETE FROM brief_cache")
    conn.commit()

    clients_map = {
        str(c["client_id"]): str(c["name"])
        for c in conn.execute("SELECT client_id, name FROM clients").fetchall()
    }
    def_date = resolve_default_date(conn)
    subjects = {str(r["subject_key"]) for r in inserted}
    all_claims = _build_claim_schemas(
        conn, person_id, person_org, current_day, def_date, clients_map
    )
    updated_claims = [c for c in all_claims if c.subject_key in subjects]

    elapsed_ms = max(0, int((time.monotonic() - t0) * 1000))
    log_usage_event(
        conn,
        operation="memory_correct",
        person_id=person_id,
        org_id=person_org,
        latency_ms=elapsed_ms,
        status="applied",
        detail=words[:120],
    )
    return MemoryCorrectResponse(
        ok=True,
        applied=True,
        message="Correction recorded and applied across memory, Q&A, and meeting briefs.",
        person_id=person_id,
        updated_claims=updated_claims,
    )


@router.post("/forget", response_model=MemoryForgetResponse)
def forget_topic(
    req: MemoryForgetRequest,
    conn: sqlite3.Connection = Depends(get_db),
) -> MemoryForgetResponse:
    """Invoke cascading Right-to-Be-Forgotten retraction scoped to person_id."""
    person = get_person_or_error(conn, req.person_id)
    person_id = person["person_id"]
    person_org = person["org_id"]
    what = (req.what or "").strip()
    if len(what) < 2:
        raise HTTPException(status_code=400, detail="Please specify what information should be forgotten.")

    t0 = time.monotonic()
    res = forget_for_person(conn, person_id, what)
    elapsed_ms = max(0, int((time.monotonic() - t0) * 1000))

    log_usage_event(
        conn,
        operation="memory_forget",
        person_id=person_id,
        org_id=person_org,
        latency_ms=elapsed_ms,
        status="ok",
        detail=f"what={what[:80]} records={res.get('forgotten_records', 0)}",
    )
    return MemoryForgetResponse(
        ok=bool(res.get("ok", True)),
        person_id=person_id,
        what=what,
        forgotten_records=int(res.get("forgotten_records") or 0),
        keywords=[str(k) for k in (res.get("keywords") or [])],
    )

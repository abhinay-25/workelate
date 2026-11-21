"""ACL-strict activity & audit trail endpoint."""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, Query

from backend.api.dependencies.db import (
    get_available_dates,
    get_db,
    get_person_or_error,
)
from backend.api.schemas.models import (
    AuditEventSchema,
    AuditResponse,
    IngestedDaySummarySchema,
    PersonSchema,
)
from src.security import (
    _get_forgotten_entries,
    _tainted_phrases,
    _text_echoes_tainted_phrase,
    _text_matches_forgotten,
    can_view,
    get_current_day,
    visible_records_params,
    visible_records_where_clause,
)

router = APIRouter(tags=["audit"])


@router.get("/audit", response_model=AuditResponse)
def get_audit_trail(
    person_id: str = Query(..., min_length=1),
    day: Optional[int] = Query(None, ge=1, le=30),
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    conn: sqlite3.Connection = Depends(get_db),
) -> AuditResponse:
    """Return authorized, org-isolated traceability events for person_id."""
    person = get_person_or_error(conn, person_id)
    person_org = person["org_id"]
    current_day = get_current_day(conn)

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

    where = visible_records_where_clause("r", with_utc_cutoff=False)
    params = visible_records_params(person_id, person_org, current_day)

    # Ingested day summary with strictly authorized visible record counts per day
    avail_dates = get_available_dates(conn)
    ingested_summaries: list[IngestedDaySummarySchema] = []
    for ad in avail_dates:
        dnum = int(ad["day"])
        cnt_rows = conn.execute(
            f"SELECT r.* FROM records r WHERE r.day = :d AND {where}",
            {**params, "d": dnum},
        ).fetchall()
        vis_cnt = 0
        for rr in cnt_rows:
            if str(rr["id"]) in forgotten_ids:
                continue
            blob = f"{rr['title'] or ''} {rr['subject'] or ''} {rr['sanitized_body'] or ''}"
            if _text_matches_forgotten(blob, kw_groups):
                continue
            vis_cnt += 1
        ingested_summaries.append(
            IngestedDaySummarySchema(day=dnum, date=str(ad["date"]), visible_records=vis_cnt)
        )

    events: list[AuditEventSchema] = []

    # 1. Authorized corrections & claim updates visible to person_id
    claim_rows = conn.execute(
        """
        SELECT * FROM claims
        WHERE org = ? AND is_forgotten = 0 AND day <= ?
        ORDER BY day DESC, claim_id DESC
        """,
        (person_org, current_day),
    ).fetchall()
    for cr in claim_rows:
        if not can_view(cr, person_id, person_org):
            continue
        if day is not None and int(cr["day"]) != day:
            continue
        blob = f"{cr['subject_key']} {cr['object_val']}"
        if _text_matches_forgotten(blob, kw_groups) or _text_echoes_tainted_phrase(blob, tainted):
            continue
        srid = cr["source_record_id"]
        srcs: list[str] = []
        if srid and str(srid) not in forgotten_ids:
            srec = conn.execute("SELECT * FROM records WHERE id = ?", (str(srid),)).fetchone()
            if srec is not None and can_view(srec, person_id, person_org) and int(srec["is_forgotten"]) == 0:
                srcs.append(str(srid))
        sk = str(cr["source_kind"])
        ev_cat = "correction" if sk == "feedback_correction" else "claim_update"
        cid = cr["client"]
        actor_id = str(cr["author"] or "")
        events.append(
            AuditEventSchema(
                event_uid=f"claim-{cr['claim_id']}",
                day=int(cr["day"]),
                at_utc=str(cr["effective_at_utc"] or cr["at_utc"]),
                category=ev_cat,
                title=f"{'Correction' if ev_cat == 'correction' else 'Fact Extracted'}: {cr['subject_key']}",
                summary=f"{cr['predicate']} -> {cr['object_val']}"
                + (f" (replaced {cr['old_val']})" if cr["old_val"] else ""),
                actor_id=actor_id,
                actor_name=people_map.get(actor_id, actor_id),
                visibility=str(cr["visibility"]),
                client_id=cid,
                client_name=clients_map.get(cid, cid) if cid else None,
                sources=srcs,
                status="applied" if ev_cat == "correction" else "verified",
            )
        )

    # 2. Strictly person-scoped preferences
    pref_rows = conn.execute(
        """
        SELECT * FROM preferences WHERE person = ? AND day <= ?
        ORDER BY day DESC, pref_id DESC
        """,
        (person_id, current_day),
    ).fetchall()
    for pr in pref_rows:
        if day is not None and int(pr["day"]) != day:
            continue
        blob = f"{pr['pref_val']} {pr['source_words']}"
        if _text_matches_forgotten(blob, kw_groups):
            continue
        events.append(
            AuditEventSchema(
                event_uid=f"pref-{pr['pref_id']}",
                day=int(pr["day"]),
                at_utc=None,
                category="preference",
                title=f"Brief Preference: {pr['pref_key']} = {pr['pref_val']}",
                summary=f"Learned from feedback: \"{pr['source_words']}\"",
                actor_id=person_id,
                actor_name=person["name"],
                visibility="private",
                sources=[],
                status="active",
            )
        )

    # 3. Strictly person-scoped forgotten topics tombstones
    ft_rows = conn.execute(
        """
        SELECT * FROM forgotten_topics WHERE person = ?
        ORDER BY forget_id DESC
        """,
        (person_id,),
    ).fetchall()
    for ft in ft_rows:
        if day is not None and int(ft["day"]) != day:
            continue
        try:
            kws = json.loads(ft["topic_keywords_json"] or "[]")
            rids = json.loads(ft["affected_record_ids_json"] or "[]")
        except ValueError:
            kws, rids = [], []
        events.append(
            AuditEventSchema(
                event_uid=f"forget-{ft['forget_id']}",
                day=int(ft["day"]),
                at_utc=None,
                category="forget",
                title="Right-to-Be-Forgotten Executed",
                summary=f"Retracted {len(rids)} authored record(s) and suppressed keywords: {', '.join(kws)}",
                actor_id=person_id,
                actor_name=person["name"],
                visibility="private",
                sources=[],
                status="retracted",
            )
        )

    # 4. Security filter notifications & authorized workplace records (including pure-injection blocks)
    sec_rows = conn.execute(
        """
        SELECT * FROM records r
        WHERE r.org = :person_org AND r.day <= :upto_day AND r.is_forgotten = 0
          AND r.has_injection_span = 1
          AND (
              r.visibility = 'team'
              OR (r.visibility = 'private' AND r.author = :person_id)
              OR (r.visibility = 'restricted' AND EXISTS (
                  SELECT 1 FROM json_each(COALESCE(r.readers_json, '[]')) AS je WHERE je.value = :person_id
              ))
          )
        ORDER BY r.day DESC, r.at_utc DESC
        """,
        params,
    ).fetchall()
    for sr in sec_rows:
        if day is not None and int(sr["day"]) != day:
            continue
        rid = str(sr["id"])
        if rid in forgotten_ids:
            continue
        has_safe_text = bool(str(sr["sanitized_body"] or "").strip())
        cid = sr["client"]
        author_id = str(sr["author"] or "")
        events.append(
            AuditEventSchema(
                event_uid=f"sec-{rid}",
                day=int(sr["day"]),
                at_utc=str(sr["at_utc"]),
                category="security_filter",
                title=f"Prompt-Injection Defense Triggered ({rid})",
                summary=(
                    "Stripped untrusted imperative span while preserving legitimate factual sentence."
                    if has_safe_text
                    else "Entire record quarantined by security firewall (zero safe factual text)."
                ),
                actor_id=author_id,
                actor_name=people_map.get(author_id, author_id),
                visibility=str(sr["visibility"]),
                client_id=cid,
                client_name=clients_map.get(cid, cid) if cid else None,
                sources=[rid] if has_safe_text else [],
                status="sanitized" if has_safe_text else "quarantined",
            )
        )

    # 5. Authorized workplace records timeline (non-boilerplate, non-newsletter)
    day_clause = "AND r.day = :filter_day" if day is not None else ""
    if day is not None:
        params_rec = {**params, "filter_day": day}
    else:
        params_rec = params

    rec_rows = conn.execute(
        f"""
        SELECT r.* FROM records r
        WHERE {where}
          AND r.is_boilerplate = 0
          AND r.is_newsletter = 0
          {day_clause}
        ORDER BY r.day DESC, r.at_utc DESC, r.line_no DESC
        LIMIT 250
        """,
        params_rec,
    ).fetchall()

    visibility_counts: dict[str, int] = {"team": 0, "private": 0, "restricted": 0}
    for rr in rec_rows:
        rid = str(rr["id"])
        if rid in forgotten_ids or not can_view(rr, person_id, person_org):
            continue
        vis = str(rr["visibility"] or "team")
        visibility_counts[vis] = visibility_counts.get(vis, 0) + 1

        body = str(rr["sanitized_body"] or "").strip()
        title_or_subj = rr["title"] or rr["subject"] or f"{rr['type']} ({rid})"
        blob = f"{title_or_subj} {body}"
        if _text_matches_forgotten(blob, kw_groups) or _text_echoes_tainted_phrase(body, tainted):
            continue
        cid = rr["client"]
        author_id = str(rr["author"] or "")
        events.append(
            AuditEventSchema(
                event_uid=f"rec-{rid}",
                day=int(rr["day"]),
                at_utc=str(rr["at_utc"]),
                category="record",
                title=str(title_or_subj),
                summary=body[:180] + ("..." if len(body) > 180 else ""),
                actor_id=author_id,
                actor_name=people_map.get(author_id, author_id),
                visibility=vis,
                client_id=cid,
                client_name=clients_map.get(cid, cid) if cid else None,
                sources=[rid],
                status=str(rr["type"]),
            )
        )

    # Also compute true total visible counts across ALL authorized records (not just top 250)
    vis_rows = conn.execute(
        f"SELECT r.visibility, r.id, r.title, r.subject, r.sanitized_body FROM records r WHERE {where}",
        params,
    ).fetchall()
    true_vis_counts = {"team": 0, "private": 0, "restricted": 0}
    for vr in vis_rows:
        if str(vr["id"]) in forgotten_ids:
            continue
        blob = f"{vr['title'] or ''} {vr['subject'] or ''} {vr['sanitized_body'] or ''}"
        if _text_matches_forgotten(blob, kw_groups):
            continue
        v = str(vr["visibility"] or "team")
        true_vis_counts[v] = true_vis_counts.get(v, 0) + 1

    # Category counts prior to filtering
    category_counts: dict[str, int] = {}
    for ev in events:
        category_counts[ev.category] = category_counts.get(ev.category, 0) + 1

    # Apply optional category and text search filters
    filtered = events
    if category and category != "all":
        filtered = [e for e in filtered if e.category == category]
    if search and search.strip():
        q = search.strip().lower()
        filtered = [
            e
            for e in filtered
            if q in e.title.lower()
            or q in e.summary.lower()
            or (e.actor_name or "").lower().find(q) >= 0
            or (e.client_name or "").lower().find(q) >= 0
            or any(q in s.lower() for s in e.sources)
        ]

    filtered.sort(key=lambda e: (e.day, e.at_utc or ""), reverse=True)

    return AuditResponse(
        person=PersonSchema(**person),
        current_day=current_day,
        ingested_days=ingested_summaries,
        visibility_counts=true_vis_counts,
        category_counts=category_counts,
        events=filtered[:250],
    )

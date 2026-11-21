"""Event-sourced task lifecycle reconstruction and point-in-time/window queries."""

import sqlite3
from typing import Any

from src.security import get_current_day, get_person_org, visible_records_params, visible_records_where_clause

_STATE_FIELDS = frozenset({"created", "status", "owner", "due"})


def ingest_tasks_for_day(
    conn: sqlite3.Connection,
    raw_records: list[dict[str, Any]],
    day: int,
) -> None:
    """Record task transitions from day N and rebuild active tasks_state."""
    conn.execute("DELETE FROM task_transitions WHERE day = ?", (day,))

    internal_ids = {
        str(r["person_id"]) for r in conn.execute("SELECT person_id FROM people").fetchall()
    }

    for rec in raw_records:
        if rec.get("type") != "task" or not rec.get("task_id"):
            continue
        ch = rec.get("change") or {}
        field = str(ch.get("field") or "comment")
        from_val = ch.get("from")
        to_val = ch.get("to")
        org = str(rec.get("org") or "org_kp")
        client = rec.get("client")
        author = str(rec.get("author") or "")
        owner = str(rec.get("owner") or "")
        rid = str(rec["id"])

        conn.execute(
            """
            INSERT INTO task_transitions(
                record_id, day, at_utc, org, client, task_id, title, author,
                field, from_val, to_val, snap_owner, snap_due, snap_status, body, visibility, is_forgotten
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                rid,
                day,
                str(rec.get("at") or ""),
                org,
                client,
                str(rec["task_id"]),
                str(rec.get("title") or ""),
                author,
                field,
                None if from_val is None else str(from_val),
                None if to_val is None else str(to_val),
                owner,
                str(rec.get("due") or ""),
                str(rec.get("status") or ""),
                str(rec.get("body") or ""),
                str(rec.get("visibility") or "team"),
            ),
        )

        # Enrich collaboration edges with task author <-> owner and client associations
        if owner in internal_ids:
            if client:
                conn.execute(
                    "INSERT INTO collab_edges(org_id, person_a, person_b, client, weight, source_record_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (org, owner, owner, client, 2.5, rid),
                )
            if author in internal_ids and author != owner:
                for pa, pb in ((author, owner), (owner, author)):
                    conn.execute(
                        "INSERT INTO collab_edges(org_id, person_a, person_b, client, weight, source_record_id) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (org, pa, pb, client, 2.5, rid),
                    )

    rebuild_tasks_state(conn)


def _replay_transitions(rows: list[sqlite3.Row]) -> dict[str, dict[str, Any]]:
    """Replay transition rows chronologically using change events only (ignoring stale comment snapshots)."""
    states: dict[str, dict[str, Any]] = {}
    for row in rows:
        tid = str(row["task_id"])
        field = str(row["field"])
        rid = str(row["record_id"])
        at_utc = str(row["at_utc"])

        if tid not in states:
            states[tid] = {
                "task_id": tid,
                "org": str(row["org"]),
                "client": row["client"],
                "title": str(row["title"]),
                "owner": str(row["snap_owner"]),
                "due": str(row["snap_due"]),
                "status": str(row["snap_status"]),
                "created_at_utc": at_utc,
                "created_record_id": rid,
                "last_status_record_id": rid,
                "last_owner_record_id": rid,
                "last_due_record_id": rid,
                "blocked_reason": None,
                "last_updated_at_utc": at_utc,
            }

        st = states[tid]
        if row["title"]:
            st["title"] = str(row["title"])
        if row["client"] is not None:
            st["client"] = row["client"]

        if field == "created":
            st["owner"] = str(row["snap_owner"])
            st["due"] = str(row["snap_due"])
            st["status"] = str(row["snap_status"])
            st["created_at_utc"] = at_utc
            st["created_record_id"] = rid
            st["last_status_record_id"] = rid
            st["last_owner_record_id"] = rid
            st["last_due_record_id"] = rid
            st["last_updated_at_utc"] = at_utc
        elif field == "status":
            new_status = str(row["to_val"] if row["to_val"] is not None else row["snap_status"])
            st["status"] = new_status
            st["last_status_record_id"] = rid
            st["last_updated_at_utc"] = at_utc
            if new_status == "blocked":
                st["blocked_reason"] = str(row["body"] or "").strip()
            else:
                st["blocked_reason"] = None
        elif field == "owner":
            new_owner = str(row["to_val"] if row["to_val"] is not None else row["snap_owner"])
            st["owner"] = new_owner
            st["last_owner_record_id"] = rid
            st["last_updated_at_utc"] = at_utc
        elif field == "due":
            new_due = str(row["to_val"] if row["to_val"] is not None else row["snap_due"])
            st["due"] = new_due
            st["last_due_record_id"] = rid
            st["last_updated_at_utc"] = at_utc
        # field == 'comment' intentionally does not mutate status, owner, or due

    return states


def rebuild_tasks_state(conn: sqlite3.Connection) -> None:
    """Recompute tasks_state from all non-forgotten task_transitions."""
    rows = conn.execute(
        """
        SELECT tt.*
        FROM task_transitions tt
        JOIN records r ON r.id = tt.record_id
        WHERE tt.is_forgotten = 0 AND r.is_forgotten = 0
        ORDER BY tt.at_utc ASC, tt.id ASC
        """
    ).fetchall()

    states = _replay_transitions(rows)
    conn.execute("DELETE FROM tasks_state")
    for st in states.values():
        conn.execute(
            """
            INSERT INTO tasks_state(
                task_id, org, client, title, owner, due, status,
                created_at_utc, created_record_id,
                last_status_record_id, last_owner_record_id, last_due_record_id, blocked_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                st["task_id"],
                st["org"],
                st["client"],
                st["title"],
                st["owner"],
                st["due"],
                st["status"],
                st["created_at_utc"],
                st["created_record_id"],
                st["last_status_record_id"],
                st["last_owner_record_id"],
                st["last_due_record_id"],
                st["blocked_reason"],
            ),
        )


def get_visible_tasks_at(
    conn: sqlite3.Connection,
    person_id: str,
    upto_day: int | None = None,
    upto_utc: str | None = None,
    client_id: str | None = None,
    owner_id: str | None = None,
    due_date: str | None = None,
) -> list[dict[str, Any]]:
    """Reconstruct all visible task states as of (upto_day, upto_utc)."""
    org = get_person_org(conn, person_id)
    if not org:
        return []
    day_lim = get_current_day(conn) if upto_day is None else upto_day
    where = visible_records_where_clause("r", with_utc_cutoff=(upto_utc is not None))
    params = visible_records_params(person_id, org, day_lim, upto_utc)

    sql = f"""
        SELECT tt.*
        FROM task_transitions tt
        JOIN records r ON r.id = tt.record_id
        WHERE tt.is_forgotten = 0 AND {where}
        ORDER BY tt.at_utc ASC, tt.id ASC
    """
    rows = conn.execute(sql, params).fetchall()
    states = list(_replay_transitions(rows).values())

    if client_id is not None:
        states = [s for s in states if s["client"] == client_id]
    if owner_id is not None:
        states = [s for s in states if s["owner"] == owner_id]
    if due_date is not None:
        states = [s for s in states if s["due"] == due_date]

    states.sort(key=lambda s: s["task_id"])
    return states


def get_task_state_at(
    conn: sqlite3.Connection,
    task_id: str,
    person_id: str,
    upto_day: int | None = None,
    upto_utc: str | None = None,
) -> dict[str, Any] | None:
    """Return reconstructed state for one visible task as of (upto_day, upto_utc)."""
    org = get_person_org(conn, person_id)
    if not org:
        return None
    day_lim = get_current_day(conn) if upto_day is None else upto_day
    where = visible_records_where_clause("r", with_utc_cutoff=(upto_utc is not None))
    params = visible_records_params(person_id, org, day_lim, upto_utc)
    params["tid"] = task_id

    rows = conn.execute(
        f"""
        SELECT tt.*
        FROM task_transitions tt
        JOIN records r ON r.id = tt.record_id
        WHERE tt.task_id = :tid AND tt.is_forgotten = 0 AND {where}
        ORDER BY tt.at_utc ASC, tt.id ASC
        """,
        params,
    ).fetchall()
    states = _replay_transitions(rows)
    return states.get(task_id)


def get_task_changes_in_window(
    conn: sqlite3.Connection,
    person_id: str,
    client_id: str | None,
    since_utc: str | None = None,
    upto_day: int | None = None,
    upto_utc: str | None = None,
    since_day: int | None = None,
) -> list[dict[str, Any]]:
    """Collect collapsed substantive task changes (status, owner, due) in window."""
    org = get_person_org(conn, person_id)
    if not org:
        return []
    day_lim = get_current_day(conn) if upto_day is None else upto_day
    where = visible_records_where_clause("r", with_utc_cutoff=(upto_utc is not None))
    params = visible_records_params(person_id, org, day_lim, upto_utc)

    window_clause = ""
    if since_day is not None:
        window_clause = "AND tt.day >= :since_day"
        params["since_day"] = since_day
    elif since_utc:
        window_clause = "AND tt.at_utc > :since_utc"
        params["since_utc"] = since_utc

    client_clause = "AND tt.client = :cid" if client_id is not None else "AND tt.client IS NULL"
    if client_id is not None:
        params["cid"] = client_id

    rows = conn.execute(
        f"""
        SELECT tt.*
        FROM task_transitions tt
        JOIN records r ON r.id = tt.record_id
        WHERE tt.is_forgotten = 0
          AND tt.field IN ('status', 'owner', 'due')
          {window_clause}
          {client_clause}
          AND {where}
        ORDER BY tt.at_utc ASC, tt.id ASC
        """,
        params,
    ).fetchall()

    # Collapse consecutive transitions on the same (task_id, field) within the window
    collapsed: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["task_id"]), str(row["field"]))
        if key not in collapsed:
            collapsed[key] = {
                "task_id": str(row["task_id"]),
                "title": str(row["title"]),
                "client": row["client"],
                "field": str(row["field"]),
                "from_val": row["from_val"],
                "to_val": row["to_val"],
                "at_utc": str(row["at_utc"]),
                "record_id": str(row["record_id"]),
                "body": str(row["body"] or ""),
            }
        else:
            collapsed[key]["to_val"] = row["to_val"]
            collapsed[key]["at_utc"] = str(row["at_utc"])
            collapsed[key]["record_id"] = str(row["record_id"])
            collapsed[key]["body"] = str(row["body"] or "")

    out: list[dict[str, Any]] = []
    for item in collapsed.values():
        field = item["field"]
        to_val = str(item["to_val"] or "")
        if field == "status":
            if to_val == "blocked":
                item["kind"] = "blocked"
                item["prio"] = 1
            else:
                item["kind"] = "status"
                item["prio"] = 2
        elif field == "due":
            item["kind"] = "date"
            item["prio"] = 2
        elif field == "owner":
            item["kind"] = "owner"
            item["prio"] = 2
        else:
            continue
        out.append(item)

    return out

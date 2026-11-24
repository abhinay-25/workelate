"""SQLite + FTS5 persistence layer for BuildSprint R3."""

import json
import os
import sqlite3

from src.config import db_path

SCHEMA_VERSION = "1"

REQUIRED_TABLES = (
    "meta_state",
    "orgs",
    "people",
    "clients",
    "entity_aliases",
    "collab_edges",
    "records",
    "records_fts",
    "task_transitions",
    "tasks_state",
    "doc_versions",
    "promises",
    "claims",
    "feedback",
    "preferences",
    "forgotten_topics",
    "brief_cache",
    "record_vectors",
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orgs (
    org_id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS people (
    person_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES orgs(org_id),
    name TEXT NOT NULL,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    role TEXT NOT NULL,
    team TEXT,
    zone TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
    client_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES orgs(org_id),
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_aliases (
    alias TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    org_id TEXT NOT NULL,
    evidence_kind TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    source_record_id TEXT,
    PRIMARY KEY (alias, entity_type, entity_id, org_id, evidence_kind)
);
CREATE INDEX IF NOT EXISTS idx_aliases_lookup ON entity_aliases(org_id, alias);

CREATE TABLE IF NOT EXISTS collab_edges (
    edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id TEXT NOT NULL,
    person_a TEXT NOT NULL,
    person_b TEXT NOT NULL,
    client TEXT,
    weight REAL NOT NULL DEFAULT 1.0,
    source_record_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_collab_pair ON collab_edges(org_id, person_a, person_b, client);

CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY,
    day INTEGER NOT NULL,
    line_no INTEGER NOT NULL,
    org TEXT NOT NULL,
    type TEXT NOT NULL,
    at_utc TEXT NOT NULL,
    local_date TEXT NOT NULL,
    author TEXT NOT NULL,
    client TEXT,
    visibility TEXT NOT NULL,
    readers_json TEXT,
    channel TEXT,
    to_json TEXT,
    subject TEXT,
    title TEXT,
    doc_id TEXT,
    sheet_id TEXT,
    version INTEGER,
    cells_json TEXT,
    task_id TEXT,
    event_id TEXT,
    start_utc TEXT,
    attendees_json TEXT,
    body TEXT NOT NULL,
    sanitized_body TEXT NOT NULL,
    has_injection_span INTEGER NOT NULL DEFAULT 0,
    injected_spans_json TEXT,
    is_boilerplate INTEGER NOT NULL DEFAULT 0,
    is_newsletter INTEGER NOT NULL DEFAULT 0,
    is_forgotten INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_records_org_day ON records(org, day, at_utc);
CREATE INDEX IF NOT EXISTS idx_records_client ON records(org, client, at_utc);
CREATE INDEX IF NOT EXISTS idx_records_type ON records(org, type, event_id);
CREATE INDEX IF NOT EXISTS idx_records_task ON records(task_id, at_utc);
CREATE INDEX IF NOT EXISTS idx_records_author ON records(org, author, is_forgotten);

CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
    id UNINDEXED,
    title,
    subject,
    sanitized_body,
    content='records',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS records_ai AFTER INSERT ON records BEGIN
    INSERT INTO records_fts(rowid, id, title, subject, sanitized_body)
    VALUES (new.rowid, new.id, COALESCE(new.title, ''), COALESCE(new.subject, ''), new.sanitized_body);
END;

CREATE TRIGGER IF NOT EXISTS records_ad AFTER DELETE ON records BEGIN
    INSERT INTO records_fts(records_fts, rowid, id, title, subject, sanitized_body)
    VALUES ('delete', old.rowid, old.id, COALESCE(old.title, ''), COALESCE(old.subject, ''), old.sanitized_body);
    DELETE FROM record_vectors WHERE record_id = old.id;
END;

CREATE TRIGGER IF NOT EXISTS records_au AFTER UPDATE ON records BEGIN
    INSERT INTO records_fts(records_fts, rowid, id, title, subject, sanitized_body)
    VALUES ('delete', old.rowid, old.id, COALESCE(old.title, ''), COALESCE(old.subject, ''), old.sanitized_body);
    INSERT INTO records_fts(rowid, id, title, subject, sanitized_body)
    VALUES (new.rowid, new.id, COALESCE(new.title, ''), COALESCE(new.subject, ''), new.sanitized_body);
END;

CREATE TRIGGER IF NOT EXISTS records_au_vectors AFTER UPDATE ON records
WHEN new.is_forgotten = 1 OR TRIM(COALESCE(new.sanitized_body, '')) = '' BEGIN
    DELETE FROM record_vectors WHERE record_id = old.id;
END;

CREATE TABLE IF NOT EXISTS task_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT NOT NULL REFERENCES records(id),
    day INTEGER NOT NULL,
    at_utc TEXT NOT NULL,
    org TEXT NOT NULL,
    client TEXT,
    task_id TEXT NOT NULL,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    field TEXT NOT NULL,
    from_val TEXT,
    to_val TEXT,
    snap_owner TEXT NOT NULL,
    snap_due TEXT NOT NULL,
    snap_status TEXT NOT NULL,
    body TEXT NOT NULL,
    visibility TEXT NOT NULL,
    is_forgotten INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_task_trans_tid ON task_transitions(task_id, at_utc);

CREATE TABLE IF NOT EXISTS tasks_state (
    task_id TEXT PRIMARY KEY,
    org TEXT NOT NULL,
    client TEXT,
    title TEXT NOT NULL,
    owner TEXT NOT NULL,
    due TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    created_record_id TEXT NOT NULL,
    last_status_record_id TEXT,
    last_owner_record_id TEXT,
    last_due_record_id TEXT,
    blocked_reason TEXT
);

CREATE TABLE IF NOT EXISTS doc_versions (
    doc_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    version INTEGER NOT NULL,
    record_id TEXT NOT NULL REFERENCES records(id),
    day INTEGER NOT NULL,
    at_utc TEXT NOT NULL,
    org TEXT NOT NULL,
    client TEXT,
    author TEXT NOT NULL,
    title TEXT,
    extracted_kv_json TEXT NOT NULL,
    diff_summary_json TEXT,
    is_forgotten INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (org, doc_id, version)
);

CREATE TABLE IF NOT EXISTS promises (
    promise_id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT NOT NULL REFERENCES records(id),
    day INTEGER NOT NULL,
    at_utc TEXT NOT NULL,
    org TEXT NOT NULL,
    client TEXT,
    owner TEXT NOT NULL,
    what TEXT NOT NULL,
    what_norm TEXT NOT NULL,
    due_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    fulfilled_by_record_id TEXT,
    fulfilled_at_utc TEXT,
    visibility TEXT NOT NULL,
    readers_json TEXT,
    is_forgotten INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_promises_client ON promises(org, client, status, is_forgotten);

CREATE TABLE IF NOT EXISTS claims (
    claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
    org TEXT NOT NULL,
    client TEXT,
    subject_key TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object_val TEXT NOT NULL,
    old_val TEXT,
    source_kind TEXT NOT NULL,
    author_role TEXT,
    effective_at_utc TEXT NOT NULL,
    at_utc TEXT NOT NULL,
    day INTEGER NOT NULL,
    source_record_id TEXT,
    feedback_id INTEGER,
    visibility TEXT NOT NULL,
    author TEXT NOT NULL,
    readers_json TEXT,
    valid_from TEXT,
    valid_until TEXT,
    superseded_by_claim_id INTEGER,
    is_forgotten INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_claims_subj ON claims(org, subject_key, is_forgotten);
CREATE INDEX IF NOT EXISTS idx_claims_client ON claims(org, client, day, is_forgotten);

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
    day INTEGER NOT NULL,
    ingested_on_day INTEGER NOT NULL,
    person TEXT NOT NULL,
    type TEXT NOT NULL,
    meeting TEXT,
    topic TEXT,
    verdict TEXT,
    words TEXT NOT NULL,
    is_forgotten INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS preferences (
    pref_id INTEGER PRIMARY KEY AUTOINCREMENT,
    person TEXT NOT NULL,
    day INTEGER NOT NULL,
    pref_key TEXT NOT NULL,
    pref_val TEXT NOT NULL,
    source_words TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prefs_person ON preferences(person, pref_key);

CREATE TABLE IF NOT EXISTS forgotten_topics (
    forget_id INTEGER PRIMARY KEY AUTOINCREMENT,
    person TEXT NOT NULL,
    day INTEGER NOT NULL,
    raw_words TEXT NOT NULL,
    topic_keywords_json TEXT NOT NULL,
    affected_record_ids_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_forgotten_person ON forgotten_topics(person);

CREATE TABLE IF NOT EXISTS brief_cache (
    person TEXT NOT NULL,
    date TEXT NOT NULL,
    day INTEGER NOT NULL,
    response_json TEXT NOT NULL,
    PRIMARY KEY (person, date, day)
);

CREATE TABLE IF NOT EXISTS brief_watermarks (
    person TEXT NOT NULL,
    client TEXT NOT NULL,
    meeting_date TEXT NOT NULL,
    prepared_day INTEGER NOT NULL,
    PRIMARY KEY (person, client, meeting_date)
);

CREATE TABLE IF NOT EXISTS llm_claim_cache (
    record_id TEXT PRIMARY KEY,
    parsed_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS record_vectors (
    record_id TEXT NOT NULL REFERENCES records(id),
    chunk_index INTEGER NOT NULL,
    day INTEGER NOT NULL,
    at_utc TEXT NOT NULL,
    org TEXT NOT NULL,
    client TEXT,
    author TEXT NOT NULL,
    type TEXT NOT NULL,
    visibility TEXT NOT NULL,
    readers_json TEXT,
    content_hash TEXT NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding_blob BLOB NOT NULL,
    dim INTEGER NOT NULL,
    model TEXT NOT NULL,
    PRIMARY KEY (record_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_vectors_org_day ON record_vectors(org, day, at_utc);
CREATE INDEX IF NOT EXISTS idx_vectors_client ON record_vectors(org, client);
"""


def reset_db(path: str | None = None) -> str:
    """Remove any existing SQLite store and journal files so day 1 starts fresh."""
    target = path or db_path()
    for suffix in ("", "-wal", "-shm"):
        candidate = target + suffix
        if os.path.exists(candidate):
            os.remove(candidate)
    return target


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables, indexes, and FTS5 triggers if absent."""
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT OR IGNORE INTO meta_state(key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    conn.commit()


def get_connection(path: str | None = None) -> sqlite3.Connection:
    """Open a configured SQLite connection and guarantee schema initialization."""
    target = path or db_path()
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    init_schema(conn)
    return conn


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta_state(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_ingested_days(conn: sqlite3.Connection) -> list[int]:
    raw = get_meta(conn, "ingested_days", "[]")
    try:
        days = json.loads(raw or "[]")
        return sorted(int(d) for d in days)
    except (ValueError, TypeError):
        return []


def mark_day_ingested(conn: sqlite3.Connection, day: int) -> None:
    days = sorted(set(get_ingested_days(conn)) | {int(day)})
    set_meta(conn, "ingested_days", json.dumps(days))
    set_meta(conn, "current_day", str(max(days)))

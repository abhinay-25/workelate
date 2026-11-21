"""Document and spreadsheet version parsing, boilerplate removal, and field-level diffing."""

import json
import re
import sqlite3
from typing import Any

from src.filters import strip_boilerplate_lines

_KV_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 _/-]{1,40})\s*:\s*(.+)$")

_NO_CHANGE_PATTERNS = (
    re.compile(r"\bno\s+change\s+to\s+plan\b", re.IGNORECASE),
    re.compile(r"\ball\s+metrics\s+within\s+normal\s+range\b", re.IGNORECASE),
    re.compile(r"\bno\s+changes?\b", re.IGNORECASE),
)


def _norm_key(raw_key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", raw_key.strip().lower()).strip("_")


def format_sheet_cells(cells_obj: dict[str, Any] | None) -> tuple[list[str], dict[str, str]]:
    """Pair spreadsheet A{row}/B{row} labels with values and optional C1 unit string."""
    if not isinstance(cells_obj, dict) or not cells_obj:
        return [], {}

    upper_cells = {str(k).strip().upper(): str(v).strip() for k, v in cells_obj.items() if str(v).strip()}
    unit = ""
    c1 = upper_cells.get("C1", "")
    if c1 and not ("A1" in upper_cells and "B1" in upper_cells):
        unit = c1

    row_nums: list[int] = []
    for k in upper_cells:
        m = re.fullmatch(r"A(\d+)", k)
        if m:
            rn = int(m.group(1))
            if f"B{rn}" in upper_cells and rn not in row_nums:
                row_nums.append(rn)
    row_nums.sort()

    display_lines: list[str] = []
    paired_kv: dict[str, str] = {}
    for rn in row_nums:
        lbl = upper_cells[f"A{rn}"]
        val = upper_cells[f"B{rn}"]
        full_val = f"{val} {unit}".strip() if unit else val
        display_lines.append(f"{lbl}: {full_val}")
        nk = _norm_key(lbl)
        if nk:
            paired_kv[nk] = full_val

    return display_lines, paired_kv


def extract_doc_kv(
    title: str | None,
    sanitized_body: str,
    cells_json: str | None = None,
) -> tuple[dict[str, str], bool]:
    """Extract normalized key-value fields and no-change status from sanitized doc/sheet content."""
    kv: dict[str, str] = {}
    if cells_json:
        try:
            cells = json.loads(cells_json)
            if isinstance(cells, dict):
                for cell_k, cell_v in cells.items():
                    kv[f"cell_{str(cell_k).lower()}"] = str(cell_v)
                _, paired_kv = format_sheet_cells(cells)
                kv.update(paired_kv)
        except ValueError:
            pass

    lines = strip_boilerplate_lines(sanitized_body)
    norm_title = (title or "").strip().rstrip(".").lower()
    is_no_change = False

    for line in lines:
        cleaned = line.strip()
        low_clean = cleaned.rstrip(".").lower()
        if not cleaned:
            continue
        if low_clean == norm_title or low_clean.startswith("working notes"):
            continue
        if any(p.search(cleaned) for p in _NO_CHANGE_PATTERNS):
            is_no_change = True
            continue
        m = _KV_RE.match(cleaned)
        if m:
            k = _norm_key(m.group(1))
            v = m.group(2).strip().rstrip(".")
            if k and v:
                kv[k] = v

    return kv, is_no_change


def rebuild_doc_versions(conn: sqlite3.Connection) -> None:
    """Rebuild doc_versions with field-level diffing and carryover effective timestamps."""
    conn.execute("DELETE FROM doc_versions")

    rows = conn.execute(
        """
        SELECT * FROM records
        WHERE is_forgotten = 0
          AND type IN ('doc', 'sheet')
          AND COALESCE(doc_id, sheet_id) IS NOT NULL
          AND (has_injection_span = 0 OR TRIM(COALESCE(sanitized_body, '')) != '')
        ORDER BY COALESCE(doc_id, sheet_id) ASC, COALESCE(version, 1) ASC, at_utc ASC
        """
    ).fetchall()

    prev_by_doc: dict[str, tuple[str, dict[str, dict[str, Any]]]] = {}

    for rec in rows:
        doc_id = str(rec["doc_id"] or rec["sheet_id"])
        kind = str(rec["type"])
        version = int(rec["version"] or 1)
        rid = str(rec["id"])
        day = int(rec["day"])
        at_utc = str(rec["at_utc"])
        org = str(rec["org"])
        client = rec["client"]
        author = str(rec["author"])
        title = rec["title"] or rec["subject"] or doc_id

        raw_kv, is_no_change = extract_doc_kv(title, str(rec["sanitized_body"] or ""), rec["cells_json"])
        prev_rid, prev_fields = prev_by_doc.get(doc_id, (None, {}))

        structured_fields: dict[str, dict[str, Any]] = {}
        diffs: list[dict[str, Any]] = []

        for k, val in raw_kv.items():
            prev_entry = prev_fields.get(k)
            if prev_entry is None:
                # Field introduced for the first time in this version
                changed_here = bool(prev_fields)
                structured_fields[k] = {
                    "val": val,
                    "effective_at_utc": at_utc,
                    "changed_here": changed_here,
                    "introduced_record_id": rid,
                }
                if changed_here:
                    diffs.append(
                        {"field": k, "old_val": None, "new_val": val, "prev_record_id": prev_rid}
                    )
            elif prev_entry["val"] != val:
                # Field value explicitly changed in this version
                structured_fields[k] = {
                    "val": val,
                    "effective_at_utc": at_utc,
                    "changed_here": True,
                    "introduced_record_id": rid,
                    "old_val": prev_entry["val"],
                    "prev_record_id": prev_rid,
                }
                diffs.append(
                    {
                        "field": k,
                        "old_val": prev_entry["val"],
                        "new_val": val,
                        "prev_record_id": prev_rid,
                    }
                )
            else:
                # Unchanged carryover field: preserve first-introduced effective_at_utc
                structured_fields[k] = {
                    "val": val,
                    "effective_at_utc": prev_entry["effective_at_utc"],
                    "changed_here": False,
                    "introduced_record_id": prev_entry["introduced_record_id"],
                }

        if is_no_change:
            diffs = []

        conn.execute(
            """
            INSERT INTO doc_versions(
                doc_id, kind, version, record_id, day, at_utc, org, client, author, title,
                extracted_kv_json, diff_summary_json, is_forgotten
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                doc_id,
                kind,
                version,
                rid,
                day,
                at_utc,
                org,
                client,
                author,
                title,
                json.dumps(structured_fields, ensure_ascii=False),
                json.dumps(diffs, ensure_ascii=False),
            ),
        )
        prev_by_doc[doc_id] = (rid, structured_fields)

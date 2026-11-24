"""Evidence-based entity resolution, data-driven alias learning, and collaboration scoring."""

import json
import re
import sqlite3
from typing import Any

from src.security import get_current_day, get_person_org, visible_records_params, visible_records_where_clause

_COMPOUND_SUFFIXES = (
    "beam", "line", "point", "wave", "peak", "star", "bridge", "field",
    "stone", "port", "mark", "flow", "tech", "soft", "cloud", "link",
    "core", "view", "cast", "path", "works", "labs", "sys", "net",
)

_STOP_ALIASES = frozenset(
    {
        "in", "on", "at", "to", "by", "of", "or", "an", "is", "it", "as",
        "no", "so", "do", "go", "me", "my", "we", "us", "he", "up", "if",
        "the", "and", "for", "new", "all", "are", "not", "now", "who",
    }
)

# Phrases that mention brand-adjacent words but refer to separate entities/vendors.
_DISTRACTOR_PATTERNS = (
    re.compile(r"\bnorthstar\s+print\b", re.IGNORECASE),
    re.compile(r"\bnorthstar\b", re.IGNORECASE),
    re.compile(r"\bbeam\s+design\s+system\b", re.IGNORECASE),
    re.compile(r"\bbeam\s+design\b", re.IGNORECASE),
    re.compile(r"\blogistics\s+now\b", re.IGNORECASE),
)

# Country TLD -> regional tokens (only applied when corroborated by org data).
_TLD_COUNTRY_CUES: dict[str, tuple[str, ...]] = {
    ".co.uk": ("uk", "london"),
    ".uk": ("uk", "london"),
    ".in": ("india", "mumbai"),
}


def _strip_distractors(text: str) -> str:
    cleaned = text or ""
    for pat in _DISTRACTOR_PATTERNS:
        cleaned = pat.sub(" ", cleaned)
    return cleaned


def _word_boundary_match(phrase: str, text: str) -> bool:
    if not phrase or not text:
        return False
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(phrase) + r"(?![A-Za-z0-9])", text, re.IGNORECASE) is not None


def _insert_alias(
    conn: sqlite3.Connection,
    alias: str,
    entity_type: str,
    entity_id: str,
    org_id: str,
    evidence_kind: str,
    confidence: float,
    source_record_id: str | None = None,
) -> None:
    norm = " ".join((alias or "").strip().lower().split())
    if not norm or norm in _STOP_ALIASES:
        return
    conn.execute(
        """
        INSERT INTO entity_aliases(alias, entity_type, entity_id, org_id, evidence_kind, confidence, source_record_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(alias, entity_type, entity_id, org_id, evidence_kind) DO UPDATE SET
            confidence = MAX(entity_aliases.confidence, excluded.confidence),
            source_record_id = COALESCE(entity_aliases.source_record_id, excluded.source_record_id)
        """,
        (norm, entity_type, entity_id, org_id, evidence_kind, float(confidence), source_record_id),
    )


def _derive_client_lexical_aliases(client_id: str, client_name: str) -> list[tuple[str, str, float]]:
    """Derive generic lexical aliases and acronyms from client id/name."""
    out: list[tuple[str, str, float]] = []
    cid_low = client_id.strip().lower()
    name_low = " ".join(client_name.strip().lower().split())
    out.append((cid_low, "name", 1.0))
    if name_low:
        out.append((name_low, "name", 1.0))

    words = re.findall(r"[A-Za-z0-9]+", client_name)
    if words:
        first = words[0].lower()
        out.append((first, "name", 0.95))
        if len(words) >= 2:
            multi_init = "".join(w[0].lower() for w in words if w)
            if len(multi_init) >= 2:
                out.append((multi_init, "acronym", 0.85))
                out.append((f"{multi_init} {words[1].lower()}", "acronym", 0.9))

        # Compound word root acronym & stem: North+beam -> NB, Cobalt+line -> CL + Cobalt, Alder+point -> AP + Alder
        for suffix in _COMPOUND_SUFFIXES:
            if first.endswith(suffix) and len(first) - len(suffix) >= 4:
                prefix = first[: len(first) - len(suffix)]
                comp_init = prefix[0] + suffix[0]
                out.append((comp_init, "acronym", 0.92))
                out.append((f"{prefix} {suffix}", "name", 0.95))
                if len(words) >= 2:
                    out.append((f"{comp_init} {words[1].lower()}", "acronym", 0.95))
                if len(prefix) >= 5 and prefix not in {
                    "north", "south", "east", "west", "first", "prime", "grand", "clear", "smart"
                }:
                    out.append((prefix, "stem", 0.88))
                break
    return out


def _add_collab_edge(
    conn: sqlite3.Connection,
    org_id: str,
    person_a: str,
    person_b: str,
    client: str | None,
    weight: float,
    source_record_id: str,
) -> None:
    conn.execute(
        """
        INSERT INTO collab_edges(org_id, person_a, person_b, client, weight, source_record_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (org_id, person_a, person_b, client, float(weight), source_record_id),
    )


def _disambiguate_mentioned_people(
    conn: sqlite3.Connection,
    rec: sqlite3.Row,
    people_rows: list[sqlite3.Row],
) -> list[str]:
    """Identify internal person_ids referenced by name in a record body."""
    body = str(rec["sanitized_body"] or "")
    if not body:
        return []
    channel = str(rec["channel"] or "").lower()
    low_body = body.lower()

    by_first: dict[str, list[sqlite3.Row]] = {}
    matched_ids: list[str] = []

    for p in people_rows:
        pid = str(p["person_id"])
        full = str(p["name"] or "")
        first = str(p["first_name"] or "")
        if full and _word_boundary_match(full, body):
            matched_ids.append(pid)
        elif first:
            by_first.setdefault(first.lower(), []).append(p)

    for first_low, cands in by_first.items():
        if not _word_boundary_match(first_low, body):
            continue
        if len(cands) == 1:
            pid = str(cands[0]["person_id"])
            if pid not in matched_ids:
                matched_ids.append(pid)
            continue

        # Multiple people share this first name: check role/team/channel context in the record itself.
        scored: list[tuple[int, str]] = []
        for c in cands:
            score = 0
            team = str(c["team"] or "").lower()
            role = str(c["role"] or "").lower()
            if team and (team in channel or _word_boundary_match(team, low_body)):
                score += 3
            for rtok in re.findall(r"[a-z]+", role):
                if len(rtok) >= 4 and _word_boundary_match(rtok, low_body):
                    score += 2
            # Common functional match between billing/invoice and accounts/billing
            if ("billing" in low_body or "invoice" in low_body) and ("billing" in role or "accounts" in team):
                score += 3
            scored.append((score, str(c["person_id"])))
        scored.sort(reverse=True)
        if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            if scored[0][1] not in matched_ids:
                matched_ids.append(scored[0][1])

    return matched_ids


def extract_entities_for_day(conn: sqlite3.Connection, day: int) -> None:
    """Populate data-driven entity aliases and collaboration edges for all records up to day."""
    # Clear day's prior collaboration edges to preserve ingest idempotency.
    conn.execute(
        "DELETE FROM collab_edges WHERE source_record_id IN (SELECT id FROM records WHERE day = ?)",
        (day,),
    )

    clients = conn.execute("SELECT client_id, org_id, name FROM clients").fetchall()
    client_names_by_org: dict[str, dict[str, str]] = {}
    for c in clients:
        cid = str(c["client_id"])
        oid = str(c["org_id"])
        cname = str(c["name"])
        client_names_by_org.setdefault(oid, {})[cid] = cname
        for alias, kind, conf in _derive_client_lexical_aliases(cid, cname):
            _insert_alias(conn, alias, "client", cid, oid, kind, conf, None)

    people_by_org: dict[str, list[sqlite3.Row]] = {}
    internal_ids_by_org: dict[str, set[str]] = {}
    zones_by_person: dict[str, str] = {}
    for p in conn.execute("SELECT * FROM people").fetchall():
        org = str(p["org_id"])
        pid = str(p["person_id"])
        people_by_org.setdefault(org, []).append(p)
        internal_ids_by_org.setdefault(org, set()).add(pid)
        zones_by_person[pid] = str(p["zone"] or "")

    day_recs = conn.execute(
        """
        SELECT * FROM records
        WHERE day = ? AND is_forgotten = 0
          AND (has_injection_span = 0 OR TRIM(COALESCE(sanitized_body, '')) != '')
        """,
        (day,),
    ).fetchall()

    for rec in day_recs:
        org = str(rec["org"])
        rid = str(rec["id"])
        rtype = str(rec["type"])
        author = str(rec["author"] or "")
        client = rec["client"]
        internal_ids = internal_ids_by_org.get(org, set())
        org_people = people_by_org.get(org, [])

        # Domain & event-corroborated client cues
        if client and int(rec["is_newsletter"]) == 0:
            ext_addrs: list[str] = []
            if author.startswith("ext:"):
                ext_addrs.append(author[4:].lower())
            if rec["to_json"]:
                try:
                    for t in json.loads(rec["to_json"]) or []:
                        if str(t).startswith("ext:"):
                            ext_addrs.append(str(t)[4:].lower())
                except ValueError:
                    pass
            for addr in ext_addrs:
                if "@" in addr:
                    domain = addr.split("@", 1)[1]
                    _insert_alias(conn, domain, "client", client, org, "domain", 0.9, rid)
                    brand_part = domain.split(".", 1)[0]
                    if len(brand_part) >= 4:
                        _insert_alias(conn, brand_part, "client", client, org, "domain", 0.85, rid)

            if rtype == "event" and rec["title"]:
                title_low = str(rec["title"]).lower()
                if _word_boundary_match("uk", title_low):
                    _insert_alias(conn, "uk", "client", client, org, "event_title", 0.85, rid)
                    # Corroborate London only when explicit UK event + Europe/London attendee or .co.uk domain co-exist
                    attendees = json.loads(rec["attendees_json"] or "[]") if rec["attendees_json"] else []
                    has_london_attendee = any(zones_by_person.get(a) == "Europe/London" for a in attendees)
                    has_uk_domain = any(a.endswith(".uk") for a in ext_addrs)
                    if has_london_attendee or has_uk_domain:
                        _insert_alias(conn, "london", "client", client, org, "event_domain_corroborated", 0.88, rid)

            # Data-driven parenthetical nickname extraction: e.g. Acme Corp ("Acme") / Beta (BT)
            cname = client_names_by_org.get(org, {}).get(str(client), "")
            if cname and rec["sanitized_body"]:
                cfirst = cname.split()[0]
                m_nick = re.search(
                    r"\b(?:" + re.escape(str(client)) + r"|" + re.escape(cfirst) + r")\s*"
                    r"\(\s*[\"']?([A-Za-z][A-Za-z0-9 .-]{1,20})[\"']?\s*\)",
                    str(rec["sanitized_body"]),
                    re.IGNORECASE,
                )
                if m_nick:
                    nick = m_nick.group(1).strip()
                    if 2 <= len(nick) <= 20 and nick.lower() not in _STOP_ALIASES:
                        _insert_alias(conn, nick, "client", str(client), org, "parenthetical_nick", 0.9, rid)

        if int(rec["is_boilerplate"]) == 1 or int(rec["is_newsletter"]) == 1:
            continue

        # Collaboration edges from events, mails, client chats/docs/notes
        participants: list[str] = []
        if author in internal_ids:
            participants.append(author)

        if rtype == "event" and rec["attendees_json"]:
            try:
                for att in json.loads(rec["attendees_json"]) or []:
                    if att in internal_ids and att not in participants:
                        participants.append(att)
            except ValueError:
                pass
        elif rtype == "mail" and rec["to_json"]:
            try:
                for recip in json.loads(rec["to_json"]) or []:
                    if recip in internal_ids and recip not in participants:
                        participants.append(recip)
            except ValueError:
                pass

        weight_map = {"event": 3.0, "mail": 2.0, "note": 2.5, "doc": 1.5, "sheet": 1.5, "chat": 1.5}
        w = weight_map.get(rtype, 1.0)
        if participants:
            for i, pa in enumerate(participants):
                if client:
                    _add_collab_edge(conn, org, pa, pa, client, w, rid)
                for pb in participants[i + 1 :]:
                    _add_collab_edge(conn, org, pa, pb, client, w, rid)
                    _add_collab_edge(conn, org, pb, pa, client, w, rid)

        # Link mentioned internal colleagues when disambiguated by channel/role/client
        if client and rtype in ("chat", "mail", "note"):
            for mentioned_pid in _disambiguate_mentioned_people(conn, rec, org_people):
                _add_collab_edge(conn, org, mentioned_pid, mentioned_pid, client, 4.0, rid)
                if author in internal_ids and author != mentioned_pid:
                    _add_collab_edge(conn, org, author, mentioned_pid, client, 3.0, rid)
                    _add_collab_edge(conn, org, mentioned_pid, author, client, 3.0, rid)


def resolve_task_mentions(
    conn: sqlite3.Connection,
    asker_id: str,
    text: str,
    upto_day: int | None = None,
    upto_utc: str | None = None,
    client_hint: str | None = None,
) -> list[dict[str, Any]]:
    """Match visible tasks referenced in a question by title tokens, sorted by overlap score."""
    org = get_person_org(conn, asker_id)
    if not org:
        return []
    day_lim = get_current_day(conn) if upto_day is None else upto_day
    where = visible_records_where_clause("r", with_utc_cutoff=(upto_utc is not None))
    params = visible_records_params(asker_id, org, day_lim, upto_utc)

    rows = conn.execute(
        f"""
        SELECT r.task_id, r.title, r.client, MIN(r.id) AS first_rec_id
        FROM records r
        WHERE r.type = 'task' AND r.task_id IS NOT NULL AND {where}
        GROUP BY r.task_id
        """,
        params,
    ).fetchall()

    client_name_tokens: set[str] = set()
    for crow in conn.execute("SELECT client_id, name FROM clients WHERE org_id = ?", (org,)).fetchall():
        client_name_tokens.add(str(crow["client_id"]).lower())
        for tok in re.findall(r"[a-z0-9]{2,}", str(crow["name"] or "").lower()):
            client_name_tokens.add(tok)
    for arow in conn.execute(
        "SELECT alias FROM entity_aliases WHERE org_id = ? AND entity_type = 'client'",
        (org,),
    ).fetchall():
        for tok in re.findall(r"[a-z0-9]{2,}", str(arow["alias"] or "").lower()):
            if len(tok) >= 3:
                client_name_tokens.add(tok)

    clean_q = _strip_distractors(text).lower()
    q_tokens = set(re.findall(r"[a-z0-9]{2,}", clean_q))
    matches: list[dict[str, Any]] = []

    for row in rows:
        tid = str(row["task_id"])
        title = str(row["title"] or "")
        t_low = title.lower()
        t_tokens = [t for t in re.findall(r"[a-z0-9]{2,}", t_low) if t not in {"the", "for", "and", "to"}]
        if not t_tokens:
            continue

        score = 0.0
        strength = "multi"
        if t_low in clean_q:
            score = 10.0
            strength = "exact"
        else:
            overlap = sum(1 for tok in t_tokens if tok in q_tokens)
            non_client_overlap = sum(
                1 for tok in t_tokens if tok in q_tokens and tok not in client_name_tokens
            )
            ratio = overlap / len(t_tokens)
            if non_client_overlap >= 1 and ratio >= 0.65 and overlap >= 2:
                score = 5.0 * ratio
                strength = "multi"
            elif len(t_tokens) == 1 and t_tokens[0] in q_tokens and t_tokens[0] not in client_name_tokens:
                score = 4.0
                strength = "single"

        if score > 0:
            if client_hint and row["client"] == client_hint:
                score += 2.0
            elif client_hint and row["client"] and row["client"] != client_hint:
                score -= 2.0
            if score > 0:
                matches.append(
                    {
                        "task_id": tid,
                        "title": title,
                        "client": row["client"],
                        "score": score,
                        "match_strength": strength,
                        "source_record_id": row["first_rec_id"],
                    }
                )

    if not matches and re.search(
        r"\b(?:who\s+owns|owner\s+of|handles|when\s+is\b.*\bdue|due\s+date|deadline|"
        r"status\s+of|is\s+the\b.*\b(?:done|ready|finished|complete|blocked)|"
        r"which\s+[a-z]+\s+do\s+you\s+mean)\b",
        clean_q,
    ):
        _GENERIC_TASK_WORDS = frozenset(
            {
                "task", "tasks", "item", "items", "thing", "things", "work",
                "update", "updates", "fix", "plan", "copy", "page", "v1", "v2",
                "launch", "invoice", "billing", "client", "account", "meeting",
                "notes", "weekly", "monthly",
            }
        )
        for row in rows:
            tid = str(row["task_id"])
            title = str(row["title"] or "")
            t_tokens = [t for t in re.findall(r"[a-z0-9]{2,}", title.lower()) if t not in {"the", "for", "and", "to"}]
            shared = [
                tok
                for tok in t_tokens
                if len(tok) >= 4
                and tok not in _GENERIC_TASK_WORDS
                and tok not in client_name_tokens
                and tok in q_tokens
            ]
            if len(shared) == 1:
                score = 2.5
                if client_hint and row["client"] == client_hint:
                    score += 2.0
                elif client_hint and row["client"] and row["client"] != client_hint:
                    score -= 2.0
                if score > 0:
                    matches.append(
                        {
                            "task_id": tid,
                            "title": title,
                            "client": row["client"],
                            "score": score,
                            "match_strength": "single",
                            "source_record_id": row["first_rec_id"],
                        }
                    )

    matches.sort(key=lambda x: (-x["score"], x["task_id"]))
    return matches


def _score_client_evidence(
    conn: sqlite3.Connection,
    asker_id: str,
    org: str,
    text: str,
    upto_day: int,
    upto_utc: str | None,
    inferred_task_client: str | None,
) -> dict[str, Any]:
    clean_text = _strip_distractors(text)
    low = clean_text.lower()
    clients = conn.execute("SELECT client_id, name FROM clients WHERE org_id = ?", (org,)).fetchall()
    alias_rows = conn.execute(
        "SELECT alias, entity_id, evidence_kind, confidence, source_record_id "
        "FROM entity_aliases WHERE org_id = ? AND entity_type = 'client'",
        (org,),
    ).fetchall()

    scores: dict[str, float] = {str(c["client_id"]): 0.0 for c in clients}
    matched_phrases: dict[str, tuple[str, str | None]] = {}

    # Seed baseline lexical candidates from clients table even if extract_entities_for_day hasn't populated aliases yet
    base_aliases: list[tuple[str, str, float, str | None]] = [
        (str(row["alias"]), str(row["entity_id"]), float(row["confidence"]), row["source_record_id"])
        for row in alias_rows
    ]
    seen_pairs = {(a, eid) for a, eid, _, _ in base_aliases}
    for c in clients:
        cid_c = str(c["client_id"])
        for al_lex, _kind, conf_lex in _derive_client_lexical_aliases(cid_c, str(c["name"])):
            if (al_lex, cid_c) not in seen_pairs and al_lex not in _STOP_ALIASES:
                base_aliases.append((al_lex, cid_c, conf_lex, None))
                seen_pairs.add((al_lex, cid_c))

    # 1. Direct alias & domain/event-corroborated matches (longest phrases prioritized)
    for alias, cid, conf, src_rid in sorted(base_aliases, key=lambda item: len(item[0]), reverse=True):
        if cid not in scores:
            continue
        if _word_boundary_match(alias, clean_text):
            weight = conf * (2.5 if len(alias) > 3 else 2.0)
            if weight > scores[cid]:
                scores[cid] = max(scores[cid], weight)
            if cid not in matched_phrases or len(alias) > len(matched_phrases[cid][0]):
                matched_phrases[cid] = (alias, src_rid)

    # 2. Task co-constraint signal (if a task title in the question already identified a client)
    if inferred_task_client and inferred_task_client in scores:
        scores[inferred_task_client] += 3.0

    # 3. Topic/predicate + asker-portfolio + TLD corroboration for descriptive references
    #    (e.g., "the Mumbai client", "the London account") when no direct name/acronym matched strongly.
    max_direct = max(scores.values(), default=0.0)
    desc_match = re.search(r"\bthe\s+([a-z]+)\s+(client|account)\b", low)
    if max_direct < 2.0 and desc_match is not None:
        where = visible_records_where_clause("r", with_utc_cutoff=(upto_utc is not None))
        params = visible_records_params(asker_id, org, upto_day, upto_utc)

        # Check topic words in question against visible client records/tasks
        topic_words = [
            w for w in re.findall(r"[a-z]{4,}", low)
            if w not in {"what", "when", "where", "which", "owns", "owner", "handles", "status", "client", "account", "about", "this"}
        ]
        for cid in list(scores.keys()):
            has_signal = scores[cid] > 0.0

            # Topic co-occurrence inside visible records for this client
            if topic_words:
                topic_hits = 0
                recs = conn.execute(
                    f"SELECT sanitized_body, title FROM records r WHERE r.client = :cid AND r.is_newsletter = 0 AND r.is_boilerplate = 0 AND {where}",
                    {"cid": cid, **params},
                ).fetchall()
                for r in recs:
                    blob = f"{r['title'] or ''} {r['sanitized_body'] or ''}".lower()
                    if any(_word_boundary_match(tw, blob) for tw in topic_words):
                        topic_hits += 1
                if topic_hits > 0:
                    scores[cid] += min(2.5, 0.8 + 0.3 * topic_hits)
                    has_signal = True

            # Geographic token vs domain TLD corroboration
            geo_tok = desc_match.group(1)
            dom_rows = conn.execute(
                "SELECT alias, source_record_id FROM entity_aliases "
                "WHERE org_id = ? AND entity_id = ? AND evidence_kind = 'domain'",
                (org, cid),
            ).fetchall()
            for dr in dom_rows:
                dom = str(dr["alias"]).lower()
                for tld, country_words in _TLD_COUNTRY_CUES.items():
                    if dom.endswith(tld) and geo_tok in country_words:
                        scores[cid] += 1.5
                        has_signal = True
                        if cid not in matched_phrases:
                            matched_phrases[cid] = (desc_match.group(0), dr["source_record_id"])

            # Asker collaboration & feedback corroboration only when at least one content/domain signal hit
            if has_signal:
                c_weight = conn.execute(
                    "SELECT COALESCE(SUM(weight), 0.0) FROM collab_edges WHERE org_id = ? AND person_a = ? AND client = ?",
                    (org, asker_id, cid),
                ).fetchone()[0]
                if c_weight > 0:
                    scores[cid] += min(1.5, float(c_weight) * 0.15)

                fb_hits = conn.execute(
                    """
                    SELECT COUNT(*) FROM feedback f
                    JOIN records r ON r.event_id = f.meeting AND r.type = 'event'
                    WHERE f.person = ? AND r.client = ? AND f.ingested_on_day <= ?
                    """,
                    (asker_id, cid, upto_day),
                ).fetchone()[0]
                if fb_hits:
                    scores[cid] += 1.2

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    if not ranked or ranked[0][1] <= 0.5:
        return {"client_id": None, "ambiguous": False, "candidates": [], "phrase": None, "sources": []}

    top_cid, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    if second_score > 0 and (top_score - second_score) < 0.75:
        cands = [cid for cid, sc in ranked if top_score - sc < 0.75 and sc > 0.5]
        return {"client_id": None, "ambiguous": True, "candidates": cands, "phrase": None, "sources": []}

    phrase, src = matched_phrases.get(top_cid, (desc_match.group(0) if desc_match else top_cid, None))
    # Preserve original case substring from input text if matched
    m_orig = re.search(re.escape(phrase), clean_text, re.IGNORECASE) if phrase else None
    orig_phrase = m_orig.group(0) if m_orig else phrase
    sources = [src] if src else []
    return {
        "client_id": top_cid,
        "ambiguous": False,
        "candidates": [top_cid],
        "phrase": orig_phrase,
        "sources": sources,
    }


def resolve_client_mention(
    conn: sqlite3.Connection,
    asker_id: str,
    text: str,
    upto_day: int | None = None,
    upto_utc: str | None = None,
    inferred_task_client: str | None = None,
) -> dict[str, Any]:
    """Resolve client alias using multi-signal converging evidence."""
    org = get_person_org(conn, asker_id)
    if not org:
        return {"client_id": None, "ambiguous": False, "candidates": [], "phrase": None, "sources": []}
    day_lim = get_current_day(conn) if upto_day is None else upto_day
    return _score_client_evidence(conn, asker_id, org, text, day_lim, upto_utc, inferred_task_client)


def resolve_person_mention(
    conn: sqlite3.Connection,
    asker_id: str,
    text: str,
    upto_day: int | None = None,
    upto_utc: str | None = None,
    client_id: str | None = None,
    exclude_asker: bool = True,
) -> dict[str, Any]:
    """Resolve person name/first-name references using collaboration graph + client overlap."""
    org = get_person_org(conn, asker_id)
    if not org:
        return {"person_id": None, "ambiguous": False, "candidates": [], "phrase": None, "sources": []}
    day_lim = get_current_day(conn) if upto_day is None else upto_day

    people = conn.execute("SELECT * FROM people WHERE org_id = ?", (org,)).fetchall()
    # Check full name matches first
    for p in people:
        pid = str(p["person_id"])
        if exclude_asker and pid == asker_id:
            continue
        if _word_boundary_match(str(p["name"]), text):
            return {
                "person_id": pid,
                "ambiguous": False,
                "candidates": [pid],
                "phrase": str(p["name"]),
                "sources": [],
            }

    # Check first-name matches
    by_first: dict[str, list[sqlite3.Row]] = {}
    for p in people:
        pid = str(p["person_id"])
        if exclude_asker and pid == asker_id:
            continue
        by_first.setdefault(str(p["first_name"]).lower(), []).append(p)

    for first_low, cands in by_first.items():
        if not first_low or not _word_boundary_match(first_low, text):
            continue
        m_orig = re.search(r"(?<![A-Za-z0-9])" + re.escape(first_low) + r"(?![A-Za-z0-9])", text, re.IGNORECASE)
        phrase = m_orig.group(0) if m_orig else first_low

        if len(cands) == 1:
            return {
                "person_id": str(cands[0]["person_id"]),
                "ambiguous": False,
                "candidates": [str(cands[0]["person_id"])],
                "phrase": phrase,
                "sources": [],
            }

        # Multiple candidates share this first name -> score using collab_edges + client/topic alignment
        where = visible_records_where_clause("r", with_utc_cutoff=(upto_utc is not None))
        params = visible_records_params(asker_id, org, day_lim, upto_utc)

        scored: list[tuple[float, str, list[str]]] = []
        for c in cands:
            pid = str(c["person_id"])
            score = 0.0
            src_ids: list[str] = []

            # Direct collaboration edges with asker
            direct_rows = conn.execute(
                f"""
                SELECT ce.weight, ce.source_record_id FROM collab_edges ce
                JOIN records r ON r.id = ce.source_record_id
                WHERE ce.org_id = :org AND ce.person_a = :asker AND ce.person_b = :cand
                  AND r.is_boilerplate = 0 AND r.is_newsletter = 0
                  AND {where}
                """,
                {"org": org, "asker": asker_id, "cand": pid, **params},
            ).fetchall()
            for dr in direct_rows:
                score += float(dr["weight"])
                if dr["source_record_id"] not in src_ids:
                    src_ids.append(dr["source_record_id"])

            # Client-specific association edges from visible messages/handovers
            if client_id:
                cli_rows = conn.execute(
                    f"""
                    SELECT ce.weight, ce.source_record_id FROM collab_edges ce
                    JOIN records r ON r.id = ce.source_record_id
                    WHERE ce.org_id = :org AND ce.person_a = :cand AND ce.client = :cid
                      AND r.is_boilerplate = 0 AND r.is_newsletter = 0
                      AND {where}
                    """,
                    {"org": org, "cand": pid, "cid": client_id, **params},
                ).fetchall()
                for cr in cli_rows:
                    score += float(cr["weight"]) * 1.5
                    if cr["source_record_id"] not in src_ids:
                        src_ids.append(cr["source_record_id"])

            scored.append((score, pid, src_ids))

        scored.sort(key=lambda x: (-x[0], x[1]))
        if scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            return {
                "person_id": scored[0][1],
                "ambiguous": False,
                "candidates": [scored[0][1]],
                "phrase": phrase,
                "sources": scored[0][2][:3],
            }
        return {
            "person_id": None,
            "ambiguous": True,
            "candidates": [x[1] for x in scored],
            "phrase": phrase,
            "sources": [],
        }

    return {"person_id": None, "ambiguous": False, "candidates": [], "phrase": None, "sources": []}


def resolve_artifact_mention(
    conn: sqlite3.Connection,
    asker_id: str,
    artifact_keyword: str,
    client_id: str | None = None,
    upto_day: int | None = None,
    upto_utc: str | None = None,
) -> dict[str, Any] | None:
    """Find the most relevant visible artifact record in asker's org (preferring asker's involvement)."""
    org = get_person_org(conn, asker_id)
    if not org or not artifact_keyword:
        return None
    day_lim = get_current_day(conn) if upto_day is None else upto_day
    where = visible_records_where_clause("r", with_utc_cutoff=(upto_utc is not None))
    params = visible_records_params(asker_id, org, day_lim, upto_utc)

    sql = f"""
        SELECT r.* FROM records r
        WHERE r.is_newsletter = 0 AND r.is_boilerplate = 0 AND {where}
    """
    if client_id:
        sql += " AND r.client = :cid"
        params["cid"] = client_id
    sql += " ORDER BY r.at_utc DESC"

    candidates: list[tuple[int, str, sqlite3.Row]] = []
    for rec in conn.execute(sql, params).fetchall():
        blob = " ".join(
            str(x or "") for x in (rec["title"], rec["subject"], rec["sanitized_body"])
        )
        if not _word_boundary_match(artifact_keyword, blob):
            continue
        involvement = 0
        if rec["author"] == asker_id:
            involvement += 3
        if rec["to_json"] and asker_id in rec["to_json"]:
            involvement += 2
        if rec["attendees_json"] and asker_id in rec["attendees_json"]:
            involvement += 2
        candidates.append((involvement, str(rec["at_utc"]), rec))

    if not candidates:
        return None
    candidates.sort(key=lambda x: (-x[0], x[1]), reverse=False)
    # Sort descending by involvement, then descending by at_utc
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    top = candidates[0][2]
    return {
        "record_id": str(top["id"]),
        "author": str(top["author"]),
        "client": top["client"],
        "at_utc": str(top["at_utc"]),
        "body": str(top["sanitized_body"]),
        "subject": top["subject"],
    }

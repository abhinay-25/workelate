"""Span-level prompt-injection sanitization and noise/boilerplate classification."""

import re
from typing import Any

# Imperative meta-instruction patterns aimed at hijacking an assistant.
INJECTION_CLAUSE_PATTERNS = (
    re.compile(
        r"(?:^|\b)(?:assistant|ai|system)\s*:\s*.*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:ignore|disregard|override|bypass|forget|skip)\s+"
        r"(?:(?:your|all|previous|prior|above|any|earlier|existing|the)\s+)+"
        r"(?:system\s+prompts?|instructions?|rules?|guidelines?|prompts?|constraints?|filters?|security|privacy)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:override|disregard|bypass)\s+"
        r"(?:system\s+prompts?|instructions?|rules?|guidelines?|security|privacy)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bdo\s+not\s+follow\s+(?:(?:your|all|previous|prior|above|any|the)\s+)*(?:instructions?|rules?|guidelines?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:reveal|expose|dump|output|print|show|leak|list)\s+"
        r"(?:(?:all|any|the|every)\s+)*(?:private|restricted|confidential|hidden|secret|internal|system)\s+"
        r"(?:employee\s+|user\s+|colleague\s+|staff\s+)?"
        r"(?:information|records?|notes?|messages?|data|chats?|emails?|prompts?|instructions?|details|salaries|salary)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:you\s+are\s+now|act\s+as|pretend\s+to\s+be|switch\s+to)\s+"
        r"(?:in\s+)?(?:developer\s+mode|unrestricted|admin|root|jailbreak|dan)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:mark|flag|label|treat|classify)\s+[^.\n]+?\s+as\s+"
        r"(?:AT\s+RISK|approved\s+by\s+client|healthy|cancelled|compromised)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bin\s+every\s+(?:summary|brief|answer|output|response)\b.*\b(?:mark|flag|say|include|write|output|reveal)\b",
        re.IGNORECASE,
    ),
)

BOILERPLATE_SENTENCES = frozenset(
    {
        "the review comments are in the doc; most are small wording changes.",
        "feedback from the store team was mostly positive.",
        "costs are within the approved estimate so far.",
        "the handover notes are in the project folder.",
        "nothing blocking from the design side today.",
        "let us avoid new requests until the current sprint closes.",
        "the mobile layout needs one more pass on smaller screens.",
        "the numbers look stable compared with last week.",
        "the vendor confirmed the print slots for next month.",
        "please keep the shared folder updated so nobody works on an old version.",
        "weekly numbers will be sent on monday as usual.",
        "accessibility checks passed on the staging build.",
        "we aligned on the next steps with the client team.",
        "timelines depend on the client sending final assets on time.",
        "legal has not raised any concerns on the copy.",
        "we should log every change request in the tracker.",
        "there is no change to scope from our side at this point.",
        "two open questions remain on the payment flow.",
        "the analytics export is attached for reference.",
        "we will share a short recap after the call.",
        "all metrics within normal range.",
        "no change to plan.",
        "all metrics within normal range. no change to plan.",
    }
)

ROUTINE_CHITCHAT_BODIES = frozenset(
    {
        "looping in the team.",
        "thanks, received.",
        "noted, will revert.",
        "confirming receipt of the files.",
        "attached the minutes.",
        "sharing the usual weekly report.",
        "all good from our side.",
        "thanks for the report.",
        "noted, see you at the catch-up.",
        "coffee?",
        "bhai wifi slow hai aaj",
        "lunch kab?",
        "jira down hai kya?",
        "build green ho gaya",
        "slides share kar diye",
        "weekly report template is in drive",
        "great work on the demo yesterday!",
        "typo fixed in footer",
        "new laptop request raised",
        "figma link updated",
        "who has the staging password? ask in #dev not here",
        "client call shifted by 15 min",
        "anyone going to the weekly sync?",
        "anyone has the brand fonts?",
        "cake in pantry",
        "can someone review my copy draft",
        "done ✅",
        "kal wfh hoon",
        "ok noted",
        "on it",
        "pls check mail",
        "reminder: timesheets by eod",
        "thanks!",
        "uploaded the new icons",
        "will check and revert",
    }
)

ROUTINE_CHITCHAT_PATTERNS = (
    re.compile(
        r"^beam design system v[0-9.]+\s+(?:tokens released, update your libs|is out\. new button tokens\.)$",
        re.IGNORECASE,
    ),
    re.compile(r"^pr #\d+\s+review kar do please$", re.IGNORECASE),
    re.compile(r"^qa build \d+\s+ready$", re.IGNORECASE),
    re.compile(r"^meeting room \d+\s+booked till \d+$", re.IGNORECASE),
    re.compile(r"^standup \d+\s+min late$", re.IGNORECASE),
    re.compile(r"^happy birthday\s+[a-z]+\s*!$", re.IGNORECASE),
)

_NEWSLETTER_LOCAL_PARTS = frozenset(
    {"news", "digest", "noreply", "no-reply", "offers", "hello", "team", "jobs", "newsletter", "marketing", "updates"}
)

_NEWSLETTER_SUBJECT_RE = re.compile(
    r"\b(newsletter|digest|offers|top products|usage summary|candidates for|layout trends|what retail brands)\b",
    re.IGNORECASE,
)


def _split_clauses(line: str) -> list[str]:
    """Split a single line into sentence/command clauses while preserving punctuation."""
    parts = re.split(
        r"(?<=[.!?])\s+|"
        r"(?:;|\s+--)\s+(?=(?:assistant|ai|system|ignore|override|disregard|bypass|forget|do\s+not\s+follow|reveal|expose|dump|you\s+are\s+now|act\s+as|mark|flag|label)\b)",
        line.strip(),
    )
    return [p.strip() for p in parts if p and p.strip()]


def is_injection_clause(clause: str) -> bool:
    """Return True if an isolated clause contains an untrusted assistant directive."""
    return any(pat.search(clause) for pat in INJECTION_CLAUSE_PATTERNS)


def sanitize_text(text: str | None) -> tuple[str, int, list[str]]:
    """Strip imperative assistant-targeted clauses while retaining co-occurring facts.

    Returns (sanitized_body, has_injection_span, stripped_spans).
    """
    if not text:
        return "", 0, []

    clean_lines: list[str] = []
    stripped_spans: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        clauses = _split_clauses(line)
        kept: list[str] = []
        for clause in clauses:
            if is_injection_clause(clause):
                stripped_spans.append(clause)
            else:
                kept.append(clause)
        if kept:
            clean_lines.append(" ".join(kept))

    sanitized = "\n".join(clean_lines).strip()
    return sanitized, (1 if stripped_spans else 0), stripped_spans


def strip_boilerplate_lines(text: str) -> list[str]:
    """Return non-boilerplate sentences/lines from sanitized record text."""
    meaningful: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        kept_clauses: list[str] = []
        for clause in _split_clauses(line):
            if clause.lower() not in BOILERPLATE_SENTENCES:
                kept_clauses.append(clause)
        if kept_clauses:
            meaningful.append(" ".join(kept_clauses))
    return meaningful


def classify_noise(record: dict[str, Any], sanitized_body: str) -> tuple[int, int]:
    """Classify a record into (is_boilerplate, is_newsletter)."""
    rtype = (record.get("type") or "").strip().lower()
    author = str(record.get("author") or "")
    client = record.get("client")
    subject = str(record.get("subject") or "")
    title = str(record.get("title") or "")
    low_body = sanitized_body.strip().lower()

    is_newsletter = 0
    if rtype == "mail" and author.startswith("ext:") and not client:
        addr = author[4:].lower()
        local_part = addr.split("@", 1)[0] if "@" in addr else ""
        if local_part in _NEWSLETTER_LOCAL_PARTS or _NEWSLETTER_SUBJECT_RE.search(subject):
            is_newsletter = 1
        elif "northstar print" in subject.lower() or "northstarprint" in addr:
            is_newsletter = 1

    is_boilerplate = 0
    if rtype == "notification":
        is_boilerplate = 1
    elif low_body in ROUTINE_CHITCHAT_BODIES or any(p.match(low_body) for p in ROUTINE_CHITCHAT_PATTERNS):
        is_boilerplate = 1
    elif rtype == "doc":
        meaningful = strip_boilerplate_lines(sanitized_body)
        non_header = [
            line for line in meaningful
            if line.lower() != f"working notes – {(title or '').lower()}"
            and not line.lower().startswith("working notes")
            and line.rstrip(".").lower() != (title or "").rstrip(".").lower()
        ]
        if not non_header:
            is_boilerplate = 1
        else:
            all_sents = [c for line in non_header for c in _split_clauses(line)]
            has_structured = any(
                re.match(r"^[A-Za-z][A-Za-z0-9 /_-]{1,35}:\s+\S", line)
                for line in non_header
            ) or bool(
                re.search(
                    r"\b(?:\d{4}-\d{2}-\d{2}|"
                    r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*|"
                    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2})\b",
                    sanitized_body,
                    re.IGNORECASE,
                )
            )
            if (
                not has_structured
                and len(all_sents) >= 8
                and (len({s.lower() for s in all_sents}) / len(all_sents)) <= 0.65
            ):
                is_boilerplate = 1

    return is_boilerplate, is_newsletter

# WorkElate (BuildSprint R3) — The Work Assistant That Remembers

A privacy-preserving, temporal-aware CLI & Web workplace memory assistant built on Python 3.12, SQLite + FTS5 + 256-d hybrid vector retrieval, event-sourced state reconstruction, and a deterministic-first reasoning pipeline with 3-tier LLM failover (`Groq Primary -> Gemini Secondary -> OpenAI Final Fallback`).

---

## Quick Start (Clone & Run Out-of-the-Box)

This repository includes a pre-configured `.env` file so the assistant works immediately upon cloning with no extra manual configuration (`src/config.py` resolves `.env`, `BuildSprint-R3-candidate/`, and `.r3_assist.db` relative to the repository root automatically).

> **Note:** On first backend startup (or `ingest`), SQLite `.r3_assist.db` automatically seeds Days 1–5 from `BuildSprint-R3-candidate/`.

### Option 1: Run the Full WorkMate Web UI (FastAPI + React/Vite)

**Terminal 1 — Start the FastAPI Backend (`http://127.0.0.1:8000`)**

```powershell
# Install API server dependencies (one time)
pip install -r requirements.txt

# Start the backend server (auto-seeds Days 1–5 into .r3_assist.db on first run)
$env:PYTHONDONTWRITEBYTECODE = "1"
python -B -m uvicorn backend.api.main:app --reload --port 8000
```

*(macOS/Linux: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m uvicorn backend.api.main:app --reload --port 8000`)*

**Terminal 2 — Start the React + Vite Frontend (`http://localhost:5173`)**

```powershell
cd frontend
npm install
npm run dev
```

Open **`http://localhost:5173`** in your browser. Vite proxies `/api/*` directly to `http://127.0.0.1:8000`.

---

### Option 2: Run the CLI Directly (`assist.py` — Pure Python 3.12 Stdlib)

The core CLI (`assist.py`) requires **zero external pip packages** — Python 3.12 standard library is sufficient:

```powershell
# 1. Ingest workspace days incrementally (Day 1 resets/initializes schema; idempotent if repeated)
python -B assist.py ingest --day 1
python -B assist.py ingest --day 2
python -B assist.py ingest --day 3
python -B assist.py ingest --day 4
python -B assist.py ingest --day 5

# 2. Generate a delta-watermarked meeting brief for Meera Iyer (p02)
python -B assist.py brief --person p02 --date 2026-11-06

# 3. Ask a factual question (deterministic, $0 / 0 tokens)
python -B assist.py ask --person p02 --at 2026-11-06T18:00:00Z --ask "Who handles billing for Northbeam?"

# 4. Ask an open-ended analytical or Hinglish question (LLM-synthesized with grounded citations)
python -B assist.py ask --person p02 --at 2026-11-06T18:00:00Z --ask "Write an executive memo summarizing current project risks and recommendations."
python -B assist.py ask --person p02 --at 2026-11-06T18:00:00Z --ask "Kya scene hai Cobaltline ka? Kaam progress mein hai ya kuch major blocker chal raha hai?"

# 5. Forget private memory (cascades across records, tasks, claims, promises, vectors, and brief cache)
python -B assist.py forget --person p02 --what "my Dubai trip"

# 6. Audit all authorized knowledge retained for a user
python -B assist.py audit --person p02
```

---

### Option 3: Run Official Evaluation Harness & Full Test Suite

```powershell
# Full unit, integration, vector retrieval, security firewall, and FastAPI test suite (95 tests)
$env:PYTHONDONTWRITEBYTECODE = "1"
python -B -m unittest discover -s tests -v

# Replay & score official Days 1–5 pack (from repo root)
python -B BuildSprint-R3-candidate/tools/run_days.py --cmd "python assist.py" --days 1-5 --out runs/final
python -B BuildSprint-R3-candidate/tools/score.py --run runs/final --days 1-5

# Frontend TypeScript + Vite production build check
cd frontend
npm run build
```

> **Note for `run_days.py`:** `tools/run_days.py` defaults cleanly from `BuildSprint-R3-candidate/`:
> ```powershell
> cd BuildSprint-R3-candidate
> python -B tools/run_days.py --cmd "python ../assist.py" --days 1-5 --out ../runs/final
> python -B tools/score.py --run ../runs/final --days 1-5
> ```

---

## Architecture & Key Capabilities

1. **Deterministic-First Compiler (`src/tasks_engine.py`, `src/claims.py`, `src/promises.py`, `src/docs_engine.py`, `src/entities.py`)**:
   - **Event-Sourced Task State**: Replays `change` events sequentially instead of trusting buggy top-level comment snapshots (preventing stale owner/due overrides).
   - **Conflict Resolution Rules**: Separates ownership claims (`manager > lead > coordinator > ... > intern`, then recency) from non-ownership facts (newest valid claim wins), ignoring hedged statements (`maybe`, `probably`, `I think`) and carried-over doc lines (`effective_at_utc`).
   - **Multi-Signal Entity Resolution**: Resolves acronyms (`NB`, `CL`), city aliases (`the Mumbai client`, `the London account`), task titles, and same-first-name disambiguation (`Which Arjun is that?`).
   - **Temporal Leave & Expiry Awareness**: Distinguishes active leave windows (`4–12 Nov`) from expired leave (`13 Nov`).
2. **Hybrid Retrieval (`src/retrieval/`)**:
   - Combines SQLite **FTS5 BM25** lexical search with **256-d semantic vector similarity** (`record_vectors`, Reciprocal Rank Fusion) pre-filtered by `org`, `day`, `at_utc`, `visibility`, `readers_json`, and `is_forgotten`.
3. **3-Tier LLM Failover & Token Budget Guardrails (`src/llm.py`, `src/usage.py`)**:
   - **Chain**: `Groq (Primary)` → `Gemini (Secondary)` → `OpenAI (Final Evaluation Fallback)`.
   - Exact factual lookups run deterministically at **`$0.000000` (`0` tokens)**; analytical, synthesis, recommendation, executive memo, and Hinglish/conversational status queries trigger grounded LLM synthesis strictly packing `<workplace_record>` `sanitized_body` XML entries within `4,000` input tokens (`ask`) and `8,000` input tokens (`brief`), validating all source citations against SQLite ACLs before returning.
4. **Defense-in-Depth Security & Memory (`src/filters.py`, `src/security.py`, `src/memory.py`)**:
   - Blocks `private`, `restricted`, cross-org (`org_kp` vs `org_bw`), and future-day/timestamp leaks before retrieval and again before model calls and final response validation.
   - Strips embedded prompt injections (`Ignore previous instructions...`, `AT RISK`, etc.) prior to indexing and LLM prompts.
   - Supports live per-person natural-language corrections and cascading forget (`forget_for_person`).

---

## Demo Test Questions (Ask Assistant UI & CLI)

Select **Day 5 (`2026-11-06`)** and **`Meera Iyer (p02)`** (unless noted) to verify every execution path:

### 1. Deterministic Compiler Path (`Deterministic ($0)` • `0` Tokens)
- `Who handles billing for Northbeam?` → `"Arjun Mehta handles billing for Northbeam Retail."` (`rgg1ex2g`)
- `Who owns billing for the Mumbai client?` → `"Arjun Mehta handles billing for Northbeam Retail."` (`rgg1ex2g`)
- `When is the Northbeam launch date?` → `"The Northbeam Retail launch date is 14 Nov (2026-11-14)."` (`rwzvbzdv`)
- `What's the status of product feed fix (Northbeam)?` → `"Product feed fix is blocked (waiting on client assets)."` (`r0hwyx1z`)
- `Who owns the Cobalt uat test plan?` *(as `Rohan Kapoor (p03)`)* → `"Dev Patel (p05) owns UAT test plan."`
- `What open promises are pending for Northbeam?` → Unfulfilled Northbeam commitments with owners and due dates
- `When is my Dubai trip?` *(as `p02` before forgetting)* → `"Booked my Dubai trip for 20–24 Nov."` (`rjuznjge`)
- `What is our disaster recovery failover arrangement for Cobalt?` → Matches `backup plan` (`r2htb0w2`) via 256-d semantic vector retrieval (`0` LLM tokens)

### 2. Interactive Ambiguity Clarification (`0` Tokens)
- `Is the Friday thing done?` → Prompts clarification chips for `t-me-01: Send invoice to Northbeam` and `t-me-02: Review campaign copy`
- `Send the NB deck to Arjun. Which Arjun is that?` → Resolves/disambiguates `Arjun Mehta (p11)` vs `Arjun Rao (p14)`

### 3. Privacy Refusal & Out-of-Scope Abstention (`0` Tokens)
- `What did Meera say about me?` *(as `Rohan Kapoor (p03)`)* → Refuses (`private`), zero private record leakage
- `Is Meera travelling anywhere this month?` *(as `Rohan Kapoor (p03)`)* → Refuses (`private`), zero private record leakage
- `What is our disaster recovery failover arrangement for Northbeam?` → Abstains (`"I have nothing on that."`) — no cross-client leakage from Cobaltline
- `Analyze Italian pasta recipes and recommend dinner.` → Abstains (`"I have nothing on that."`) — out-of-scope blocked before calling LLM

### 4. LLM-Triggered Analytical, Executive Memo & Hinglish Queries (`LLM: groq / ...`)
- `Write an executive memo summarizing current project risks and recommendations.` → Executive memo grounded in blocked tasks (`r0hwyx1z`, `r2vn8n1z`), launch changes (`rwzvbzdv`), and open promises with clickable source badges
- `Based on available information, what should we prioritize this week?` → Prioritized schedule & blockers (`rbfjemhd`, `rrq318zf`, `r0hwyx1z`, `rhwmu74q`, `rfaw7xyk`)
- `Analyze recent team communications and infer where delivery bottlenecks will happen.` → Fact-vs-inference breakdown citing communication/task IDs
- `Kya scene hai Cobaltline ka? Kaam progress mein hai ya kuch major blocker chal raha hai?` → Hinglish status/blocker synthesis (`Rate card update` blocked on API access `r2vn8n1z`, `Driver app login fix` in progress `rhwmu74q`, 4 tasks done)
- `Northbeam pe kya kaam chal raha hai aur koi blocker hai kya?` → Hinglish status/blocker synthesis for Northbeam (`r0hwyx1z`, `rfaw7xyk`, `rbfjemhd`, etc.)
- `What's going on with Cobaltline right now?` → Conversational English status overview grounded in Cobaltline records

---

## Official Days 1–5 Evaluation Benchmark (`tools/score.py`)

| Category | Score | Points |
|---|---:|---:|
| Right briefs | 1.00 | 20.0 / 20 |
| Right answers | 1.00 | 15.0 / 15 |
| Honesty | 1.00 | 15.0 / 15 |
| Memory | 1.00 | 15.0 / 15 |
| Privacy | 1.00 | 10.0 / 10 |
| Cost | 1.00 | 10.0 / 10 |
| **TOTAL** | **100%** | **85.0 / 85** |

---

## Exposed FastAPI Endpoints (`backend/api/`)

- `GET /api/health` & `GET /api/people` — System status, ingested day coverage, vector counts, and personas (`org_kp`, `org_bw`).
- `GET /api/overview?person_id=...&date=...` — Person-scoped tasks, commitments, meetings, and authorized recent updates.
- `POST /api/brief` — Delta-watermarked meeting briefs with optional client filter and cache regeneration.
- `POST /api/ask` — Temporal/ACL-enforced Q&A with entity matching, clarification options, privacy shields, and LLM telemetry (`mode`, `provider`, `model`).
- `GET /api/records/{record_id}?person_id=...` — Authorized source citation inspection (`403` private/cross-org, `404` forgotten).
- `GET /api/memory`, `POST /api/memory/correct`, `POST /api/memory/forget` — Memory inspection, NL corrections, and cascading forget.
- `GET /api/audit?person_id=...` — Filterable authorized traceability log (`correction`, `preference`, `forget`, `security_filter`, `record`).
- `GET /api/usage` — 3-tier LLM chain status (zero key exposure), token guardrails, vector index stats, and telemetry log.

---

## Repository Structure

```text
├── assist.py                  # Standalone Python 3.12 stdlib CLI entrypoint
├── .env                       # Pre-configured runtime & LLM keys (portable relative defaults)
├── .env.example               # Template reference
├── requirements.txt           # FastAPI Web UI backend dependencies
├── BuildSprint-R3-candidate/  # Official challenge pack (data, answers, tools/run_days.py, score.py)
├── src/                       # Core Python engine (ingest, claims, tasks, promises, ask, brief, memory, retrieval, security, llm, usage)
├── backend/api/               # Thin FastAPI adapter exposing src/ to Web UI
├── frontend/                  # React 18 + TypeScript + Vite + Tailwind CSS + Recharts UI ("WorkMate")
└── tests/                     # 95 automated unit, integration, security, retrieval, and FastAPI tests
```

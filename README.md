# Preflight Companion

A local-first study coach for engineering students. Paste an assignment brief and a draft; Preflight extracts the real requirements, checks the draft for evidence, remembers what you keep missing, and gives you a short plan for what to do next.

Everything runs on your machine. Briefs, drafts, reports, submissions, and coach chat are stored in one SQLite file and are never sent anywhere.

> **What it is not:** a ghostwriter, a homework solver, or a pass predictor. The model writes only a three-step prioritisation plan and one risk sentence. Every requirement is quoted from your brief and checked by deterministic code.

---

## Quick start

```bash
cd /home/living_paradox/preflight

# 1. Point at a llama.cpp build and at least one .gguf model
ln -sfn /home/living_paradox/foreshadow/vendor vendor
ln -sfn /home/living_paradox/foreshadow/models models

# 2. Run in the foreground
./run.sh

# or run detached (terminal can close)
./start.sh
./stop.sh
```

Open **http://127.0.0.1:8090**.

The first launch starts `llama-server` on `127.0.0.1:8081` and loads `Qwen2.5-1.5B-Instruct-Q4_K_M.gguf`. If something is already healthy on 8081 it is reused. The app still works with the model offline: the deterministic plan is shown instead.

### Requirements

- Linux, Python **3.11+** (developed on 3.13), standard library only
- A llama.cpp `llama-server` binary plus its shared libraries in `vendor/llama/`
- One GGUF model in `models/` (1.5B Q4_K_M recommended; 0.6B and 0.5B fallbacks are detected)
- No Node, npm, CDN, API key, or network connection needed

Model choice can be overridden with `PREFLIGHT_MODEL=/path/to/model.gguf`. Ports can be changed with `PREFLIGHT_PORT` and `PREFLIGHT_LLAMA_PORT` / `PREFLIGHT_LLAMA_URL`.

---

## The flow

1. **Add an assignment** — course code, title, optional deadline, brief.
2. **Run a preflight** — requirements appear first as a streamed evidence table; the local coach then adds exactly three actions and one risk.
3. **Paste or load a draft** — `.txt`/`.md` files are read in the browser; nothing is uploaded. Rerun to see addressed / partial / missing / contradicted coverage with quoted evidence and version diffs.
4. **Mark submitted** — stores a versioned snapshot and derives recurring gaps.
5. **Ask the coach** — checklist, readiness, next-step, and deadline questions (“What is missing?”, “Am I ready?”, “What first?”, “Deadline risk?”) are answered instantly and deterministically from the latest report. Other questions use the local model, grounded in the report, deadline, memory, the last 8 messages, and a deterministic rolling summary.
6. **Export** — Markdown report or JSON data, generated locally.

The **Load demo** button seeds two prior submissions for one course so recurring-gap memory is visible immediately, plus an active design task due in three days.

---

## How it works

```
brief ──► ingest ──► extract (deterministic) ──► coverage (deterministic) ──► plan (3 actions)
                                                        │                        ▲
draft ──────────────────────────────────────────────────┘                        │
                                                     local model: wording only ───┘
```

| Layer | File | Responsibility |
|---|---|---|
| Storage | `app/db.py` | SQLite schema, WAL, courses, assignments, audits, submissions, feedback, memory, messages, network ledger |
| Ingest | `app/ingest.py` | Normalisation, sentence splitting with line/paragraph refs, size limits |
| Extract | `app/extract.py` | Requirement sentences, kinds, vague/missing detection, deadlines, contradictions |
| Coverage | `app/coverage.py` | Evidence matching, numeric/unit checks, untestable claims, version diff |
| Plan | `app/plan.py` | Deterministic candidates, estimates, integrity filter, model/deterministic merge |
| Companion | `app/companion.py` | Deadline states, check-ins, recurring-gap memory, chat context, fallback replies |
| Model | `app/model.py` | llama.cpp client, JSON repair, compact prompts, serialised calls |
| Pipeline | `app/pipeline.py` | Evidence-first orchestration, NDJSON events, audit persistence |
| Export | `app/export.py` | Markdown and JSON |
| Server | `app/server.py` | Local HTTP API, streaming, static files, model autostart |
| UI | `static/` | Vanilla HTML/CSS/JS, offline fonts, no framework or bundler |

The model is used for exactly two things:

- **Plan:** three ordered actions (≤12 words each) and one risk (≤15 words), from the evidence block only.
- **Chat:** ≤90 words, grounded in the report and memory, with an explicit refusal to do the work. Checklist, readiness, next-step, and deadline questions bypass the model entirely and are answered from the report and saved plan.

Requirement statuses in the chat context are labelled as authoritative, and a deterministic guard replaces any reply that calls an unresolved item covered or the work ready. If the model is offline, times out, or returns unusable JSON, the deterministic plan and grounded fallback reply are used. Model output also passes an integrity filter that rejects code fences, “here is the solution”, sample sections, and similar submission-ready content; rejected replies are replaced with a planning-only refusal. Direct requests to write the assignment are refused before the model is called.

Coverage is an **assignment checklist**, not a grade and not an engineering correctness or safety review. A missing item means the draft shows no evidence for that requirement; it does not mean the design is wrong.

### Why recall is conservative

Requirement extraction deliberately favours precision over recall. A missed sentence is recoverable; a fabricated requirement is not. The app never asks the model to list requirements.

---

## Privacy

- Server binds **127.0.0.1** only.
- The only outbound request is to **127.0.0.1:8081** (llama-server). There is no web search, telemetry, analytics, CDN, or remote font loading.
- Chat uses the last 8 messages plus a rolling keyword summary. No embeddings, no unbounded history.
- The database is created with mode `0600` under `data/preflight.db`.
- The **Memory** tab lists every stored memory item with a delete button, plus a network ledger that is normally empty.
- **Forget** deletes the assignment, its reports, submissions, feedback, chat, and assignment-scoped memory.

Web lookup exists only as a stored disabled setting in this build. Enabling it does not make any requests.

---

## API

All endpoints are local JSON unless noted. Streaming preflight returns `application/x-ndjson`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/status` | Model/runtime status |
| `GET` | `/api/bootstrap` | Assignments, check-ins, memory, stats, status |
| `GET` | `/api/assignments` | Assignment list |
| `GET` | `/api/assignments/{id}` | Full detail: audits, submissions, messages, memory |
| `GET` | `/api/assignments/{id}/export.md` | Markdown report download |
| `GET` | `/api/assignments/{id}/export.json` | JSON data download |
| `GET` | `/api/memory` | Memory plus network ledger |
| `POST` | `/api/assignments` | Create assignment |
| `POST` | `/api/assignments/{id}/update` | Update brief, draft, deadline, status |
| `POST` | `/api/assignments/{id}/delete` | Delete assignment and children |
| `POST` | `/api/preflight` | Stream `stage` / `report` / `plan` / `done` events |
| `POST` | `/api/chat` | Coach reply |
| `POST` | `/api/submit` | Save a submission version, derive patterns |
| `POST` | `/api/feedback` | Attach instructor feedback to a submission |
| `POST` | `/api/messages/clear` | Clear one assignment's chat |
| `POST` | `/api/memory/delete` | Delete one memory item |
| `POST` | `/api/demo` | Seed the curated demo (only when empty) |

---

## Tests

```bash
cd /home/living_paradox/preflight
python3 -m unittest discover -s tests -v
```

33 deterministic tests cover extraction (quotes, source refs, deadlines, vague terms, no invented requirements), coverage (evidence, missing drafts, contradictions, diffs), planning (exactly three actions, integrity filter, merge), companion (deadline states, two-submission pattern threshold, rolling summary, grounded fallback, deterministic checklist/readiness/next-step/deadline replies, work-request refusal, ghostwriting filter, false-ready guard), and storage (persistence, versions, None-safe updates, detected-deadline adoption, export, privacy defaults).

Tests use a temporary `PREFLIGHT_DB` and never touch your real data.

---

## Fixtures

| Brief | Draft | Shows |
|---|---|---|
| `fixtures/heat-exchanger-brief.md` | `fixtures/heat-exchanger-draft.md` | Units, instrumental error, missing tables/plots |
| `fixtures/bounded-buffer-brief.md` | `fixtures/bounded-buffer-draft.md` | Code assignment; evidence vs. missing stress test and README |

Paste them into the UI, or use them with the API.

---

## Scope of this build (7-hour version)

**Included**

- SQLite persistence, evidence-first audit, draft coverage, numeric/unit checks
- Paste or browser-local `.txt`/`.md` loading (no upload)
- Deadline states and check-in banner; a deadline found in the brief is adopted when none was set
- Versioned submissions, instructor feedback, recurring-gap memory after two submissions
- Persistent per-assignment coach chat with summary and fallback
- Markdown/JSON export, privacy ledger, delete controls
- Local model autostart/reuse, offline UI, deterministic fallbacks
- 33 tests, demo, fixtures, run/start/stop scripts

**Deliberately excluded**

- PDF/DOCX/OCR and file upload (plain-text `.txt`/`.md` files are read locally in the browser)
- Real web search
- OS notifications while the app is closed
- Cloud sync, multi-user accounts, mobile app
- Grade prediction and LMS integration
- Embeddings or long-term semantic chat memory

**Known limits**

- Deadline parsing is heuristic (weekdays, ISO, month names, slash dates, times).
- Requirement extraction is conservative; sparse briefs can yield few requirements.
- Coverage is lexical evidence matching, not semantic entailment; it can miss paraphrases and is not a grade.
- The 1.5B model is slow on CPU. Measured here: the evidence table appears in ~0.01–0.03 s, a full audit takes ~11 s (allow 10–30 s), and coach replies take ~3–16 s. Model calls are serialised, and the first model load can take up to two minutes.
- The UI loads plain text only and has no autosave; use **Save draft**.
- Background operation means the terminal can close, not that the machine can sleep. OS suspend pauses the app and the model.
- No browser automation was available during development, so the UI was verified through HTTP/API checks and manual inspection.

---

## Layout

```
preflight/
├─ app/               Python server and deterministic engine
├─ static/            Offline UI, fonts, favicon
├─ tests/             Deterministic test suite
├─ fixtures/          Example briefs and drafts
├─ data/              SQLite DB, logs, exports (gitignored)
├─ vendor/  -> ...    llama.cpp runtime (gitignored)
├─ models/  -> ...    GGUF weights (gitignored)
├─ run.sh             Foreground launch
├─ start.sh           Detached launch
└─ stop.sh            Stop the app, keep shared llama-server
```

## License

MIT. See `LICENSE`.

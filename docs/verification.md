# Verification log

Date: 2026-10-27 · Linux · Python 3.13.12 · llama.cpp · Qwen2.5-1.5B-Instruct Q4_K_M

## Automated

```bash
python3 -m py_compile app/*.py
python3 -m unittest discover -s tests
```

Result: **33/33 tests pass** in ~0.3 s. Tests use a temporary `PREFLIGHT_DB` under `/tmp/opencode`.

## HTTP / API checks

Checked against a detached instance on `127.0.0.1:8090`:

- `/` and all static assets return 200 (HTML, CSS, JS, WOFF2).
- `/api/status` reports `ready: true`, model file, engine, and `web_enabled: false`.
- `/api/bootstrap` returns courses, assignments, check-ins, memory, stats, and an empty network ledger.
- `/api/demo` seeds two assignments, two submission versions, five recurring-gap memories, and an active task.
- `/api/preflight` streams `stage → report → plan → done` in order. Evidence table: ~0.01 s. Full audit with the model: ~11 s.
- `/api/chat` returns grounded replies in ~3–17 s; the readiness guard replaced a false “ready” reply with the remaining gap list.
- Direct work requests (“Can you write the recommendation section for me?”) are refused in ~0.03 s with `model_used: false`; a model reply containing a sample section was detected and replaced with the same planning-only refusal (`integrity_filtered: true`).
- Checklist, readiness, next-step, and deadline prompts are deterministic: “What is missing from the draft?” returns the exact report gaps in ~0.04 s with `model_used: false`, and “Am I ready to submit?” is likewise answered from coverage without a model call.
- Chat responses return distinct `user_message` and `coach` records; the UI no longer renders the coach reply twice.
- `/api/submit` creates versioned snapshots, sets status, and derives patterns only after two submissions.
- `/api/feedback` attaches grade/comments; detail returns them under the submission.
- Markdown and JSON exports download with the expected content and `local_only: true`.
- Create → update → preflight (no model) → submit → export → delete all succeeded for a fixture assignment.
- Detected deadlines are adopted when none was supplied; `brief_source` survives a preflight.
- Validation paths return 422/404 with stable error codes.
- Restart persistence verified: assignments, audits, submissions, chat, feedback, and memory survived `stop.sh` + `start.sh`; the shared llama-server on 8081 was reused.

## Privacy checks

- Server binds `127.0.0.1` only.
- Code audit found no remote URLs, CDNs, telemetry, or external fetches. The only HTTP clients target `127.0.0.1:8081`.
- `data/preflight.db` is mode `0600`.
- Network ledger stayed empty through all checks.
- The UI reads local `.txt`/`.md` files in the browser; no upload endpoint exists.

## Not verified automatically

No browser, headless Chromium, or JavaScript runtime is installed on this machine. The UI was checked by HTTP/static inspection, brace/paren/backtick balance, and manual review. A human should open `http://127.0.0.1:8090` once to confirm layout and interactions in their browser.

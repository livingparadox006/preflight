#!/usr/bin/env python3
"""Preflight Companion local web server.

Standard library only. Binds 127.0.0.1, serves the interface, talks to a local
llama.cpp server, and stores everything in SQLite under data/.
"""

from __future__ import annotations

import argparse
import atexit
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time as dtime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import companion  # noqa: E402
import db  # noqa: E402
import export  # noqa: E402
import ingest  # noqa: E402
import model as model_mod  # noqa: E402
import pipeline  # noqa: E402
import plan as plan_mod  # noqa: E402

STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"
LLAMA_DIR = ROOT / "vendor" / "llama"
LLAMA_PORT = int(os.environ.get("PREFLIGHT_LLAMA_PORT", "8081"))
LLAMA_URL = os.environ.get("PREFLIGHT_LLAMA_URL", f"http://127.0.0.1:{LLAMA_PORT}")
VERSION = "1.0"

_llama_process: subprocess.Popen | None = None
_llama_log = None


def _health(url: str) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=1.5) as response:
            return response.status == 200
    except Exception:
        return False


def start_model() -> str:
    global _llama_process, _llama_log
    if _health(LLAMA_URL):
        return "connected to an existing llama-server"
    model_path, label = model_mod.pick_model()
    if not model_path:
        return "no model found; expected a .gguf file under models/"
    binary = LLAMA_DIR / "llama-server"
    if not binary.exists():
        return "llama-server binary missing under vendor/llama/"
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = str(LLAMA_DIR) + (
        ":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else ""
    )
    threads = str(min(8, os.cpu_count() or 4))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _llama_log = open(DATA_DIR / "llama-server.log", "ab", buffering=0)
    _llama_log.write(
        f"\n--- starting {label} at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n".encode()
    )
    cmd = [
        str(binary), "-m", str(model_path), "--host", "127.0.0.1",
        "--port", str(LLAMA_PORT), "--ctx-size",
        os.environ.get("PREFLIGHT_CTX", "4096"), "--threads", threads,
        "--parallel", "1", "--no-warmup",
    ]
    _llama_process = subprocess.Popen(
        cmd, cwd=str(ROOT), env=env, stdout=_llama_log, stderr=subprocess.STDOUT
    )
    atexit.register(stop_model)
    deadline = time.time() + 120
    while time.time() < deadline:
        if _llama_process.poll() is not None:
            return "llama-server exited while loading; see data/llama-server.log"
        if _health(LLAMA_URL):
            return f"loaded {label} on {threads} CPU threads"
        time.sleep(0.5)
    return "timed out loading the local model; see data/llama-server.log"


def stop_model() -> None:
    global _llama_process
    if _llama_process and _llama_process.poll() is None:
        _llama_process.terminate()
        try:
            _llama_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _llama_process.kill()
    _llama_process = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


DEMO_BRIEF_1 = """ENG 204 — Lab 3: Beam Deflection
Submit a 6-page technical report as a single PDF by Friday at 17:00.
The report must include a simply supported beam diagram, a table of measured deflections, and a comparison with the Euler-Bernoulli prediction.
Use at least five load steps between 0 and 20 kg.
Calculate the percentage error for each step.
Discuss sources of error, including at least two instrumental effects.
The conclusion must state whether the experimental result supports the theoretical model.
Figures must be numbered and referenced in the text.
"""

DEMO_DRAFT_1 = """This report compares measured and theoretical beam deflection. The beam was loaded and the deflections were recorded. The results were close to the prediction. A graph is included. The experiment worked well overall and the method was appropriate.
"""

DEMO_BRIEF_2 = """ENG 204 — Design Task 4: Bridge Deck Load Path
Produce a 4-page design memo for a simply supported pedestrian bridge deck.
The memo must include a load-path diagram, a free-body diagram, and a table of member forces.
Assume a deck span of 12 m and a uniform pedestrian load of 5 kPa.
Check the maximum bending moment against the steel section capacity.
State all units and sign conventions.
Identify at least two failure modes and one mitigation for each.
The memo must end with a recommendation of no more than 150 words.
Submit as PDF by 17:00 on the due date.
"""

DEMO_DRAFT_2 = """The bridge deck transfers pedestrian load to the supports through the main girders. The span is 12 m. A load path diagram is included. The maximum moment was calculated and compared with the capacity. The design is safe. The recommendation is to use the larger section and check the connections on site. The report is four pages.
"""


def seed_demo() -> dict:
    if db.list_assignments():
        return {"seeded": False, "reason": "assignments already exist"}
    course = db.find_or_create_course("ENG 204", "Engineering Design Studio", "Autumn")
    today = date.today()
    prior_deadline = (today - timedelta(days=9)).isoformat() + "T17:00"
    next_deadline = (today + timedelta(days=3)).isoformat() + "T17:00"

    first = db.create_assignment(
        "Lab 3 — Beam Deflection",
        DEMO_BRIEF_1,
        course_id=course["id"],
        deadline=prior_deadline,
        draft_text=DEMO_DRAFT_1,
        brief_source="demo-lab.md",
        status="submitted",
    )
    report = pipeline.build_report(first, DEMO_BRIEF_1, DEMO_DRAFT_1, source="demo-lab.md")
    plan = plan_mod.deterministic_plan(report)
    db.save_audit(
        first["id"], report, plan, model_used=False,
        model_label="deterministic demo", duration_ms=0, kind="preflight",
    )
    submission = db.save_submission(
        first["id"], DEMO_DRAFT_1, report["coverage"]["items"],
        report["coverage"]["gaps"], plan, notes="Earlier snapshot",
    )
    db.save_submission(
        first["id"], DEMO_DRAFT_1, report["coverage"]["items"],
        report["coverage"]["gaps"], plan, notes="Final snapshot",
    )
    db.update_assignment(first["id"], status="submitted")

    second = db.create_assignment(
        "Design Task 4 — Bridge Deck Load Path",
        DEMO_BRIEF_2,
        course_id=course["id"],
        deadline=next_deadline,
        draft_text=DEMO_DRAFT_2,
        brief_source="demo-design.md",
        status="active",
    )
    report2 = pipeline.build_report(second, DEMO_BRIEF_2, DEMO_DRAFT_2, source="demo-design.md")
    plan2 = plan_mod.deterministic_plan(report2)
    db.save_audit(
        second["id"], report2, plan2, model_used=False,
        model_label="deterministic demo", duration_ms=0, kind="preflight",
    )
    patterns = companion.derive_patterns(
        db.list_submissions(first["id"]), course_id=course["id"], assignment_id=first["id"]
    )
    coach_text = "I remember the last submission. "
    if patterns:
        coach_text += patterns[0]["text"] + " "
    coach_text += "This brief has measurable checks. Start with the highest-risk one."
    db.add_message(second["id"], "coach", coach_text)
    return {
        "seeded": True,
        "course": course,
        "assignments": [first["id"], second["id"]],
        "patterns": patterns,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = f"PreflightCompanion/{VERSION}"

    def log_message(self, fmt: str, *args) -> None:
        if args and str(args[0]).startswith(("GET /static", "GET /fonts")):
            return
        sys.stderr.write(f"[{self.log_date_time_string()}] " + (fmt % args) + "\n")

    # ── responses ────────────────────────────────────────────────────────

    def _json(self, payload: dict | list, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int = 400, code: str = "error") -> None:
        self._json({"error": message, "code": code}, status)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 400_000:
            raise ValueError("Request body must be between 1 and 400,000 bytes.")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Request body is not valid JSON.") from exc
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object.")
        return value

    def _serve_file(self, path: Path) -> None:
        try:
            resolved = path.resolve()
            if not str(resolved).startswith(str(STATIC_DIR.resolve())):
                raise FileNotFoundError
            if not resolved.is_file():
                raise FileNotFoundError
            body = resolved.read_bytes()
        except Exception:
            self._error("Not found.", 404, "not_found")
            return
        ctype = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header(
            "Cache-Control",
            "no-cache",
        )
        self.end_headers()
        self.wfile.write(body)

    # ── serializers ──────────────────────────────────────────────────────

    def _detail(self, assignment_id: str) -> dict | None:
        assignment = db.get_assignment(assignment_id)
        if not assignment:
            return None
        audits = assignment.get("audits") or []
        latest = audits[0] if audits else None
        assignment["latest_audit"] = latest
        assignment["checkin"] = companion.checkin(assignment, latest)
        assignment["deadline_state"] = companion.deadline_state(assignment.get("deadline", ""))
        return assignment

    def _bootstrap(self) -> dict:
        assignments = db.list_assignments()
        for assignment in assignments:
            assignment["deadline_state"] = companion.deadline_state(
                assignment.get("deadline", "")
            )
        model_path, label = model_mod.pick_model()
        return {
            "status": {
                "local": True,
                "ready": _health(LLAMA_URL),
                "model": label if model_path else "no model found",
                "model_file": model_path.name if model_path else None,
                "engine": "llama.cpp",
                "version": VERSION,
                "web_enabled": db.get_setting("web_enabled", "false") == "true",
            },
            "stats": db.stats(),
            "courses": db.list_courses(),
            "assignments": assignments,
            "checkins": companion.checkins_for(assignments),
            "memory": db.all_memory()[:20],
            "network": db.list_network(50),
        }

    # ── GET ──────────────────────────────────────────────────────────────

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)

        if path == "/api/status":
            self._json(self._bootstrap()["status"])
            return
        if path == "/api/bootstrap":
            self._json(self._bootstrap())
            return
        if path == "/api/assignments":
            assignments = db.list_assignments()
            self._json(
                {
                    "assignments": assignments,
                    "checkins": companion.checkins_for(assignments),
                }
            )
            return
        if path == "/api/memory":
            self._json({"memory": db.all_memory(), "network": db.list_network(50)})
            return
        if path == "/api/network":
            self._json({"network": db.list_network(100)})
            return

        match = re.fullmatch(r"/api/assignments/([a-f0-9]+)", path)
        if match:
            detail = self._detail(match.group(1))
            if not detail:
                self._error("Assignment not found.", 404, "not_found")
                return
            detail["memory"] = db.list_memory(
                detail.get("course_id", ""), detail.get("id", "")
            )
            self._json(detail)
            return

        match = re.fullmatch(r"/api/assignments/([a-f0-9]+)/export\.(md|json)", path)
        if match:
            assignment = db.get_assignment(match.group(1))
            if not assignment:
                self._error("Assignment not found.", 404, "not_found")
                return
            audits = assignment.get("audits") or []
            audit = audits[0] if audits else None
            submissions = assignment.get("submissions") or []
            memory = db.list_memory(assignment.get("course_id", ""), assignment.get("id", ""))
            if match.group(2) == "md":
                body = export.markdown(assignment, audit, submissions, memory).encode("utf-8")
                ctype = "text/markdown; charset=utf-8"
            else:
                body = export.as_json(assignment, audit, submissions, memory).encode("utf-8")
                ctype = "application/json; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="preflight-{assignment["id"]}.{match.group(2)}"',
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path in ("/", "/index.html"):
            self._serve_file(STATIC_DIR / "index.html")
            return
        if path.startswith("/static/"):
            self._serve_file(STATIC_DIR / path[len("/static/"):])
            return
        if path.startswith("/fonts/"):
            self._serve_file(STATIC_DIR / path.lstrip("/"))
            return
        self._error("Not found.", 404, "not_found")

    # ── POST ─────────────────────────────────────────────────────────────

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._error(str(exc), 400, "bad_request")
            return

        if path == "/api/courses":
            code = str(payload.get("code", "")).strip()
            if not code:
                self._error("A course code is required.", 422, "missing_code")
                return
            course = db.find_or_create_course(
                code,
                str(payload.get("title", "")).strip(),
                str(payload.get("term", "")).strip(),
            )
            self._json({"course": course})
            return

        if path == "/api/assignments":
            title = str(payload.get("title", "")).strip()
            brief = str(payload.get("brief", ""))
            if not title:
                self._error("Give the assignment a title.", 422, "missing_title")
                return
            if not brief.strip():
                self._error("Add the assignment brief.", 422, "missing_brief")
                return
            course_id = str(payload.get("course_id", "")).strip()
            if not course_id:
                course = db.find_or_create_course(
                    str(payload.get("course_code", "General")).strip() or "General",
                    str(payload.get("course_title", "")).strip(),
                )
                course_id = course["id"]
            assignment = db.create_assignment(
                title,
                brief,
                course_id=course_id,
                deadline=str(payload.get("deadline", "")).strip(),
                draft_text=str(payload.get("draft", "")),
                brief_source=ingest.source_label(str(payload.get("brief_source", "pasted"))),
            )
            self._json({"assignment": assignment}, 201)
            return

        match = re.fullmatch(r"/api/assignments/([a-f0-9]+)/update", path)
        if match:
            assignment = db.update_assignment(
                match.group(1),
                title=payload.get("title"),
                brief_text=payload.get("brief"),
                draft_text=payload.get("draft"),
                deadline=payload.get("deadline"),
                status=payload.get("status"),
                course_id=payload.get("course_id"),
                brief_source=payload.get("brief_source"),
            )
            if not assignment:
                self._error("Assignment not found.", 404, "not_found")
                return
            self._json({"assignment": assignment})
            return

        match = re.fullmatch(r"/api/assignments/([a-f0-9]+)/delete", path)
        if match:
            ok = db.delete_assignment(match.group(1))
            self._json({"deleted": ok})
            return

        if path == "/api/preflight":
            self._preflight(payload)
            return

        if path == "/api/submit":
            assignment_id = str(payload.get("assignment_id", "")).strip()
            assignment = db.get_assignment(assignment_id)
            if not assignment:
                self._error("Assignment not found.", 404, "not_found")
                return
            audit = db.latest_audit(assignment_id)
            if not audit:
                self._error("Run a preflight before recording a submission.", 409, "no_audit")
                return
            report = audit.get("report") or {}
            coverage = report.get("coverage") or {}
            submission = db.save_submission(
                assignment_id,
                assignment.get("draft_text", ""),
                coverage.get("items") or [],
                coverage.get("gaps") or [],
                audit.get("plan") or {},
                notes=str(payload.get("notes", "")),
                source_name=ingest.source_label(str(payload.get("source_name", "pasted"))),
            )
            patterns = companion.derive_patterns(
                db.list_submissions(assignment_id),
                course_id=assignment.get("course_id", ""),
                assignment_id=assignment_id,
            )
            late = companion.deadline_memory(submission, assignment)
            self._json(
                {
                    "submission": submission,
                    "patterns": patterns,
                    "deadline_memory": late,
                    "assignment": self._detail(assignment_id),
                }
            )
            return

        if path == "/api/chat":
            self._chat(payload)
            return

        if path == "/api/messages/clear":
            assignment_id = str(payload.get("assignment_id", "")).strip()
            count = db.clear_messages(assignment_id)
            self._json({"cleared": count})
            return

        if path == "/api/memory/delete":
            memory_id = str(payload.get("id", "")).strip()
            ok = db.delete_memory(memory_id)
            self._json({"deleted": ok})
            return

        if path == "/api/feedback":
            submission_id = str(payload.get("submission_id", "")).strip()
            if not submission_id:
                self._error("A submission id is required.", 422, "missing_submission")
                return
            item = db.add_feedback(
                submission_id,
                str(payload.get("grade", "")),
                str(payload.get("comments", "")),
                payload.get("tags") if isinstance(payload.get("tags"), list) else [],
            )
            self._json({"feedback": item})
            return

        if path == "/api/settings":
            if "web_enabled" in payload:
                db.set_setting("web_enabled", "true" if payload["web_enabled"] else "false")
            if "model_mode" in payload:
                db.set_setting("model_mode", str(payload["model_mode"])[:40])
            self._json({"settings": {
                "web_enabled": db.get_setting("web_enabled", "false") == "true",
                "model_mode": db.get_setting("model_mode", "balanced"),
            }})
            return

        if path == "/api/demo":
            self._json(seed_demo())
            return

        self._error("Not found.", 404, "not_found")

    # ── preflight streaming ──────────────────────────────────────────────

    def _preflight(self, payload: dict) -> None:
        assignment_id = str(payload.get("assignment_id", "")).strip()
        brief = str(payload.get("brief", ""))
        draft = str(payload.get("draft", ""))
        deadline = payload.get("deadline")

        assignment = db.get_assignment(assignment_id) if assignment_id else None
        if assignment_id and not assignment:
            self._error("Assignment not found.", 404, "not_found")
            return
        if payload.get("brief_source"):
            source = ingest.source_label(str(payload.get("brief_source")))
        elif assignment:
            source = assignment.get("brief_source") or "pasted"
        else:
            source = "pasted"
        if not assignment:
            title = str(payload.get("title", "")).strip() or "Quick preflight"
            course = db.find_or_create_course(
                str(payload.get("course_code", "General")).strip() or "General",
                str(payload.get("course_title", "")).strip(),
            )
            assignment = db.create_assignment(
                title,
                brief,
                course_id=course["id"],
                deadline=str(deadline or "").strip(),
                draft_text=draft,
                brief_source=source,
            )

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()

        def emit(event: dict) -> None:
            line = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
            self.wfile.write(line)
            self.wfile.flush()

        try:
            pipeline.run(
                assignment,
                brief,
                draft,
                deadline=str(deadline) if deadline is not None else None,
                source=source,
                emit=emit,
                use_model=not bool(payload.get("no_model")),
            )
        except ingest.IngestError as exc:
            try:
                emit({"type": "error", "message": str(exc), "code": "ingest"})
            except (BrokenPipeError, ConnectionResetError):
                pass
        except (BrokenPipeError, ConnectionResetError):
            self.log_message("client disconnected from /api/preflight")
        except Exception as exc:
            traceback.print_exc()
            try:
                emit({"type": "error", "message": str(exc) or "The preflight failed.", "code": "failed"})
            except (BrokenPipeError, ConnectionResetError):
                pass

    # ── coach chat ───────────────────────────────────────────────────────

    def _chat(self, payload: dict) -> None:
        assignment_id = str(payload.get("assignment_id", "")).strip()
        question = str(payload.get("message", "")).strip()
        if not assignment_id:
            self._error("Choose an assignment first.", 422, "missing_assignment")
            return
        if not question:
            self._error("Write a message for the coach.", 422, "missing_message")
            return
        if len(question) > 1200:
            self._error("Keep the message under 1,200 characters.", 422, "message_too_long")
            return
        assignment = db.get_assignment(assignment_id)
        if not assignment:
            self._error("Assignment not found.", 404, "not_found")
            return

        user_message = db.add_message(assignment_id, "user", question)
        messages = db.list_messages(assignment_id, 30)
        audit = db.latest_audit(assignment_id)
        memory = companion.select_for_prompt(
            db.list_memory(assignment.get("course_id", ""), assignment_id), assignment
        )
        context = companion.context_block(assignment, audit, memory, messages)
        started = time.time()
        model_used = False
        deterministic = False
        integrity_filtered = False
        reply = ""
        client = model_mod.LlamaClient()
        if companion.is_coverage_question(question):
            # Checklist state is deterministic. A 1.5B model must never be the
            # authority on which requirement is covered, and this path is instant.
            reply = companion.coverage_reply(audit)
            deterministic = True
        elif companion.is_readiness_question(question):
            # Readiness is also a coverage decision, not a model opinion.
            reply = companion.readiness_reply(audit)
            deterministic = True
        elif companion.is_work_request(question):
            # Writing the assignment is out of scope. Refuse without a model call.
            reply = companion.work_refusal_reply(audit)
            deterministic = True
        elif companion.is_plan_question(question):
            # The plan is already saved; do not spend a model call to repeat it.
            reply = companion.plan_reply(audit)
            deterministic = True
        elif companion.is_deadline_question(question):
            # Deadline state is known; answer it without a model call.
            reply = companion.deadline_reply(assignment, audit)
            deterministic = True
        elif client.health():
            try:
                reply = model_mod.chat_reply(
                    context, messages[:-1], question, client=client, timeout=70
                )
                model_used = bool(reply)
                if reply:
                    reply, integrity_filtered = companion.sanitize_model_reply(reply, audit)
                    if integrity_filtered:
                        model_used = False
            except Exception:
                reply = ""
        if not reply:
            reply = companion.fallback_reply(assignment, audit, question)
        if not deterministic:
            reply = companion.guard_reply(reply, audit, question)
        coach = db.add_message(assignment_id, "coach", reply)
        self._json(
            {
                "message": coach,
                "user_message": user_message,
                "coach": coach,
                "reply": reply,
                "model_used": model_used,
                "integrity_filtered": integrity_filtered,
                "duration_ms": int((time.time() - started) * 1000),
            }
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Preflight Companion")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PREFLIGHT_PORT", "8090")))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-model", action="store_true", help="Do not auto-start llama-server")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()

    model_status = "model autostart disabled"
    if not args.no_model:
        print("◌ starting the local model…", flush=True)
        model_status = start_model()
        print(f"  {model_status}", flush=True)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    url = f"http://{args.host}:{args.port}"
    print()
    print("  PREFLIGHT COMPANION  /  check before you build")
    print(f"  {url}")
    print(f"  model: {model_mod.MODEL_LABEL}")
    print("  everything stays on this machine. ctrl-c to stop.")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  closing the preflight desk.")
    finally:
        server.server_close()
        stop_model()


if __name__ == "__main__":
    main()

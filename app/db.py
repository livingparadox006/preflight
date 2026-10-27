"""SQLite storage for Preflight Companion.

Everything is local. The database is a single file under data/ and every
memory item is inspectable and deletable through the API.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.environ.get("PREFLIGHT_DB", DATA_DIR / "preflight.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    title TEXT NOT NULL,
    term TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assignments (
    id TEXT PRIMARY KEY,
    course_id TEXT REFERENCES courses(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    brief_text TEXT NOT NULL DEFAULT '',
    brief_source TEXT NOT NULL DEFAULT 'pasted',
    draft_text TEXT NOT NULL DEFAULT '',
    deadline TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audits (
    id TEXT PRIMARY KEY,
    assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'preflight',
    content_hash TEXT NOT NULL DEFAULT '',
    report_json TEXT NOT NULL,
    plan_json TEXT NOT NULL DEFAULT '{}',
    model_used INTEGER NOT NULL DEFAULT 0,
    model_label TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS submissions (
    id TEXT PRIMARY KEY,
    assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content_text TEXT NOT NULL DEFAULT '',
    source_name TEXT NOT NULL DEFAULT '',
    submitted_at TEXT NOT NULL,
    coverage_json TEXT NOT NULL DEFAULT '[]',
    gaps_json TEXT NOT NULL DEFAULT '[]',
    plan_json TEXT NOT NULL DEFAULT '{}',
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    grade TEXT NOT NULL DEFAULT '',
    comments TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    course_id TEXT NOT NULL DEFAULT '',
    assignment_id TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.6,
    created_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS network_log (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',
    payload_preview TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assignments_course ON assignments(course_id);
CREATE INDEX IF NOT EXISTS idx_audits_assignment ON audits(assignment_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_submissions_assignment ON submissions(assignment_id, version DESC);
CREATE INDEX IF NOT EXISTS idx_messages_assignment ON messages(assignment_id, created_at);
CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory(course_id, assignment_id);
"""

DEFAULT_SETTINGS = {
    "web_enabled": "false",
    "model_mode": "balanced",
    "retention": "keep",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str = "") -> str:
    value = uuid.uuid4().hex[:16]
    return f"{prefix}{value}" if prefix else value


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value)
            )
        conn.commit()
        try:
            os.chmod(DB_PATH, 0o600)
        except OSError:
            pass
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def _json_load(value: str, default):
    try:
        return json.loads(value)
    except Exception:
        return default


# ── courses ──────────────────────────────────────────────────────────────


def list_courses() -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM courses ORDER BY code").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_course(code: str, title: str, term: str = "") -> dict:
    code = (code or "GEN").strip()[:24]
    title = (title or code).strip()[:120]
    course = {
        "id": new_id(),
        "code": code,
        "title": title,
        "term": (term or "").strip()[:60],
        "created_at": utc_now(),
    }
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO courses(id, code, title, term, created_at) VALUES (?, ?, ?, ?, ?)",
            (course["id"], course["code"], course["title"], course["term"], course["created_at"]),
        )
        conn.commit()
        return course
    finally:
        conn.close()


def find_or_create_course(code: str, title: str = "", term: str = "") -> dict:
    code = (code or "GEN").strip()[:24]
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM courses WHERE lower(code) = lower(?)", (code,)
        ).fetchone()
        if row:
            return dict(row)
    finally:
        conn.close()
    return create_course(code, title or code, term)


# ── assignments ──────────────────────────────────────────────────────────


def list_assignments() -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT a.*, c.code AS course_code, c.title AS course_title,
                   (SELECT COUNT(*) FROM audits au WHERE au.assignment_id = a.id) AS audit_count,
                   (SELECT COUNT(*) FROM submissions s WHERE s.assignment_id = a.id) AS submission_count,
                   (SELECT MAX(created_at) FROM audits au WHERE au.assignment_id = a.id) AS last_audit_at
            FROM assignments a
            LEFT JOIN courses c ON c.id = a.course_id
            ORDER BY
                CASE WHEN a.deadline = '' THEN 1 ELSE 0 END,
                a.deadline ASC,
                a.updated_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_assignment(assignment_id: str) -> dict | None:
    conn = connect()
    try:
        row = conn.execute(
            """
            SELECT a.*, c.code AS course_code, c.title AS course_title
            FROM assignments a LEFT JOIN courses c ON c.id = a.course_id
            WHERE a.id = ?
            """,
            (assignment_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["audits"] = [
            _audit_row(r)
            for r in conn.execute(
                "SELECT * FROM audits WHERE assignment_id = ? ORDER BY created_at DESC",
                (assignment_id,),
            ).fetchall()
        ]
        data["submissions"] = [
            _submission_row(r)
            for r in conn.execute(
                "SELECT * FROM submissions WHERE assignment_id = ? ORDER BY version DESC",
                (assignment_id,),
            ).fetchall()
        ]
        for submission in data["submissions"]:
            submission["feedback"] = [
                {
                    **dict(row),
                    "tags": _json_load(row["tags_json"], []),
                }
                for row in conn.execute(
                    "SELECT * FROM feedback WHERE submission_id = ? ORDER BY created_at DESC",
                    (submission["id"],),
                ).fetchall()
            ]
            for item in submission["feedback"]:
                item.pop("tags_json", None)
        data["messages"] = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM messages WHERE assignment_id = ? ORDER BY created_at ASC",
                (assignment_id,),
            ).fetchall()
        ]
        data["memory"] = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM memory WHERE assignment_id = ? OR course_id = ? ORDER BY created_at DESC",
                (assignment_id, data.get("course_id") or ""),
            ).fetchall()
        ]
        return data
    finally:
        conn.close()


def create_assignment(
    title: str,
    brief_text: str,
    course_id: str = "",
    deadline: str = "",
    draft_text: str = "",
    brief_source: str = "pasted",
    status: str = "active",
) -> dict:
    now = utc_now()
    assignment = {
        "id": new_id(),
        "course_id": course_id or "",
        "title": (title or "Untitled assignment").strip()[:160],
        "brief_text": (brief_text or "")[:80_000],
        "brief_source": (brief_source or "pasted")[:120],
        "draft_text": (draft_text or "")[:80_000],
        "deadline": (deadline or "").strip()[:40],
        "status": status if status in ("draft", "active", "submitted", "graded") else "active",
        "created_at": now,
        "updated_at": now,
    }
    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO assignments(id, course_id, title, brief_text, brief_source,
                                    draft_text, deadline, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                assignment["id"],
                assignment["course_id"] or None,
                assignment["title"],
                assignment["brief_text"],
                assignment["brief_source"],
                assignment["draft_text"],
                assignment["deadline"],
                assignment["status"],
                assignment["created_at"],
                assignment["updated_at"],
            ),
        )
        conn.commit()
        return get_assignment(assignment["id"]) or assignment
    finally:
        conn.close()


def update_assignment(assignment_id: str, **fields) -> dict | None:
    allowed = {
        "title", "brief_text", "brief_source", "draft_text", "deadline", "status",
        "course_id",
    }
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return get_assignment(assignment_id)
    updates["updated_at"] = utc_now()
    sets = ", ".join(f"{key} = ?" for key in updates)
    values = list(updates.values()) + [assignment_id]
    conn = connect()
    try:
        conn.execute(f"UPDATE assignments SET {sets} WHERE id = ?", values)
        conn.commit()
        return get_assignment(assignment_id)
    finally:
        conn.close()


def delete_assignment(assignment_id: str) -> bool:
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── audits ───────────────────────────────────────────────────────────────


def _audit_row(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["report"] = _json_load(data.pop("report_json", "{}"), {})
    data["plan"] = _json_load(data.pop("plan_json", "{}"), {})
    data["model_used"] = bool(data.get("model_used"))
    return data


def save_audit(
    assignment_id: str,
    report: dict,
    plan: dict,
    model_used: bool,
    model_label: str,
    duration_ms: int,
    kind: str = "preflight",
    content_hash: str = "",
) -> dict:
    audit = {
        "id": new_id(),
        "assignment_id": assignment_id,
        "kind": kind,
        "content_hash": content_hash,
        "report_json": json.dumps(report, ensure_ascii=False),
        "plan_json": json.dumps(plan, ensure_ascii=False),
        "model_used": 1 if model_used else 0,
        "model_label": model_label,
        "duration_ms": int(duration_ms),
        "created_at": utc_now(),
    }
    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO audits(id, assignment_id, kind, content_hash, report_json,
                               plan_json, model_used, model_label, duration_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit["id"], audit["assignment_id"], audit["kind"], audit["content_hash"],
                audit["report_json"], audit["plan_json"], audit["model_used"],
                audit["model_label"], audit["duration_ms"], audit["created_at"],
            ),
        )
        conn.execute(
            "UPDATE assignments SET updated_at = ? WHERE id = ?",
            (audit["created_at"], assignment_id),
        )
        conn.commit()
        audit["report"] = report
        audit["plan"] = plan
        audit["model_used"] = bool(model_used)
        audit.pop("report_json", None)
        audit.pop("plan_json", None)
        return audit
    finally:
        conn.close()


def list_audits(assignment_id: str, limit: int = 20) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM audits WHERE assignment_id = ? ORDER BY created_at DESC LIMIT ?",
            (assignment_id, limit),
        ).fetchall()
        return [_audit_row(r) for r in rows]
    finally:
        conn.close()


def latest_audit(assignment_id: str) -> dict | None:
    audits = list_audits(assignment_id, 1)
    return audits[0] if audits else None


# ── submissions ──────────────────────────────────────────────────────────


def _submission_row(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["coverage"] = _json_load(data.pop("coverage_json", "[]"), [])
    data["gaps"] = _json_load(data.pop("gaps_json", "[]"), [])
    data["plan"] = _json_load(data.pop("plan_json", "{}"), {})
    return data


def save_submission(
    assignment_id: str,
    content_text: str,
    coverage: list,
    gaps: list,
    plan: dict,
    notes: str = "",
    source_name: str = "pasted",
) -> dict:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM submissions WHERE assignment_id = ?",
            (assignment_id,),
        ).fetchone()
        version = int(row["v"]) + 1
        now = utc_now()
        sub_id = new_id()
        conn.execute(
            """
            INSERT INTO submissions(id, assignment_id, version, content_text, source_name,
                                    submitted_at, coverage_json, gaps_json, plan_json, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sub_id, assignment_id, version, (content_text or "")[:80_000],
                source_name[:120], now, json.dumps(coverage, ensure_ascii=False),
                json.dumps(gaps, ensure_ascii=False), json.dumps(plan, ensure_ascii=False),
                (notes or "")[:2000],
            ),
        )
        conn.execute(
            "UPDATE assignments SET status = 'submitted', updated_at = ? WHERE id = ?",
            (now, assignment_id),
        )
        conn.commit()
        return {
            "id": sub_id, "assignment_id": assignment_id, "version": version,
            "submitted_at": now, "coverage": coverage, "gaps": gaps, "plan": plan,
            "notes": notes, "source_name": source_name,
        }
    finally:
        conn.close()


def list_submissions(assignment_id: str) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM submissions WHERE assignment_id = ? ORDER BY version DESC",
            (assignment_id,),
        ).fetchall()
        return [_submission_row(r) for r in rows]
    finally:
        conn.close()


def course_submissions(course_id: str, limit: int = 30) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT s.*, a.title AS assignment_title FROM submissions s
            JOIN assignments a ON a.id = s.assignment_id
            WHERE a.course_id = ? ORDER BY s.submitted_at DESC LIMIT ?
            """,
            (course_id, limit),
        ).fetchall()
        return [_submission_row(r) for r in rows]
    finally:
        conn.close()


def add_feedback(
    submission_id: str, grade: str = "", comments: str = "", tags: list | None = None
) -> dict:
    item = {
        "id": new_id(),
        "submission_id": submission_id,
        "grade": (grade or "")[:40],
        "comments": (comments or "")[:4000],
        "tags_json": json.dumps(tags or [], ensure_ascii=False),
        "created_at": utc_now(),
    }
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO feedback(id, submission_id, grade, comments, tags_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (item["id"], submission_id, item["grade"], item["comments"],
             item["tags_json"], item["created_at"]),
        )
        conn.commit()
        item["tags"] = tags or []
        item.pop("tags_json", None)
        return item
    finally:
        conn.close()


# ── memory ───────────────────────────────────────────────────────────────


def add_memory(
    kind: str,
    text: str,
    course_id: str = "",
    assignment_id: str = "",
    confidence: float = 0.6,
) -> dict:
    item = {
        "id": new_id(),
        "kind": kind,
        "text": text.strip()[:240],
        "course_id": course_id or "",
        "assignment_id": assignment_id or "",
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "created_at": utc_now(),
        "last_used_at": "",
    }
    conn = connect()
    try:
        existing = conn.execute(
            "SELECT id FROM memory WHERE kind = ? AND text = ? AND (course_id = ? OR assignment_id = ?)",
            (kind, item["text"], item["course_id"], item["assignment_id"]),
        ).fetchone()
        if existing:
            item["id"] = existing["id"]
            return item
        conn.execute(
            "INSERT INTO memory(id, kind, text, course_id, assignment_id, confidence,"
            " created_at, last_used_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (item["id"], item["kind"], item["text"], item["course_id"],
             item["assignment_id"], item["confidence"], item["created_at"],
             item["last_used_at"]),
        )
        conn.commit()
        return item
    finally:
        conn.close()


def list_memory(course_id: str = "", assignment_id: str = "", limit: int = 40) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT * FROM memory
            WHERE (course_id != '' AND course_id = ?)
               OR (assignment_id != '' AND assignment_id = ?)
            ORDER BY confidence DESC, created_at DESC LIMIT ?
            """,
            (course_id, assignment_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def all_memory() -> list[dict]:
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM memory ORDER BY created_at DESC"
        ).fetchall()]
    finally:
        conn.close()


def delete_memory(memory_id: str) -> bool:
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM memory WHERE id = ?", (memory_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def touch_memory(memory_ids: list[str]) -> None:
    if not memory_ids:
        return
    conn = connect()
    try:
        conn.executemany(
            "UPDATE memory SET last_used_at = ? WHERE id = ?",
            [(utc_now(), mid) for mid in memory_ids],
        )
        conn.commit()
    finally:
        conn.close()


def clear_course_memory(course_id: str) -> int:
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM memory WHERE course_id = ?", (course_id,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ── messages ─────────────────────────────────────────────────────────────


def add_message(assignment_id: str, role: str, content: str) -> dict:
    item = {
        "id": new_id(),
        "assignment_id": assignment_id,
        "role": role if role in ("user", "coach") else "coach",
        "content": (content or "").strip()[:8000],
        "created_at": utc_now(),
    }
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO messages(id, assignment_id, role, content, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (item["id"], item["assignment_id"], item["role"], item["content"],
             item["created_at"]),
        )
        conn.commit()
        return item
    finally:
        conn.close()


def list_messages(assignment_id: str, limit: int = 40) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM messages WHERE assignment_id = ? ORDER BY created_at ASC",
            (assignment_id,),
        ).fetchall()
        return [dict(r) for r in rows[-limit:]]
    finally:
        conn.close()


def clear_messages(assignment_id: str) -> int:
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM messages WHERE assignment_id = ?", (assignment_id,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ── network ledger ───────────────────────────────────────────────────────


def log_network(url: str, purpose: str, payload_preview: str, status: str) -> dict:
    item = {
        "id": new_id(),
        "url": url[:1000],
        "purpose": purpose[:200],
        "payload_preview": payload_preview[:400],
        "status": status,
        "created_at": utc_now(),
    }
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO network_log(id, url, purpose, payload_preview, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (item["id"], item["url"], item["purpose"], item["payload_preview"],
             item["status"], item["created_at"]),
        )
        conn.commit()
        return item
    finally:
        conn.close()


def list_network(limit: int = 50) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM network_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── settings ─────────────────────────────────────────────────────────────


def get_setting(key: str, default: str = "") -> str:
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def stats() -> dict:
    conn = connect()
    try:
        def count(table: str) -> int:
            return int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

        return {
            "courses": count("courses"),
            "assignments": count("assignments"),
            "audits": count("audits"),
            "submissions": count("submissions"),
            "memory": count("memory"),
            "messages": count("messages"),
            "network": count("network_log"),
        }
    finally:
        conn.close()

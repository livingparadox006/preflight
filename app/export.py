"""Markdown and JSON export. Everything is generated locally."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from companion import deadline_state


def _status_mark(status: str) -> str:
    return {
        "addressed": "[x]",
        "partial": "[~]",
        "missing": "[ ]",
        "contradicted": "[!]",
    }.get(status, "[ ]")


def markdown(
    assignment: dict,
    audit: dict | None,
    submissions: list[dict] | None = None,
    memory: list[dict] | None = None,
) -> str:
    report = (audit or {}).get("report") or {}
    plan = (audit or {}).get("plan") or {}
    submissions = submissions or []
    memory = memory or []
    state = deadline_state(assignment.get("deadline", ""))
    lines = [
        f"# Preflight report — {assignment.get('title', 'Untitled')}",
        "",
        f"- Course: {assignment.get('course_code', 'General')}",
        f"- Deadline: {state['label']} ({assignment.get('deadline') or 'none'})",
        f"- Status: {assignment.get('status', 'active')}",
        f"- Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- Source: {assignment.get('brief_source', 'pasted')}",
        "",
        "> Local-only audit. The model contributed the prioritisation layer; every",
        "> requirement below is quoted from the brief and checked by code.",
        "",
        "## Requirements",
        "",
        "| ID | Kind | Status | Requirement | Source |",
        "|---|---|---|---|---|",
    ]
    for req in report.get("requirements", []):
        text = str(req.get("text", "")).replace("|", "\\|")
        lines.append(
            f"| {req.get('id')} | {req.get('kind')} | {req.get('status')} | {text} | "
            f"{req.get('source_ref')} |"
        )
    coverage = report.get("coverage") or {}
    if coverage:
        counts = coverage.get("counts") or {}
        lines += [
            "",
            "## Draft coverage",
            "",
            f"Evidence found for {coverage.get('coverage_pct', 0)}% of requirements "
            f"({counts.get('addressed', 0)} addressed, {counts.get('partial', 0)} partial, "
            f"{counts.get('missing', 0)} missing, {counts.get('contradicted', 0)} contradicted).",
            "",
        ]
        for item in coverage.get("items", []):
            lines.append(
                f"- {_status_mark(item.get('status'))} **{item.get('id')}** "
                f"{item.get('status')} — {item.get('note', '')}"
            )
            if item.get("evidence"):
                lines.append(f"  > {item['evidence'][:260]}")
    if report.get("contradictions"):
        lines += ["", "## Contradictions", ""]
        for item in report["contradictions"]:
            lines.append(f"- {item.get('text')} ({item.get('a_ref')} / {item.get('b_ref')})")
    if report.get("ambiguities"):
        lines += ["", "## Ambiguities", ""]
        for item in report["ambiguities"]:
            lines.append(f"- “{item.get('term')}” — {item.get('suggestion')} ({item.get('source_ref')})")
    if plan:
        lines += ["", "## Coach plan", ""]
        for index, action in enumerate(plan.get("actions", []), start=1):
            lines.append(
                f"{index}. {action.get('text')} _({action.get('minutes', 15)} min)_"
            )
        if plan.get("risk"):
            lines += ["", f"**Coach's read:** {plan['risk']}"]
    if submissions:
        lines += ["", "## Submission history", ""]
        for sub in submissions:
            lines.append(
                f"- v{sub.get('version')} — {sub.get('submitted_at')} — "
                f"{len(sub.get('gaps') or [])} gap(s)"
            )
    if memory:
        lines += ["", "## Companion memory", ""]
        for item in memory:
            lines.append(f"- {item.get('text')}")
    lines += [
        "",
        "## Integrity note",
        "",
        "This report is a completion strategy, not a submission. It does not contain",
        "answers, solved problems, or submission-ready text.",
        "",
    ]
    return "\n".join(lines)


def as_json(
    assignment: dict,
    audit: dict | None,
    submissions: list[dict] | None = None,
    memory: list[dict] | None = None,
) -> str:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "local_only": True,
        "assignment": {
            key: assignment.get(key)
            for key in (
                "id", "title", "course_code", "course_title", "deadline", "status",
                "brief_source", "created_at", "updated_at",
            )
        },
        "report": (audit or {}).get("report") or {},
        "plan": (audit or {}).get("plan") or {},
        "audit_meta": {
            "id": (audit or {}).get("id"),
            "created_at": (audit or {}).get("created_at"),
            "model_used": (audit or {}).get("model_used"),
            "duration_ms": (audit or {}).get("duration_ms"),
        },
        "submissions": submissions or [],
        "memory": memory or [],
        "integrity": "Completion strategy only. No solved work or submission-ready text.",
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)

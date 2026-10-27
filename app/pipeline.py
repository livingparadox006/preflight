"""The preflight pipeline: evidence first, model second."""

from __future__ import annotations

import time
from typing import Callable

import companion
import coverage as coverage_mod
import db
import extract
import ingest
import model as model_mod
import plan as plan_mod

Emit = Callable[[dict], None]


def build_report(
    assignment: dict,
    brief: str,
    draft: str = "",
    source: str = "pasted",
    today=None,
) -> dict:
    ingest.validate_pair(brief, draft)
    brief = ingest.normalize_text(brief)
    draft = ingest.normalize_text(draft)
    extraction = extract.extract(brief, source=source, today=today)
    coverage = coverage_mod.check(extraction["requirements"], draft)
    deadline = assignment.get("deadline", "") or extraction.get("deadline", "")
    report = {
        "brief_excerpt": ingest.clip(brief, 600),
        "draft_excerpt": ingest.clip(draft, 360),
        "brief_chars": len(brief),
        "draft_chars": len(draft),
        "requirements": extraction["requirements"],
        "ambiguities": extraction["ambiguities"],
        "contradictions": extraction["contradictions"],
        "deliverables": extraction["deliverables"],
        "deadline_detected": extraction["deadline"],
        "deadline": deadline,
        "coverage": coverage,
        "stats": {
            "sentences": extraction["sentence_count"],
            "requirements": len(extraction["requirements"]),
            "deliverables": len(extraction["deliverables"]),
        },
    }
    return report


def run(
    assignment: dict,
    brief: str,
    draft: str = "",
    deadline: str | None = None,
    source: str = "pasted",
    emit: Emit | None = None,
    use_model: bool = True,
    model_timeout: int = 85,
) -> dict:
    def send(event: dict) -> None:
        if emit:
            emit(event)

    started = time.time()
    brief = ingest.normalize_text(brief)
    draft = ingest.normalize_text(draft)
    if deadline is not None:
        assignment = dict(assignment)
        assignment["deadline"] = deadline.strip()

    send({"type": "stage", "stage": "parse", "message": "Reading the brief on this machine."})
    report = build_report(assignment, brief, draft, source=source)
    deadline_value = (assignment.get("deadline") or "").strip()
    if not deadline_value and report.get("deadline_detected"):
        deadline_value = report["deadline_detected"]
        report["deadline"] = deadline_value
    send({"type": "stage", "stage": "evidence", "message": "Evidence built and checked."})
    send({"type": "report", "report": report})

    memory = companion.select_for_prompt(
        db.list_memory(assignment.get("course_id", ""), assignment.get("id", "")),
        assignment,
    )
    model_plan = None
    model_used = False
    model_note = ""
    model_label = model_mod.MODEL_LABEL
    if use_model:
        client = model_mod.LlamaClient()
        if client.health():
            send({"type": "stage", "stage": "coach", "message": "The local coach is ordering the next steps."})
            try:
                model_plan = model_mod.generate_plan(
                    report,
                    memory,
                    deadline=deadline_value,
                    client=client,
                    timeout=model_timeout,
                )
                model_used = bool(model_plan)
                if not model_used:
                    model_note = "The local model returned no usable plan; the deterministic plan is shown."
            except model_mod.ModelTimeout:
                model_note = "The local model timed out; the deterministic plan is shown."
            except model_mod.ModelUnavailable as exc:
                model_note = str(exc)
            except Exception:
                model_note = "The local model could not add commentary; the deterministic plan is shown."
        else:
            model_note = "The local model is offline; the deterministic plan is shown."

    plan = plan_mod.merge(model_plan, report)
    if model_note:
        plan["note"] = model_note
    plan["model_used"] = model_used
    plan["memory_used"] = [item.get("text") for item in memory[:5]]
    send({"type": "plan", "plan": plan})

    duration_ms = int((time.time() - started) * 1000)
    digest = ingest.content_hash(brief, draft, deadline_value)
    audit = db.save_audit(
        assignment["id"],
        report,
        plan,
        model_used=model_used,
        model_label=model_label,
        duration_ms=duration_ms,
        content_hash=digest,
    )
    updated = db.update_assignment(
        assignment["id"],
        brief_text=brief,
        draft_text=draft,
        brief_source=source,
        deadline=deadline_value,
    )
    if memory:
        db.touch_memory([item["id"] for item in memory])
    send(
        {
            "type": "done",
            "audit_id": audit["id"],
            "duration_ms": duration_ms,
            "model_used": model_used,
            "plan_source": plan.get("source"),
            "assignment": updated,
        }
    )
    return {
        "audit": audit,
        "report": report,
        "plan": plan,
        "duration_ms": duration_ms,
        "model_used": model_used,
        "assignment": updated,
    }

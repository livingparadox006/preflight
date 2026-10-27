"""Coach memory, deadline check-ins, and grounded chat context.

The companion is deliberately narrow: it plans, prioritises, explains findings,
and remembers recurring gaps. It does not write the assignment.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import db
from ingest import clip

DEADLINE_FORMATS = (
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)

# Deadlines typed into a datetime-local field are wall-clock time on this
# machine, not UTC. Attaching the local offset keeps "due today" honest.
LOCAL_TZ = datetime.now().astimezone().tzinfo or timezone.utc


def parse_deadline(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in DEADLINE_FORMATS:
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=LOCAL_TZ)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=LOCAL_TZ)
        return parsed
    except ValueError:
        return None


def deadline_state(value: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    parsed = parse_deadline(value)
    if not parsed:
        return {
            "state": "none",
            "label": "No deadline",
            "days": None,
            "hours": None,
            "iso": "",
            "sort": 10**9,
        }
    delta = parsed - now
    hours = delta.total_seconds() / 3600
    if hours < 0:
        state, label = "overdue", "Overdue"
    elif hours < 24:
        state, label = "today", "Due today"
    elif hours < 48:
        state, label = "soon", "Due within 48 hours"
    elif hours < 24 * 7:
        state, label = "week", "Due this week"
    else:
        state, label = "later", "Later"
    return {
        "state": state,
        "label": label,
        "days": round(hours / 24, 1),
        "hours": round(hours, 1),
        "iso": value,
        "sort": parsed.timestamp(),
    }


def checkin(assignment: dict, audit: dict | None) -> dict | None:
    if assignment.get("status") in ("submitted", "graded"):
        return None
    state = deadline_state(assignment.get("deadline", ""))
    if state["state"] == "none":
        return None
    report = (audit or {}).get("report") or {}
    coverage = report.get("coverage") or {}
    gaps = coverage.get("gaps") or []
    missing = [g for g in gaps if g.get("status") == "missing"]
    risk = ((audit or {}).get("plan") or {}).get("risk", "")
    if state["state"] == "overdue":
        headline = f"Overdue: {assignment.get('title', 'this assignment')}."
        detail = "Record what you submitted, then close the loop on the biggest gap."
    elif state["state"] == "today":
        headline = "Due today. Protect the next block of time."
        detail = (
            f"{len(missing)} requirement(s) still have no evidence."
            if missing
            else "Finish the verification checklist before you submit."
        )
    elif state["state"] == "soon":
        headline = f"Due in {max(1, round(state['hours'] / 24))} day(s)."
        detail = (
            f"{len(missing)} requirement(s) still have no evidence."
            if missing
            else "The draft has evidence for every extracted requirement."
        )
    elif state["state"] == "week":
        headline = "Due this week. Book one focused block."
        detail = risk or "Start with the highest-risk gap, not the easiest section."
    else:
        headline = "Deadline is on the horizon."
        detail = risk or "Use the preflight report to start with the right section."
    return {
        "state": state["state"],
        "label": state["label"],
        "deadline": state["iso"],
        "headline": headline,
        "detail": detail,
        "missing": len(missing),
        "risk": risk,
    }


def checkins_for(assignments: list[dict]) -> list[dict]:
    out = []
    for assignment in assignments:
        audit = db.latest_audit(assignment["id"])
        item = checkin(assignment, audit)
        if item:
            item["assignment_id"] = assignment["id"]
            item["title"] = assignment.get("title", "")
            item["course"] = assignment.get("course_code", "")
            out.append(item)
    order = {"overdue": 0, "today": 1, "soon": 2, "week": 3, "later": 4}
    out.sort(key=lambda item: (order.get(item["state"], 9), item.get("deadline", "")))
    return out


def derive_patterns(
    submissions: list[dict],
    course_id: str = "",
    assignment_id: str = "",
    persist: bool = True,
) -> list[dict]:
    """Find recurring gaps after at least two submissions."""
    if len(submissions) < 2:
        return []
    recent = submissions[:5]
    kind_counts: dict[str, int] = {}
    kind_totals: dict[str, int] = {}
    total_missing = 0
    contradiction_count = 0
    for submission in recent:
        coverage = submission.get("coverage") or []
        seen_kinds = set()
        for item in coverage:
            kind = item.get("kind") or "process"
            kind_totals[kind] = kind_totals.get(kind, 0) + 1
            if kind not in seen_kinds and item.get("status") in ("missing", "partial", "contradicted"):
                kind_counts[kind] = kind_counts.get(kind, 0) + 1
                seen_kinds.add(kind)
            if item.get("status") == "missing":
                total_missing += 1
            if item.get("status") == "contradicted":
                contradiction_count += 1

    labels = {
        "acceptance": "Acceptance criteria",
        "constraint": "Limits or units",
        "deliverable": "Deliverables",
        "process": "Method sections",
        "admin": "Submission rules",
    }
    patterns = []
    for kind, count in sorted(kind_counts.items(), key=lambda pair: -pair[1]):
        if count < 2:
            continue
        label = labels.get(kind, "Requirements")
        text = f"{label} were missing or weak in {count} of your last {len(recent)} submissions."
        patterns.append(
            {
                "kind": "pattern",
                "text": text,
                "confidence": min(0.9, 0.55 + count * 0.1),
                "count": count,
                "total": len(recent),
            }
        )
    if total_missing >= 4:
        patterns.append(
            {
                "kind": "pattern",
                "text": "Missing sections have been your main source of lost marks.",
                "confidence": 0.7,
                "count": total_missing,
                "total": len(recent),
            }
        )
    if contradiction_count >= 2:
        patterns.append(
            {
                "kind": "pattern",
                "text": "Conflicting requirements have appeared more than once; read the brief twice.",
                "confidence": 0.72,
                "count": contradiction_count,
                "total": len(recent),
            }
        )
    if persist:
        for pattern in patterns:
            db.add_memory(
                "pattern",
                pattern["text"],
                course_id=course_id,
                assignment_id=assignment_id,
                confidence=pattern["confidence"],
            )
    return patterns


def deadline_memory(submission: dict, assignment: dict) -> dict | None:
    due = parse_deadline(assignment.get("deadline", ""))
    submitted = parse_deadline(submission.get("submitted_at", ""))
    if not due or not submitted:
        return None
    if submitted > due:
        return db.add_memory(
            "deadline",
            f"You submitted “{assignment.get('title', 'an assignment')}” after the deadline.",
            course_id=assignment.get("course_id", ""),
            assignment_id=assignment.get("id", ""),
            confidence=0.8,
        )
    return None


def select_for_prompt(
    memory: list[dict],
    assignment: dict,
    limit: int = 5,
) -> list[dict]:
    def score(item: dict) -> tuple:
        same_assignment = item.get("assignment_id") == assignment.get("id")
        same_course = item.get("course_id") == assignment.get("course_id")
        return (same_assignment, same_course, item.get("confidence", 0), item.get("created_at", ""))

    ranked = sorted(memory, key=score, reverse=True)
    return ranked[:limit]


def rolling_summary(messages: list[dict], window: int = 8) -> str:
    older = messages[:-window] if len(messages) > window else []
    if not older:
        return ""
    user_text = " ".join(m.get("content", "") for m in older if m.get("role") == "user")
    words = re.findall(r"[a-zA-Z][a-zA-Z]{3,}", user_text.lower())
    stop = {
        "that", "this", "with", "have", "what", "when", "where", "which", "about",
        "should", "could", "would", "there", "their", "from", "they", "will",
        "just", "like", "need", "want", "make", "does", "your", "assignment",
    }
    topics = []
    for word in words:
        if word in stop or word in topics:
            continue
        topics.append(word)
        if len(topics) >= 8:
            break
    if not topics:
        return f"Earlier conversation: {len(older)} message(s)."
    return "Earlier you discussed: " + ", ".join(topics) + "."


def context_block(
    assignment: dict,
    audit: dict | None,
    memory: list[dict],
    messages: list[dict],
) -> str:
    report = (audit or {}).get("report") or {}
    plan = (audit or {}).get("plan") or {}
    coverage = report.get("coverage") or {}
    counts = coverage.get("counts") or {}
    state = deadline_state(assignment.get("deadline", ""))
    lines = [
        f"ASSIGNMENT: {assignment.get('title', 'Untitled')}",
        f"COURSE: {assignment.get('course_code', 'General')}",
        f"DEADLINE: {state['label']} ({assignment.get('deadline', 'none')})",
        f"BRIEF EXCERPT: {clip(assignment.get('brief_text', ''), 700)}",
        "REQUIREMENTS AND COVERAGE (authoritative):",
    ]
    covered = {item.get("id"): item for item in coverage.get("items") or []}
    for req in (report.get("requirements") or [])[:8]:
        item = covered.get(req.get("id")) or {}
        status = item.get("status") or "not checked"
        lines.append(f"- {req.get('id')} [{status}] {clip(req.get('text', ''), 130)}")
    if coverage:
        lines.append(
            "COVERAGE COUNTS: "
            + ", ".join(f"{k}={v}" for k, v in counts.items())
            + f" ({coverage.get('coverage_pct', 0)}% addressed)"
        )
        lines.append(
            "RULE: the statuses above are final. Only call an item covered when its "
            "status is addressed. List missing, partial, and contradicted items first."
        )
    gaps = coverage.get("gaps") or []
    if gaps:
        lines.append("UNRESOLVED GAPS:")
        for gap in gaps[:8]:
            lines.append(f"- {gap.get('id')} {gap.get('status')}: {clip(gap.get('text', ''), 110)}")
    if plan.get("actions"):
        lines.append("CURRENT PLAN:")
        for action in plan["actions"]:
            lines.append(f"- {action.get('text')} ({action.get('minutes', 15)} min)")
    if memory:
        lines.append("MEMORY (local facts):")
        for item in memory[:5]:
            lines.append(f"- {clip(item.get('text', ''), 120)}")
    summary = rolling_summary(messages)
    if summary:
        lines.append(f"CONVERSATION SUMMARY: {summary}")
    return "\n".join(lines)


READY_CLAIM = re.compile(
    r"\b(ready\s+to\s+submit|ready\s+now|good\s+to\s+go|you(?:'re|\s+are)\s+ready|"
    r"almost\s+there|ready\s+for\s+submission|complete\s+and\s+ready|submit\s+now)\b",
    re.I,
)
READY_QUESTION = re.compile(r"\b(ready|submit|good\s+to\s+go|finished|done)\b", re.I)
COVERAGE_QUESTION = re.compile(
    r"\b(missing|left|gaps?|covered|coverage|addressed|checklist|"
    r"which\s+requirements?|requirements?\s+status)\b",
    re.I,
)
COVERAGE_CLAIM = re.compile(
    r"\b(covered|addressed|satisfied|met|handled|included|present|complete|done)\b",
    re.I,
)
COVERAGE_GLOBAL = re.compile(
    r"\b(all|every|everything|each)\b.{0,40}\b(covered|addressed|satisfied|met|complete|done)\b",
    re.I,
)
COVERAGE_NEGATED = re.compile(
    r"\b(not|isn't|aren't|never|no|missing|partial|unresolved|needs?|lacks?|without)\b",
    re.I,
)
COVERAGE_ADVICE = re.compile(
    r"\b(ensure|make\s+sure|check|verify|should|must|address|fix|add|"
    r"complete|before|still|remaining|needs?\s+to)\b",
    re.I,
)


def is_coverage_question(question: str) -> bool:
    """Questions about checklist state must come from the report, not the model."""
    return bool(COVERAGE_QUESTION.search(question or ""))


def is_readiness_question(question: str) -> bool:
    """Submission-readiness answers are a coverage decision, not a model opinion."""
    return bool(READY_QUESTION.search(question or ""))


PLAN_QUESTION = re.compile(
    r"\b(first|start|next|priority|priorit\w*|order)\b",
    re.I,
)


def is_plan_question(question: str) -> bool:
    """The next-step chip should read the existing plan, not regenerate it."""
    return bool(PLAN_QUESTION.search(question or ""))


DEADLINE_QUESTION = re.compile(
    r"\b(deadline|due|late|time\s+left|how\s+long)\b",
    re.I,
)


def is_deadline_question(question: str) -> bool:
    return bool(DEADLINE_QUESTION.search(question or ""))


WORK_REQUEST = re.compile(
    r"(?:"
    r"\b(?:can|could|would|will)\s+you\s+(?:please\s+)?"
    r"(?:write|draft|compose|produce|generate|create|solve|answer|complete|finish|do)\b"
    r"|"
    r"\b(?:please\s+)?(?:write|draft|compose|produce|generate|create|solve|answer|complete|finish)\b"
    r".{0,80}\b(?:for\s+me|my\s+(?:assignment|homework|essay|memo|report|code|program))\b"
    r"|"
    r"\b(?:write|draft|compose|produce|generate|create)\s+(?:the|a|an|my)\s+"
    r"(?:recommendation|conclusion|introduction|abstract|summary|section|paragraph|"
    r"memo|report|essay|code|program|answer|derivation|calculation)\b"
    r"|"
    r"\b(?:write|draft|compose|produce|generate|create|solve|answer|complete|finish)\s+me\s+"
    r"(?:a|an|the|my)\b"
    r"|"
    r"\b(?:what|how)\s+(?:would|should|does|do)\s+(?:a|an|the|my)\s+"
    r"(?:strong|good|great|effective|sample|model|ideal)\b.{0,60}"
    r"\b(?:look\s+like|read\s+like|sound\s+like)\b"
    r"|"
    r"\b(?:show|give)\s+me\s+(?:an?\s+)?(?:example|sample|template|model)\b"
    r"|"
    r"\b(?:example|sample|template)\s+(?:answer|section|paragraph|response|recommendation|"
    r"conclusion|introduction|abstract|memo|report)\b"
    r"|"
    r"\b(?:solve|answer|complete|finish|do)\s+(?:the|this|my)\s+"
    r"(?:problem|assignment|homework|task|question|exercise|calculation)\b"
    r")",
    re.I,
)
META_WORK = re.compile(
    r"\b(?:how|what|why|where|when)\b.{0,40}"
    r"\b(?:approach|structure|plan|revise|check|write|draft|solve|code)\b",
    re.I,
)


def is_work_request(question: str) -> bool:
    """True when the student asks for the work itself, not coaching on the work."""
    question = question or ""
    if META_WORK.search(question):
        return False
    return bool(WORK_REQUEST.search(question))


GHOST_STRONG = re.compile(
    r"(```|"
    r"\bhere(?:'s| is)\s+(?:a|the|your)\s+(?:sample|draft|completed|finished|full|final|"
    r"written|polished|submission-ready)?\s*(?:recommendation|conclusion|introduction|"
    r"abstract|section|paragraph|memo|report|code|version)\b|"
    r"\bhere(?:'s| is)\s+(?:a|the)\s+(?:sample|draft|completed|finished)\b|"
    r"\b(?:sample|draft|suggested|final|completed|polished|submission-ready)\s+"
    r"(?:recommendation|conclusion|introduction|abstract|section|paragraph|answer|"
    r"response|memo|report)\s+(?:you can use|below|follows|to use)\b|"
    r"\byou can (?:copy|paste|submit|use this|use the following|use it)\b|"
    r"\bfeel free to (?:use|copy|paste|adapt|submit)\b|"
    r"\b(?:copy|paste) (?:this|the following|it)\b|"
    r"\b(?:final|polished|submission-ready)\s+version\b)",
    re.I,
)
GHOST_HEADING = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]+(?:recommendation|conclusion|introduction|abstract|draft|"
    r"answer|response|memo|report)\b.*|"
    r"\*\*(?:recommendation|conclusion|introduction|abstract|draft|answer|response|"
    r"memo|report)\s*:?\*\*[ \t]*:?[ \t]*$|"
    r"(?:recommendation|conclusion|introduction|abstract|draft|sample answer)[ \t]*:?[ \t]*$)",
    re.I | re.M,
)
GHOST_CODE = re.compile(
    r"^[ \t]{4,}(?:def |class |#include|import |from |for |while |if |return )",
    re.M,
)


def looks_ghostwritten(reply: str) -> bool:
    """Detect a model reply that contains a sample, draft, or submission-ready text."""
    text = (reply or "").strip()
    if not text:
        return False
    if GHOST_STRONG.search(text) or GHOST_CODE.search(text):
        return True
    if GHOST_HEADING.search(text) and len(text) > 200:
        return True
    return False


def work_refusal_reply(audit: dict | None) -> str:
    """Planning-only redirect for requests to write the assignment."""
    coverage = ((audit or {}).get("report") or {}).get("coverage") or {}
    unresolved = [
        item for item in coverage.get("items") or []
        if item.get("status") in ("missing", "contradicted", "partial")
    ]
    order = {"missing": 0, "contradicted": 1, "partial": 2}
    unresolved.sort(key=lambda item: order.get(item.get("status"), 3))
    first = unresolved[0].get("id") if unresolved else ""
    next_step = f" Start with {first} and rerun the preflight." if first else ""
    return (
        "I can't write sections, code, or submission-ready text for you. I can help you plan it: "
        "state the decision your evidence supports, connect it to the diagrams and failure modes "
        f"you already have, and stay inside the brief's word limit.{next_step} "
        "This is an assignment checklist, not an engineering correctness check."
    )


def sanitize_model_reply(reply: str, audit: dict | None) -> tuple[str, bool]:
    """Replace ghostwritten model output with a planning-only redirect."""
    if looks_ghostwritten(reply):
        return work_refusal_reply(audit), True
    return reply, False


def coverage_reply(audit: dict | None) -> str:
    """Deterministic checklist answer built directly from the preflight report."""
    report = (audit or {}).get("report") or {}
    coverage = report.get("coverage") or {}
    items = coverage.get("items") or []
    counts = coverage.get("counts") or {}
    if not coverage:
        return "Run a preflight first. I answer checklist questions only from its evidence table."
    if not coverage.get("has_draft"):
        return (
            f"No draft is checked yet, so no evidence status exists for the "
            f"{coverage.get('total', len(items))} extracted requirements. "
            "Add a draft and run the preflight; this is an assignment checklist, not a grade."
        )
    unresolved = [
        item for item in items
        if item.get("status") in ("missing", "contradicted", "partial")
    ]
    order = {"missing": 0, "contradicted": 1, "partial": 2}
    unresolved.sort(key=lambda item: order.get(item.get("status"), 3))
    summary = ", ".join(
        f"{counts.get(key, 0)} {key}"
        for key in ("missing", "contradicted", "partial", "addressed")
        if counts.get(key, 0)
    ) or "no evidence status yet"
    if not unresolved:
        return (
            f"Assignment checklist (evidence, not a grade): all "
            f"{coverage.get('total', len(items))} requirements have evidence in the draft "
            f"({summary}). Check formatting and submission rules yourself before submitting."
        )
    parts = []
    for item in unresolved[:6]:
        text = clip(item.get("text", ""), 90).rstrip(" .;:")
        parts.append(f"{item.get('id')} {item.get('status')}: {text}")
    extra = len(unresolved) - len(parts)
    tail = f" Plus {extra} more." if extra > 0 else ""
    body = " · ".join(parts)
    if not body.endswith(("…", ".", "!", "?")):
        body += "."
    return (
        f"Assignment checklist (evidence, not a grade): {summary}. "
        f"{body}{tail} Start with {unresolved[0].get('id')}, then rerun the preflight."
    )


def readiness_reply(audit: dict | None) -> str:
    """Deterministic submit/not-yet answer from the latest coverage snapshot."""
    report = (audit or {}).get("report") or {}
    coverage = report.get("coverage") or {}
    if not coverage:
        return "Run a preflight first. Readiness is decided by the coverage evidence, not a guess."
    if not coverage.get("has_draft"):
        return (
            "Not ready to judge yet. There is no checked draft. Add one and run the preflight, "
            "then I can list what still needs evidence."
        )
    counts = coverage.get("counts") or {}
    items = coverage.get("items") or []
    unresolved = [
        item for item in items
        if item.get("status") in ("missing", "contradicted", "partial")
    ]
    order = {"missing": 0, "contradicted": 1, "partial": 2}
    unresolved.sort(key=lambda item: order.get(item.get("status"), 3))
    if not unresolved:
        return (
            f"Checklist clear: all {coverage.get('total', len(items))} requirements have "
            "evidence in the draft. Check formatting, the deadline, and your own engineering "
            "judgement before submitting. This is not a grade."
        )
    names = "; ".join(
        f"{item.get('id')} {item.get('status')}" for item in unresolved[:6]
    )
    tail = " plus others" if len(unresolved) > 6 else ""
    return (
        f"Not yet. {len(unresolved)} requirement(s) still need evidence: {names}{tail}. "
        f"Fix {unresolved[0].get('id')} first, rerun the preflight, then submit. "
        "This is an assignment checklist, not an engineering correctness check."
    )


def deadline_reply(assignment: dict, audit: dict | None) -> str:
    """Deterministic deadline answer: urgency first, then remaining evidence."""
    state = deadline_state(assignment.get("deadline", ""))
    if state["state"] == "none":
        return "No deadline is set. Add one and I can tell you what to protect first."
    report = (audit or {}).get("report") or {}
    coverage = report.get("coverage") or {}
    unresolved = [
        item for item in coverage.get("items") or []
        if item.get("status") in ("missing", "contradicted", "partial")
    ]
    order = {"missing": 0, "contradicted": 1, "partial": 2}
    unresolved.sort(key=lambda item: order.get(item.get("status"), 3))
    if not unresolved:
        return (
            f"{state['label']}. The checklist has evidence for every extracted requirement. "
            "Use the remaining time for formatting and a final read-through. This is not a grade."
        )
    first = unresolved[0].get("id")
    return (
        f"{state['label']}. {len(unresolved)} requirement(s) still need evidence before "
        f"submission. Protect a focused block for {first} first, then rerun the preflight. "
        "This is an assignment checklist, not an engineering correctness check."
    )


def plan_reply(audit: dict | None) -> str:
    """Deterministic next-step answer from the latest saved plan."""
    plan = (audit or {}).get("plan") or {}
    actions = plan.get("actions") or []
    if not actions:
        return "Run a preflight first. Then I can order the next three steps from the report."
    steps = " ".join(
        f"{index + 1}) {action.get('text')} ({action.get('minutes', 15)} min)."
        for index, action in enumerate(actions[:3])
    )
    risk = str(plan.get("risk") or "").strip()
    reply = f"Next three steps from your latest plan: {steps}"
    if risk:
        reply += f" Risk: {risk}"
    return reply


def claims_unresolved_covered(reply: str, coverage: dict) -> bool:
    """True when a reply says an unresolved requirement is covered."""
    unresolved = [
        item for item in coverage.get("gaps") or []
        if item.get("id") and item.get("status") in ("missing", "partial", "contradicted")
    ]
    if not unresolved:
        return False
    for sentence in re.split(r"(?<=[.!?])\s+", reply or ""):
        if not COVERAGE_CLAIM.search(sentence) or COVERAGE_NEGATED.search(sentence):
            continue
        if COVERAGE_ADVICE.search(sentence):
            continue
        if COVERAGE_GLOBAL.search(sentence):
            return True
        for item in unresolved:
            if re.search(rf"\b{re.escape(str(item['id']))}\b", sentence, re.I):
                return True
    return False


def guard_reply(reply: str, audit: dict | None, question: str) -> str:
    """Keep the model from contradicting the preflight report.

    Small local models sometimes ignore the system prompt. Coverage is the
    source of truth: no optimistic prose can mark an unresolved item covered
    or declare the work ready.
    """
    if not reply:
        return reply
    report = (audit or {}).get("report") or {}
    coverage = report.get("coverage") or {}
    counts = coverage.get("counts") or {}
    unresolved = sum(
        int(counts.get(key, 0) or 0)
        for key in ("missing", "partial", "contradicted")
    )
    if not unresolved:
        return reply
    if READY_QUESTION.search(question or "") or READY_CLAIM.search(reply):
        gaps = coverage.get("gaps") or []
        names = ", ".join(
            f"{gap.get('id')} ({gap.get('status')})" for gap in gaps[:4] if gap.get("id")
        )
        detail = f": {names}" if names else ""
        return (
            f"Not yet. {unresolved} requirement(s) still need evidence{detail}. "
            "Close the first gap, rerun the preflight, then submit."
        )
    if claims_unresolved_covered(reply, coverage):
        return coverage_reply(audit)
    return reply


def fallback_reply(assignment: dict, audit: dict | None, question: str) -> str:
    report = (audit or {}).get("report") or {}
    plan = (audit or {}).get("plan") or {}
    coverage = report.get("coverage") or {}
    actions = plan.get("actions") or []
    gaps = coverage.get("gaps") or []
    state = deadline_state(assignment.get("deadline", ""))
    q = (question or "").lower()

    if any(word in q for word in ("deadline", "due", "time", "late")):
        if state["state"] == "none":
            return "No deadline is set. Add one and I can tell you what to protect first."
        return f"{state['label']}. " + (
            f"Start with: {actions[0]['text']}" if actions else "Run a preflight first."
        )
    if any(word in q for word in ("first", "start", "next", "priority")):
        if len(actions) >= 2:
            return f"First: {actions[0]['text']} ({actions[0].get('minutes', 15)} min). Then: {actions[1]['text']}"
        if actions:
            return f"First: {actions[0]['text']} ({actions[0].get('minutes', 15)} min)."
        return "Run a preflight so I can ground the next step in the brief."
    if any(word in q for word in ("missing", "gap", "weak", "wrong")):
        if gaps:
            top = gaps[:3]
            return "The draft is missing evidence for: " + "; ".join(
                f"{g.get('id')} {g.get('status')}" for g in top
            ) + ". Fix the first one, then re-check."
        return "No gaps are recorded yet. Add a draft and run the coverage check."
    if any(word in q for word in ("submit", "ready", "check")):
        return "Before submitting: verify every requirement has evidence, resolve contradictions, and re-read the formatting rules. I can run a final coverage check."
    if any(word in q for word in ("help", "how", "explain")):
        return "I can triage the deadline, explain a finding, or order the remaining work. I will not write the assignment or solve the problem."
    risk = plan.get("risk", "")
    if actions:
        return f"Focus on this: {actions[0]['text']}" + (f" Risk: {risk}" if risk else "")
    return "Run a preflight first. I need the brief to give you a grounded next step."

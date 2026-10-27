"""Deterministic prioritisation and the integrity filter.

The model may phrase the plan. It may not add requirements, write the answer, or
turn the app into a ghostwriter. Every model action passes through the filter
below; if it fails, the deterministic plan is used instead.
"""

from __future__ import annotations

import re

SOLUTION_PATTERNS = re.compile(
    r"(```|^\s*(def|class|function|import|#include)\b|"
    r"\b(here(?:'s| is) (?:the|your) (?:answer|solution|code|report)|"
    r"the answer is|copy (?:this|the following)|paste this|"
    r"submit (?:this|the following)|solution:|answer:))",
    re.I,
)
MAX_WORDS = 14
MAX_CHARS = 150


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip().strip('"').strip("'")
    text = re.sub(r"^[\-\*\d.)\s]+", "", text)
    return text


def sanitize_action(text: str) -> str | None:
    text = _clean(text)
    if not text or len(text) < 8:
        return None
    if SOLUTION_PATTERNS.search(text):
        return None
    words = text.split()
    if len(words) > MAX_WORDS:
        text = " ".join(words[:MAX_WORDS]).rstrip(",;:.-")
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rsplit(" ", 1)[0]
    if not text[0].isupper():
        text = text[0].upper() + text[1:]
    if text[-1] not in ".!?":
        text += "."
    return text


def sanitize_risk(text: str) -> str | None:
    text = _clean(text)
    if not text or len(text) < 8:
        return None
    if SOLUTION_PATTERNS.search(text):
        return None
    words = text.split()
    if len(words) > 18:
        text = " ".join(words[:18]).rstrip(",;:.-") + "."
    if not text[0].isupper():
        text = text[0].upper() + text[1:]
    if text[-1] not in ".!?":
        text += "."
    return text[:180]


def _minutes(category: str, status: str) -> int:
    if category == "ask":
        return 10
    if category == "verify":
        return 15
    if category == "format":
        return 10
    if status == "contradicted":
        return 20
    if status == "missing":
        return 30
    if status == "partial":
        return 20
    if category == "acceptance":
        return 20
    return 15


def candidate_actions(report: dict) -> list[dict]:
    coverage = report.get("coverage") or {}
    items = coverage.get("items") or []
    requirements = {r["id"]: r for r in report.get("requirements", [])}
    candidates: list[dict] = []

    order = {"contradicted": 0, "missing": 1, "partial": 2}
    kind_order = {"acceptance": 0, "deliverable": 1, "constraint": 2, "process": 3, "admin": 4}
    for item in sorted(
        items,
        key=lambda i: (
            order.get(i.get("status"), 3),
            kind_order.get(
                requirements.get(i.get("id"), {}).get("kind", i.get("kind")), 5
            ),
        ),
    ):
        status = item.get("status")
        if status == "addressed":
            continue
        req = requirements.get(item.get("id"), {})
        kind = req.get("kind", item.get("kind", "process"))
        rid = item.get("id", "?")
        if status == "contradicted":
            text = f"Resolve the conflict around {rid} before drafting."
            category = "contradiction"
        elif kind == "acceptance":
            text = "Add a measurable acceptance criterion before drafting."
            category = "acceptance"
        elif kind == "constraint":
            text = "Define the missing limit or unit before continuing."
            category = "constraint"
        elif kind == "admin":
            text = "Check the submission rules before final formatting."
            category = "format"
        elif kind == "deliverable":
            text = "Draft the missing deliverable before polishing."
            category = "deliverable"
        elif status == "partial":
            text = f"Strengthen the evidence for {rid} with a specific value."
            category = "evidence"
        else:
            text = f"Draft the missing section for {rid} before polishing."
            category = "section"
        candidates.append(
            {
                "text": text,
                "why": item.get("note", ""),
                "minutes": _minutes(category, status),
                "priority": len(candidates) + 1,
                "requirement_id": rid,
                "category": category,
                "source": "deterministic",
            }
        )

    for ambiguity in report.get("ambiguities", [])[:2]:
        term = ambiguity.get("term", "the vague term")
        candidates.append(
            {
                "text": f"Ask the instructor to define “{term}”.",
                "why": ambiguity.get("text", ""),
                "minutes": _minutes("ask", "partial"),
                "priority": len(candidates) + 1,
                "requirement_id": "",
                "category": "ask",
                "source": "deterministic",
            }
        )

    for claim in (coverage.get("untestable") or [])[:1]:
        candidates.append(
            {
                "text": "Add a measurement to the untestable claim.",
                "why": claim.get("text", ""),
                "minutes": 15,
                "priority": len(candidates) + 1,
                "requirement_id": "",
                "category": "evidence",
                "source": "deterministic",
            }
        )

    candidates.append(
        {
            "text": "Run the verification checklist before you submit.",
            "why": "A final evidence check catches requirements that were missed.",
            "minutes": _minutes("verify", "partial"),
            "priority": len(candidates) + 1,
            "requirement_id": "",
            "category": "verify",
            "source": "deterministic",
        }
    )
    return candidates


def fallback_risk(report: dict) -> str:
    contradictions = report.get("contradictions") or []
    if contradictions:
        return "Two requirements conflict; resolve that before drafting."
    coverage = report.get("coverage") or {}
    items = coverage.get("items") or []
    missing = [i for i in items if i.get("status") == "missing"]
    vague = [r for r in report.get("requirements", []) if r.get("status") == "vague"]
    untestable = coverage.get("untestable") or []
    if vague:
        return "A requirement is not measurable yet; define it before drafting."
    if untestable:
        return "The draft makes a claim without a measurement."
    if missing:
        return f"{len(missing)} requirement(s) have no evidence in the draft."
    if items:
        return "Coverage looks complete; verify the submission rules."
    return "Add a draft to check coverage, not just the brief."


def deterministic_plan(report: dict) -> dict:
    candidates = candidate_actions(report)
    actions = []
    seen = set()
    for candidate in candidates:
        text = sanitize_action(candidate["text"]) or candidate["text"]
        if text in seen:
            continue
        seen.add(text)
        actions.append(
            {
                "text": text,
                "minutes": candidate["minutes"],
                "why": candidate.get("why", ""),
                "requirement_id": candidate.get("requirement_id", ""),
                "category": candidate.get("category", ""),
                "source": "deterministic",
            }
        )
        if len(actions) >= 3:
            break
    while len(actions) < 3:
        filler = [
            ("Break the remaining work into three short blocks.", 15, "plan"),
            ("Check the brief for a limit you have not written down.", 10, "verify"),
            ("Leave time to re-read the submission rules.", 10, "format"),
        ][len(actions) % 3]
        actions.append(
            {
                "text": filler[0],
                "minutes": filler[1],
                "why": "Keeps the plan actionable when the brief is thin.",
                "requirement_id": "",
                "category": filler[2],
                "source": "deterministic",
            }
        )
    return {
        "actions": actions,
        "risk": fallback_risk(report),
        "source": "deterministic",
        "estimated_minutes": sum(a["minutes"] for a in actions),
    }


def merge(model_plan: dict | None, report: dict) -> dict:
    base = deterministic_plan(report)
    if not model_plan:
        return base

    raw_actions = model_plan.get("actions") or []
    if not isinstance(raw_actions, list):
        raw_actions = []
    cleaned = []
    for raw in raw_actions:
        text = sanitize_action(raw if isinstance(raw, str) else str(raw))
        if text:
            cleaned.append(text)
    if len(cleaned) < 1:
        return base

    used = set()
    actions = []
    candidates = base["actions"]
    for text in cleaned[:3]:
        # Reuse the deterministic estimate when the wording matches a candidate
        # category; otherwise use a neutral estimate.
        minutes = 15
        category = "model"
        requirement_id = ""
        for candidate in candidates:
            if candidate["text"] in used:
                continue
            overlap = len(set(text.lower().split()) & set(candidate["text"].lower().split()))
            if overlap >= 3:
                minutes = candidate["minutes"]
                category = candidate["category"]
                requirement_id = candidate["requirement_id"]
                used.add(candidate["text"])
                break
        actions.append(
            {
                "text": text,
                "minutes": minutes,
                "why": "Added by the local coach.",
                "requirement_id": requirement_id,
                "category": category,
                "source": "model",
            }
        )
    for candidate in candidates:
        if len(actions) >= 3:
            break
        if candidate["text"] in used:
            continue
        actions.append(candidate)
    risk = sanitize_risk(model_plan.get("risk", "")) or base["risk"]
    return {
        "actions": actions[:3],
        "risk": risk,
        "source": "mixed" if any(a["source"] == "model" for a in actions) else "deterministic",
        "estimated_minutes": sum(a["minutes"] for a in actions[:3]),
    }

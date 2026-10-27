"""Draft-to-requirement coverage.

Coverage is evidence, not a grade. The output says whether evidence was found,
not whether the assignment will pass.
"""

from __future__ import annotations

import re

from extract import content_words

from ingest import split_sentences

VAGUE = re.compile(
    r"\b(some|several|appropriate|as\s+needed|etc\.?|reasonable|fast|efficient|"
    r"good|adequate|sufficient|various|significant|proper|suitable|"
    r"user[- ]friendly|high[- ]quality|minimal|roughly|approximately|about)\b",
    re.I,
)
CLAIM = re.compile(
    r"\b(will|would|can|achieves?|improves?|increases?|reduces?|provides?|"
    r"ensures?|guarantees?|supports?|handles?|performs?)\b", re.I
)
NEGATION = re.compile(r"\b(not|no|never|omit|skip|without|excluded|optional)\b", re.I)


def stem(word: str) -> str:
    word = word.lower()
    for suffix in ("ations", "ation", "ments", "ment", "ings", "ing", "ies", "ied",
                   "ers", "er", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            word = word[: -len(suffix)]
            break
    return word


def _stems(words: list[str]) -> set[str]:
    return {stem(w) for w in words}


def _best_sentence(req_words: set[str], sentences: list[dict]) -> tuple[dict | None, list[str]]:
    best = None
    best_matches: list[str] = []
    for sent in sentences:
        sent_stems = _stems(content_words(sent["text"]))
        matches = sorted(req_words & sent_stems)
        if len(matches) > len(best_matches):
            best = sent
            best_matches = matches
    return best, best_matches


def _requirements_words(text: str) -> set[str]:
    return _stems(content_words(text))


def check_one(requirement: dict, draft: str, draft_sentences: list[dict] | None = None) -> dict:
    draft = draft or ""
    sentences = draft_sentences if draft_sentences is not None else split_sentences(draft, "draft")
    req_words = _requirements_words(requirement.get("text", ""))
    if not req_words:
        req_words = _stems(content_words(requirement.get("evidence", "")))
    best, matches = _best_sentence(req_words, sentences)
    ratio = len(matches) / max(1, len(req_words))
    numbers = re.findall(r"\b\d+(?:\.\d+)?\b", requirement.get("text", ""))
    draft_numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", draft))
    numeric_ok = all(number in draft_numbers for number in numbers) if numbers else True
    numeric_any = any(number in draft_numbers for number in numbers) if numbers else False

    contradiction = None
    for sent in sentences:
        sent_stems = _stems(content_words(sent["text"]))
        overlap = req_words & sent_stems
        if len(overlap) >= 2 and NEGATION.search(sent["text"]):
            contradiction = sent
            break
        if (
            numbers
            and requirement.get("kind") == "constraint"
            and len(overlap) >= 2
            and not numeric_any
            and _same_unit_conflict(requirement.get("text", ""), sent["text"])
        ):
            contradiction = sent
            break

    if contradiction:
        status = "contradicted"
    elif ratio >= 0.52 and (numeric_ok or not numbers):
        status = "addressed"
    elif ratio >= 0.22 or numeric_any:
        status = "partial"
    else:
        status = "missing"

    evidence = ""
    if best is not None and matches:
        evidence = best["text"][:300]
    return {
        "id": requirement.get("id"),
        "text": requirement.get("text"),
        "kind": requirement.get("kind"),
        "requirement_status": requirement.get("status"),
        "status": status,
        "evidence": evidence,
        "source_ref": requirement.get("source_ref"),
        "matched_terms": matches[:8],
        "overlap": round(ratio, 2),
        "numeric_expected": numbers,
        "numeric_found": sorted(draft_numbers)[:10],
        "note": _note(status, numbers, numeric_any),
    }


def _same_unit_conflict(requirement: str, sentence: str) -> bool:
    pairs_req = re.findall(r"(\d+(?:\.\d+)?)\s?([a-zA-Z%°]+)", requirement)
    pairs_sent = re.findall(r"(\d+(?:\.\d+)?)\s?([a-zA-Z%°]+)", sentence)
    for req_value, req_unit in pairs_req:
        for sent_value, sent_unit in pairs_sent:
            if req_unit.lower() == sent_unit.lower() and req_value != sent_value:
                return True
    return False


def _note(status: str, numbers: list[str], numeric_any: bool) -> str:
    if status == "addressed":
        return "Evidence found in the draft."
    if status == "partial":
        if numbers and not numeric_any:
            return "Mentioned, but the required value or unit is missing."
        return "Some evidence found; the condition is not fully met."
    if status == "contradicted":
        return "The draft appears to conflict with this requirement."
    return "No evidence found in the draft."


def check(requirements: list[dict], draft: str) -> dict:
    sentences = split_sentences(draft, "draft") if draft.strip() else []
    items = [check_one(req, draft, sentences) for req in requirements]
    counts = {"addressed": 0, "partial": 0, "missing": 0, "contradicted": 0}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    total = len(items)
    covered = counts["addressed"]
    gaps = [item for item in items if item["status"] in ("missing", "partial", "contradicted")]
    untestable = []
    if draft.strip():
        for sent in sentences:
            if CLAIM.search(sent["text"]) and VAGUE.search(sent["text"]):
                untestable.append(
                    {"text": sent["text"][:240], "source_ref": sent["source_ref"]}
                )
            elif CLAIM.search(sent["text"]) and re.search(
                r"\b(better|faster|safer|more|less)\b", sent["text"], re.I
            ) and not re.search(r"\d", sent["text"]):
                untestable.append(
                    {"text": sent["text"][:240], "source_ref": sent["source_ref"]}
                )
    return {
        "items": items,
        "counts": counts,
        "total": total,
        "coverage_pct": round((covered / total) * 100) if total else 0,
        "gaps": gaps,
        "untestable": untestable[:6],
        "has_draft": bool(draft.strip()),
    }


def diff(previous: list[dict], current: list[dict]) -> dict:
    """Compare two coverage snapshots by requirement text."""
    prev = {re.sub(r"\s+", " ", item.get("text", "")).strip().lower(): item for item in previous}
    improved, regressed, changed, added, removed = [], [], [], [], []
    rank = {"missing": 0, "partial": 1, "contradicted": 1, "addressed": 2}
    for item in current:
        key = re.sub(r"\s+", " ", item.get("text", "")).strip().lower()
        old = prev.get(key)
        if not old:
            added.append(item)
            continue
        old_status = old.get("status")
        new_status = item.get("status")
        if rank.get(new_status, 0) > rank.get(old_status, 0):
            improved.append({"text": item.get("text"), "from": old_status, "to": new_status})
        elif rank.get(new_status, 0) < rank.get(old_status, 0):
            regressed.append({"text": item.get("text"), "from": old_status, "to": new_status})
        elif old_status != new_status:
            changed.append({"text": item.get("text"), "from": old_status, "to": new_status})
        else:
            continue
    for key, old in prev.items():
        if not any(
            re.sub(r"\s+", " ", item.get("text", "")).strip().lower() == key for item in current
        ):
            removed.append(old)
    return {
        "improved": improved,
        "regressed": regressed,
        "changed": changed,
        "added": added,
        "removed": removed,
    }

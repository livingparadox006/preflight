"""Deterministic requirement extraction.

The model is never allowed to invent a requirement. Every requirement here is
a sentence copied from the brief, with a source reference. The rules are
deliberately conservative: a missed requirement is better than a fabricated one.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from ingest import split_sentences

MODAL = re.compile(
    r"\b(must|shall|should|required?|requires?|need(?:s|ed)?\s+to|have\s+to|"
    r"expected\s+to|is\s+to|are\s+to)\b",
    re.I,
)
IMPERATIVE = re.compile(
    r"^(design|implement|submit|include|demonstrate|compare|validate|analyse|analyze|"
    r"produce|provide|write|build|test|calculate|derive|document|present|use|ensure|"
    r"show|explain|report|attach|develop|evaluate|investigate|measure|simulate|"
    r"identify|describe|discuss|outline|prepare|create|draw|model|verify)\b",
    re.I,
)
DELIVERABLE = re.compile(
    r"\b(report|diagram|code|program|script|test|tests|presentation|slides|dataset|"
    r"model|prototype|drawing|calculation|memo|portfolio|appendix|video|poster|"
    r"spreadsheet|notebook|logbook|reflection|proposal|specification|summary)\b",
    re.I,
)
CONSTRAINT = re.compile(
    r"\b(within|no\s+more\s+than|at\s+most|at\s+least|maximum|minimum|limit|limited|"
    r"budget|tolerance|material|standard|safety|size|weight|voltage|current|"
    r"temperature|pressure|flow|speed|duration|cost|pages?|words?|minutes?|hours?)\b",
    re.I,
)
ACCEPTANCE = re.compile(
    r"\b(pass|meets?|verify|demonstrate|criteri(?:on|a)|acceptable|expected|target|"
    r"performance|accuracy|tolerance|validate|test(?:ed|ing)?|show|prove|"
    r"successful|success)\b",
    re.I,
)
PROCESS = re.compile(
    r"\b(design|implement|analyse|analyze|compare|calculate|derive|simulate|iterate|"
    r"document|methodology|procedure|workflow|process|approach|evaluate|investigate)\b",
    re.I,
)
ADMIN = re.compile(
    r"\b(submit|upload|format|font|margin|page\s+count|word\s+count|deadline|"
    r"file\s*name|\.pdf|pdf|docx|canvas|moodle|turnitin|citation|reference|"
    r"plagiarism|late|grading|rubric)\b",
    re.I,
)
NUMERIC = re.compile(r"\b\d+(?:\.\d+)?\s?(?:%|mm|cm|m|km|kg|g|mg|s|ms|min|h|hr|"
                     r"hz|khz|mhz|v|mv|a|ma|w|kw|j|kj|n|nm|pa|kpa|mpa|°c|°f|"
                     r"l|ml|pages?|words?|hours?|minutes?|days?|weeks?)\b", re.I)
VAGUE = re.compile(
    r"\b(some|several|appropriate|as\s+needed|etc\.?|reasonable|fast|efficient|"
    r"good|adequate|sufficient|various|significant|proper|suitable|"
    r"user[- ]friendly|high[- ]quality|minimal|roughly|approximately|about)\b",
    re.I,
)
MISSING_MARKER = re.compile(
    r"\b(tbd|to\s+be\s+(?:decided|confirmed|provided)|will\s+be\s+provided|"
    r"see\s+attached|refer\s+to\s+the\s+attached|as\s+discussed)\b", re.I
)
CONTRADICTION_PAIR = re.compile(r"\b(not\s+required|optional|may\s+omit|need\s+not)\b", re.I)

_UNIT_WORDS = {
    "page", "pages", "word", "words", "hour", "hours", "minute", "minutes",
    "day", "days", "week", "weeks", "%", "percent", "mm", "cm", "m", "km",
    "kg", "g", "mg", "s", "ms", "min", "h", "hr", "hz", "khz", "mhz", "v",
    "mv", "a", "ma", "w", "kw", "j", "kj", "n", "nm", "pa", "kpa", "mpa",
    "°c", "°f", "l", "ml", "students", "people", "items", "tests", "sources",
}

_STOP = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "that", "this",
    "these", "those", "is", "are", "was", "were", "be", "been", "being", "to",
    "of", "in", "on", "at", "for", "from", "with", "by", "as", "into", "over",
    "after", "before", "while", "when", "where", "who", "whom", "whose", "it",
    "its", "their", "they", "them", "we", "our", "you", "your", "i", "my", "he",
    "she", "his", "her", "not", "no", "more", "most", "less", "least", "can",
    "could", "will", "would", "may", "might", "must", "should", "do", "does",
    "did", "have", "has", "had", "up", "out", "about", "across", "so", "such",
    "one", "two", "three", "each", "every", "any", "all", "some", "few", "many",
    "also", "using", "use", "used", "within", "include", "including", "submit",
    "provide", "ensure", "make", "sure", "brief", "assignment", "work",
    "produce", "prepare", "state", "identify", "check", "discuss", "calculate",
    "determine", "describe", "explain", "show", "create", "develop", "write",
    "present", "compare", "analyse", "analyze", "evaluate", "investigate",
    "measure", "simulate", "document", "draw", "model", "verify", "end",
    "must", "should", "required", "requirement", "task",
}

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}


def content_words(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}", (text or "").lower())
    return [w for w in words if w not in _STOP]


def _kind(text: str) -> str:
    if ADMIN.search(text):
        return "admin"
    if CONSTRAINT.search(text):
        return "constraint"
    if ACCEPTANCE.search(text):
        return "acceptance"
    if DELIVERABLE.search(text):
        return "deliverable"
    return "process"


def _score(text: str) -> int:
    score = 0
    if MODAL.search(text):
        score += 4
    if IMPERATIVE.search(text):
        score += 3
    if NUMERIC.search(text):
        score += 2
    if DELIVERABLE.search(text):
        score += 2
    if ACCEPTANCE.search(text):
        score += 2
    if ADMIN.search(text):
        score += 1
    if ":" in text and len(text) < 160:
        score += 1
    return score


def _status(text: str, kind: str) -> str:
    if MISSING_MARKER.search(text):
        return "missing"
    if VAGUE.search(text):
        return "vague"
    if kind in ("constraint", "acceptance"):
        numbers = re.findall(r"\b\d+(?:\.\d+)?\b", text)
        if numbers:
            # A bare number with no unit in a limit is not testable.
            bare = False
            for number in numbers:
                tail = text.split(number, 1)[1][:8].strip().lower().lstrip("-–— ")
                if not any(tail.startswith(u) for u in _UNIT_WORDS):
                    bare = True
                    break
            if bare:
                return "vague"
    return "clear"


def _confidence(text: str, score: int, status: str) -> float:
    value = 0.58 + min(score, 10) * 0.035
    if status == "clear":
        value += 0.04
    if status == "vague":
        value -= 0.08
    return round(max(0.4, min(0.96, value)), 2)


def _dedupe(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for item in items:
        key = re.sub(r"\W+", " ", item["text"].lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}


def _time_suffix(text: str) -> str:
    match = re.search(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b", text, re.I)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        suffix = (match.group(3) or "").lower()
        if suffix == "pm" and hour < 12:
            hour += 12
        if suffix == "am" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"T{hour:02d}:{minute:02d}"
    match = re.search(r"\b(\d{1,2})\s*(am|pm)\b", text, re.I)
    if match:
        hour = int(match.group(1))
        suffix = match.group(2).lower()
        if suffix == "pm" and hour < 12:
            hour += 12
        if suffix == "am" and hour == 12:
            hour = 0
        if 0 <= hour <= 23:
            return f"T{hour:02d}:00"
    return ""


def _format_date(day: date, text: str) -> str:
    return day.isoformat() + _time_suffix(text)


def _parse_date(text: str, today: date | None = None) -> str:
    today = today or date.today()
    weekday = re.search(
        r"\b(" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")\b", text, re.I
    )
    if weekday:
        target = WEEKDAYS[weekday.group(1).lower()]
        ahead = (target - today.weekday()) % 7
        if ahead == 0:
            ahead = 7
        return _format_date(today + timedelta(days=ahead), text)
    iso = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", text)
    if iso:
        try:
            return _format_date(
                date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3))), text
            )
        except ValueError:
            pass
    named = re.search(
        r"\b(" + "|".join(MONTHS) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))?\b",
        text,
        re.I,
    )
    if named:
        month = MONTHS[named.group(1).lower()]
        day = int(named.group(2))
        year = int(named.group(3)) if named.group(3) else today.year
        try:
            value = date(year, month, day)
            if not named.group(3) and value < today:
                value = date(year + 1, month, day)
            return _format_date(value, text)
        except ValueError:
            pass
    named_rev = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(MONTHS) + r")\.?(?:,?\s+(20\d{2}))?\b",
        text,
        re.I,
    )
    if named_rev:
        day = int(named_rev.group(1))
        month = MONTHS[named_rev.group(2).lower()]
        year = int(named_rev.group(3)) if named_rev.group(3) else today.year
        try:
            value = date(year, month, day)
            if not named_rev.group(3) and value < today:
                value = date(year + 1, month, day)
            return _format_date(value, text)
        except ValueError:
            pass
    slash = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", text)
    if slash:
        a, b, year = int(slash.group(1)), int(slash.group(2)), int(slash.group(3))
        day, month = (a, b) if a > 12 else (b, a)
        try:
            return _format_date(date(year, month, day), text)
        except ValueError:
            pass
    return ""


def extract_deadline(text: str) -> str:
    return _parse_date(text)


def _ambiguities(sentences: list[dict]) -> list[dict]:
    out: list[dict] = []
    for sent in sentences:
        for match in VAGUE.finditer(sent["text"]):
            out.append(
                {
                    "term": match.group(0),
                    "text": sent["text"],
                    "source_ref": sent["source_ref"],
                    "suggestion": f"Replace “{match.group(0)}” with a measurable condition.",
                }
            )
        if MISSING_MARKER.search(sent["text"]):
            out.append(
                {
                    "term": "missing information",
                    "text": sent["text"],
                    "source_ref": sent["source_ref"],
                    "suggestion": "Ask the instructor for the missing detail before drafting.",
                }
            )
    return out[:8]


def _contradictions(requirements: list[dict]) -> list[dict]:
    out: list[dict] = []
    for i, left in enumerate(requirements):
        for right in requirements[i + 1:]:
            left_words = set(content_words(left["text"]))
            right_words = set(content_words(right["text"]))
            shared = left_words & right_words
            if len(shared) < 2:
                continue
            if CONTRADICTION_PAIR.search(left["text"]) or CONTRADICTION_PAIR.search(right["text"]):
                modal_left = bool(re.search(r"\bmust\b", left["text"], re.I))
                modal_right = bool(re.search(r"\bmust\b", right["text"], re.I))
                if modal_left or modal_right:
                    out.append(
                        {
                            "text": "One sentence requires the item; another marks it optional.",
                            "a": left["text"],
                            "b": right["text"],
                            "a_ref": left["source_ref"],
                            "b_ref": right["source_ref"],
                        }
                    )
                    continue
            left_nums = re.findall(r"(\d+(?:\.\d+)?)\s?([a-zA-Z%°]+)", left["text"])
            right_nums = re.findall(r"(\d+(?:\.\d+)?)\s?([a-zA-Z%°]+)", right["text"])
            for ln, lu in left_nums:
                for rn, ru in right_nums:
                    if lu.lower() == ru.lower() and ln != rn and lu.lower() in _UNIT_WORDS:
                        out.append(
                            {
                                "text": f"Conflicting {lu} values: {ln} and {rn}.",
                                "a": left["text"],
                                "b": right["text"],
                                "a_ref": left["source_ref"],
                                "b_ref": right["source_ref"],
                            }
                        )
                        break
    # De-duplicate by the pair of source references.
    seen = set()
    unique = []
    for item in out:
        key = (item["a_ref"], item["b_ref"], item["text"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique[:5]


def extract(
    text: str,
    source: str = "pasted",
    limit: int = 8,
    today: date | None = None,
) -> dict:
    sentences = split_sentences(text, source)
    scored = []
    for sent in sentences:
        score = _score(sent["text"])
        if score >= 3:
            scored.append((score, sent))
    if len(scored) < 3:
        for sent in sentences:
            score = _score(sent["text"])
            if score >= 1 and all(sent is not s for _, s in scored):
                scored.append((score, sent))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["line"]))

    requirements = []
    for index, (score, sent) in enumerate(scored[:limit], start=1):
        kind = _kind(sent["text"])
        status = _status(sent["text"], kind)
        requirements.append(
            {
                "id": f"R{index}",
                "text": sent["text"],
                "kind": kind,
                "status": status,
                "evidence": sent["quote"],
                "source_ref": sent["source_ref"],
                "line": sent["line"],
                "confidence": _confidence(sent["text"], score, status),
            }
        )
    requirements = _dedupe(requirements)
    for index, req in enumerate(requirements, start=1):
        req["id"] = f"R{index}"

    deliverables = []
    for sent in sentences:
        for match in DELIVERABLE.finditer(sent["text"]):
            word = match.group(0).lower()
            if word not in deliverables:
                deliverables.append(word)

    deadline = _parse_date(text, today or date.today())
    return {
        "requirements": requirements,
        "ambiguities": _ambiguities(sentences),
        "contradictions": _contradictions(requirements),
        "deliverables": deliverables[:10],
        "deadline": deadline,
        "sentence_count": len(sentences),
        "source": source,
    }


def extract_deadline_from(text: str) -> str:  # pragma: no cover - compatibility
    return _parse_date(text, date.today())

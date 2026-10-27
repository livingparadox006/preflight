"""Local llama.cpp client.

One short call per preflight, one short call per coach message. The model writes
a prioritisation layer; deterministic code owns the evidence.
"""

from __future__ import annotations

import ast
import json
import os
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LLAMA_URL = os.environ.get("PREFLIGHT_LLAMA_URL", "http://127.0.0.1:8081").rstrip("/")
MODEL_LABEL = os.environ.get("PREFLIGHT_MODEL_LABEL", "Qwen2.5-1.5B-Instruct · Q4_K_M")

MODEL_CANDIDATES = [
    ("Qwen2.5-1.5B-Instruct-Q4_K_M.gguf", "Qwen2.5-1.5B-Instruct · Q4_K_M"),
    ("Qwen3-0.6B-Q4_K_M.gguf", "Qwen3-0.6B · Q4_K_M"),
    ("Qwen2.5-0.5B-Instruct-Q4_K_M.gguf", "Qwen2.5-0.5B-Instruct · Q4_K_M"),
]


class ModelUnavailable(RuntimeError):
    pass


class ModelTimeout(RuntimeError):
    pass


_MODEL_LOCK = threading.Lock()


def pick_model() -> tuple[Path | None, str]:
    requested = os.environ.get("PREFLIGHT_MODEL", "").strip()
    if requested:
        path = Path(requested)
        if not path.is_absolute():
            path = ROOT / path
        if path.exists():
            return path, path.stem
    for filename, label in MODEL_CANDIDATES:
        path = ROOT / "models" / filename
        if path.exists():
            return path, label
    return None, "no local model found"


class LlamaClient:
    def __init__(self, base_url: str = LLAMA_URL) -> None:
        self.base_url = base_url.rstrip("/")

    def health(self) -> bool:
        try:
            with urllib.request.urlopen(self.base_url + "/health", timeout=2) as response:
                return response.status == 200
        except Exception:
            return False

    def chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 140,
        temperature: float = 0.2,
        json_mode: bool = False,
        timeout: int = 120,
    ) -> str:
        payload = {
            "model": "preflight-local",
            "messages": messages,
            "temperature": temperature,
            "top_p": 0.9,
            "repeat_penalty": 1.08,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        request = urllib.request.Request(
            self.base_url + "/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with _MODEL_LOCK:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    data = json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise ModelUnavailable(f"llama-server returned {exc.code}: {detail}") from exc
        except TimeoutError as exc:
            raise ModelTimeout("The local model took too long to answer.") from exc
        except Exception as exc:
            raise ModelUnavailable(
                "The local model is not reachable. The report still works without it."
            ) from exc
        choices = data.get("choices") or []
        if not choices:
            raise ModelUnavailable("The local model returned no choices.")
        content = (choices[0].get("message") or {}).get("content") or ""
        return content.strip()


def repair_json(raw: str) -> dict | None:
    if not raw:
        return None
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    text = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    try:
        value = ast.literal_eval(text)
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    # Last resort: pull out the arrays and strings we asked for.
    actions_match = re.search(r'"actions"\s*:\s*\[(.*?)\]', text, re.S)
    risk_match = re.search(r'"risk"\s*:\s*"([^"]*)"', text, re.S)
    if actions_match or risk_match:
        actions = re.findall(r'"([^"]{6,140})"', actions_match.group(1)) if actions_match else []
        risk = risk_match.group(1) if risk_match else ""
        if actions:
            return {"actions": actions, "risk": risk}
    return None


def _evidence_block(report: dict, memory: list[dict], deadline: str = "") -> str:
    requirements = report.get("requirements") or []
    coverage = report.get("coverage") or {}
    gaps = coverage.get("gaps") or []
    lines = []
    if deadline:
        lines.append(f"Deadline: {deadline}")
    lines.append("Requirements:")
    for req in requirements[:6]:
        lines.append(
            f"{req.get('id')} [{req.get('kind')}/{req.get('status')}] "
            f"{req.get('text', '')[:150]}"
        )
    lines.append("Draft gaps:")
    for gap in gaps[:5]:
        lines.append(f"{gap.get('id')} {gap.get('status')}: {gap.get('text', '')[:120]}")
    if not gaps:
        lines.append("none")
    if report.get("contradictions"):
        for item in report["contradictions"][:2]:
            lines.append(f"Conflict: {item.get('text', '')[:120]}")
    if report.get("ambiguities"):
        for item in report["ambiguities"][:2]:
            lines.append(f"Vague: {item.get('term', '')} in {item.get('source_ref', '')}")
    if memory:
        lines.append("Memory:")
        for item in memory[:5]:
            lines.append(f"- {item.get('text', '')[:120]}")
    return "\n".join(lines)


PLAN_SYSTEM = (
    "You are Preflight Companion, a study coach for engineering students. "
    "You prioritise work; you never write the assignment, solve the problem, or "
    "produce submission-ready text. Use only the supplied evidence. "
    "Return one JSON object and nothing else: "
    '{"actions":["...","...","..."],"risk":"..."}. '
    "Exactly 3 actions, each at most 12 words, ordered First, Then, Before submit. "
    "One risk, at most 15 words. No markdown, no code, no answers."
)


def generate_plan(
    report: dict,
    memory: list[dict],
    deadline: str = "",
    client: LlamaClient | None = None,
    timeout: int = 85,
) -> dict | None:
    client = client or LlamaClient()
    prompt = (
        _evidence_block(report, memory, deadline)
        + "\n\nWrite the 3-action plan and one risk. Actions must be planning steps, "
        "not solutions."
    )
    raw = client.chat(
        [
            {"role": "system", "content": PLAN_SYSTEM},
            {"role": "user", "content": prompt[:4200]},
        ],
        max_tokens=130,
        temperature=0.2,
        json_mode=True,
        timeout=timeout,
    )
    plan = repair_json(raw)
    if not plan:
        return None
    actions = plan.get("actions")
    if isinstance(actions, dict):
        actions = list(actions.values())
    if not isinstance(actions, list):
        actions = []
    plan["actions"] = [str(a) for a in actions if str(a).strip()][:3]
    plan["risk"] = str(plan.get("risk", ""))
    if not plan["actions"]:
        return None
    return plan


CHAT_SYSTEM = (
    "You are Preflight Companion, a local study coach for engineering students. "
    "You help plan, prioritise, explain findings, and triage deadlines. "
    "You never write the assignment, solve the problem, generate submission-ready "
    "code or text, or give a full derivation. Use only the supplied context. "
    "The requirement statuses in the context are the source of truth: never call "
    "an item covered, complete, or satisfied unless its status is addressed. "
    "This is an assignment checklist, not an engineering correctness or safety review. "
    "Keep the reply under 90 words. Be direct and practical. "
    "Never call the work ready, complete, or submittable if COVERAGE shows any "
    "missing, partial, or contradicted item. When asked whether to submit, name "
    "the remaining gaps first. "
    "If the student asks you to do the work, never output a sample, template, "
    "heading, bullet list, draft, code, or submission-ready text. Refuse in one "
    "sentence and offer one planning step instead."
)


def chat_reply(
    context: str,
    history: list[dict],
    question: str,
    client: LlamaClient | None = None,
    timeout: int = 75,
) -> str:
    client = client or LlamaClient()
    messages = [{"role": "system", "content": CHAT_SYSTEM + "\n\nCONTEXT:\n" + context[:3000]}]
    for item in history[-8:]:
        role = "assistant" if item.get("role") == "coach" else "user"
        messages.append({"role": role, "content": str(item.get("content", ""))[:500]})
    messages.append({"role": "user", "content": question[:500]})
    raw = client.chat(messages, max_tokens=150, temperature=0.35, timeout=timeout)
    reply = re.sub(r"^\s*(coach|assistant)\s*:\s*", "", raw, flags=re.I).strip()
    reply = re.sub(r"<\|[^>]+\|>", "", reply).strip()
    return reply[:900]

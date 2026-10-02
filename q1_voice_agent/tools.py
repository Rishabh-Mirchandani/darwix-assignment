"""Agent tools: knowledge retrieval plus the business actions.

The tool schemas are written in OpenAI function-calling format, which Groq's
chat API accepts directly.

Design note on `search_knowledge_base`: the tool result handed back to the
model is deliberately *not* the raw retrieval payload. When the gate fails, the
model receives an instruction and no retrieved text at all. If low-confidence
passages were included "for context", the model would paraphrase them -- that
is precisely how grounded systems leak hallucinations. Withholding the text is
a stronger control than instructing the model to ignore it.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from shared.config import settings

RETRIEVAL_URL = "http://127.0.0.1:8001/search"
DB_PATH = settings.data_dir / "leads.db"


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": (
                "Look up a Niva Bupa health insurance fact. Call before stating "
                "ANY insurance fact. grounded=false means you must say you "
                "don't know."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query in insurance terms.",
                    },
                    "category": {
                        "type": ["string", "null"],
                        "enum": [
                            "product_plan", "coverage_rule", "eligibility_rule",
                            "claims_process", "faq", "service_policy",
                        ],
                        "description": "Optional filter.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_lead",
            "description": "Save the qualified lead. Call once near the end.",
            "parameters": {
                "type": "object",
                # Every field is nullable. No field is required, and a model
                # that has not learned a value will emit null rather than
                # omitting the key. Declaring a bare "string" makes the
                # provider reject the ENTIRE tool call on schema validation,
                # which silently loses the whole lead -- observed in a live
                # call where eight valid fields were discarded because three
                # unknown ones came back null.
                "properties": {
                    "caller_name": {"type": ["string", "null"]},
                    "cover_for": {
                        "type": ["string", "null"],
                        "description": "self | family | parents | other",
                    },
                    "members_count": {"type": ["integer", "null"]},
                    "eldest_age": {"type": ["integer", "null"]},
                    "city": {"type": ["string", "null"]},
                    "existing_conditions": {"type": ["string", "null"]},
                    "existing_cover": {"type": ["string", "null"]},
                    "budget_hint": {"type": ["string", "null"]},
                    "notes": {"type": ["string", "null"]},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_human_handoff",
            "description": (
                "Escalate to a human. Call immediately when a person is asked "
                "for, after a refusal they push back on, or on distress."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "enum": [
                            "caller_requested", "unanswered_question",
                            "account_specific", "complaint", "distress", "other",
                        ],
                    },
                    "context": {
                        "type": ["string", "null"],
                        "description": "One-line handover note.",
                    },
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_callback",
            "description": "Record a callback request.",
            "parameters": {
                "type": "object",
                "properties": {
                    "when": {"type": ["string", "null"]},
                    "phone_ok": {"type": ["boolean", "null"]},
                },
                "required": ["when"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Call state
# ---------------------------------------------------------------------------


@dataclass
class CallState:
    """Everything the call produces, for the transcript and the CRM summary."""

    call_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    market: str = "in"
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    lead: dict[str, Any] = field(default_factory=dict)
    handoff: dict[str, Any] | None = None
    callback: dict[str, Any] | None = None
    searches: list[dict[str, Any]] = field(default_factory=list)
    grounded_answers: int = 0
    refusals: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "started_at": self.started_at,
            "lead": self.lead,
            "handoff": self.handoff,
            "callback": self.callback,
            "kb_searches": len(self.searches),
            "grounded_answers": self.grounded_answers,
            "refusals": self.refusals,
            "grounding_rate": (
                round(self.grounded_answers / len(self.searches), 3)
                if self.searches else None
            ),
        }


# ---------------------------------------------------------------------------
# Lead storage
# ---------------------------------------------------------------------------


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                call_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                caller_name TEXT,
                cover_for TEXT,
                members_count INTEGER,
                eldest_age INTEGER,
                city TEXT,
                existing_conditions TEXT,
                existing_cover TEXT,
                budget_hint TEXT,
                notes TEXT,
                handoff_reason TEXT,
                callback_when TEXT,
                raw_json TEXT
            )
            """
        )
        conn.commit()


def persist_lead(state: CallState) -> None:
    init_db()
    lead = state.lead
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO leads (
                call_id, created_at, caller_name, cover_for, members_count,
                eldest_age, city, existing_conditions, existing_cover,
                budget_hint, notes, handoff_reason, callback_when, raw_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                state.call_id,
                state.started_at,
                lead.get("caller_name"),
                lead.get("cover_for"),
                lead.get("members_count"),
                lead.get("eldest_age"),
                lead.get("city"),
                lead.get("existing_conditions"),
                lead.get("existing_cover"),
                lead.get("budget_hint"),
                lead.get("notes"),
                (state.handoff or {}).get("reason"),
                (state.callback or {}).get("when"),
                json.dumps(state.summary()),
            ),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------


def _search(args: dict[str, Any], state: CallState) -> dict[str, Any]:
    # The market comes from call state, never from the model. Letting the
    # LLM choose which market to search would make a cross-market leak one
    # hallucinated argument away.
    query = args.get("query", "").strip()
    if not query:
        return {"grounded": False, "instruction": "Empty query; ask the caller to clarify."}

    t0 = time.perf_counter()
    try:
        resp = httpx.post(
            RETRIEVAL_URL,
            json={
                "query": query,
                "category": args.get("category"),
                "market": state.market,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        # A retrieval outage must fail closed, not open. If the agent cannot
        # verify a fact it must behave exactly as if the KB had no answer.
        state.refusals += 1
        state.searches.append(
            {"query": query, "grounded": False, "error": str(exc)[:200]}
        )
        return {
            "grounded": False,
            "instruction": (
                "The knowledge base is unreachable. Tell the caller you cannot "
                "check that right now and offer to have a colleague call back. "
                "Do not answer from memory."
            ),
        }

    elapsed = (time.perf_counter() - t0) * 1000
    state.searches.append({
        "query": query,
        "grounded": data["grounded"],
        "confidence": data["confidence"],
        "threshold": data["threshold"],
        "citations": [c["source_url"] for c in data["citations"]],
        "latency_ms": round(elapsed, 1),
    })

    if not data["grounded"]:
        state.refusals += 1
        # No retrieved text is returned. The model cannot paraphrase what it
        # was never given.
        return {
            "grounded": False,
            "instruction": (
                "No reliable answer. Tell the caller you don't have that and "
                "offer a colleague. Do NOT guess or hedge."
            ),
        }

    state.grounded_answers += 1
    # The retrieved text is capped before it enters the message history. The
    # full passage is kept in state.searches for the transcript, but the model
    # only needs enough to speak two sentences -- and every token here is
    # resent on every subsequent turn against an 8k/min ceiling.
    answer = data["answer"] or ""
    if len(answer) > 700:
        answer = answer[:700].rsplit(". ", 1)[0] + "."
    return {
        "grounded": True,
        "answer": answer,
        "instruction": "Answer using ONLY this text, in one or two spoken sentences.",
    }


def _save_lead(args: dict[str, Any], state: CallState) -> dict[str, Any]:
    state.lead.update({k: v for k, v in args.items() if v not in (None, "")})
    persist_lead(state)
    captured = sorted(state.lead.keys())
    return {
        "saved": True,
        "call_id": state.call_id,
        "fields_captured": captured,
        "instruction": (
            "Lead saved. Tell the caller in ONE sentence what happens next. "
            "Do not read the captured fields back to them."
        ),
    }


def _handoff(args: dict[str, Any], state: CallState) -> dict[str, Any]:
    state.handoff = {
        "reason": args.get("reason", "other"),
        "context": args.get("context", ""),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    persist_lead(state)
    return {
        "escalated": True,
        "instruction": (
            "Confirm warmly in ONE sentence that a colleague will call back. "
            "Do not continue qualifying."
        ),
    }


def _callback(args: dict[str, Any], state: CallState) -> dict[str, Any]:
    state.callback = {
        "when": args.get("when", ""),
        "phone_ok": args.get("phone_ok", True),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    persist_lead(state)
    return {
        "scheduled": True,
        "instruction": "Confirm the callback in ONE short sentence and close warmly.",
    }


_DISPATCH = {
    "search_knowledge_base": _search,
    "save_lead": _save_lead,
    "request_human_handoff": _handoff,
    "schedule_callback": _callback,
}


def execute(name: str, args: dict[str, Any], state: CallState) -> dict[str, Any]:
    handler = _DISPATCH.get(name)
    if handler is None:
        return {"error": f"unknown tool {name!r}"}
    return handler(args, state)


def retrieval_healthy() -> bool:
    try:
        r = httpx.get("http://127.0.0.1:8001/health", timeout=5.0)
        return r.status_code == 200 and r.json().get("status") == "ok"
    except Exception:
        return False

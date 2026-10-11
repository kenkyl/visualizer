"""trace.json → one self-contained HTML file.

The page builds everything from the trace embedded in it (PLAN.md §11), so the same template
can later load a dropped trace, or be served by live mode. Python's only jobs are to refuse a
broken trace, derive what the browser shouldn't decide (step confidence), and embed safely.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from .confidence import step_confidence
from .trace import problems

TEMPLATE = Path(__file__).with_name("viewer.html")


class RenderError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__(f"{len(problems)} problem(s)")
        self.problems = problems


def derive(trace: dict) -> dict:
    """Values computed from facts plus narrative, never stored in the trace itself."""
    exchanges = {x["id"]: x for x in trace.get("exchanges") or []}
    return {"steps": {s["id"]: {"confidence": step_confidence(s, exchanges)} for s in trace["narrative"]["steps"]}}


def embed_json(data) -> str:
    """JSON that can't end the <script> element it sits in, whatever strings the trace holds."""
    text = json.dumps(data, ensure_ascii=False, indent=1)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render(trace: dict) -> str:
    found = problems(trace)
    if found:
        raise RenderError(found)
    header = trace["narrative"].get("header") or {}
    title = header.get("page_title") or f"{trace['meta'].get('target', 'authtrace')} auth flow"
    page = TEMPLATE.read_text(encoding="utf-8")
    page = page.replace("{{TITLE}}", html.escape(title))
    return page.replace("{{PAYLOAD}}", embed_json({"trace": trace, "derived": derive(trace)}))

"""MockProvider: deterministic stand-in for Gemini (tests, and demos without a key).

- Text documents are "read" with the same regex parser the fallback uses.
- Images and scanned PDFs can't be read without a model, except the generated demo
  samples: their expected output is registered by file hash (see seed/sample_docs.py).
- Tests can script raw responses per task and inspect every prompt sent."""

import hashlib
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

from app.config import get_settings
from app.domain.quote_text import parse_text
from app.domain.reply_text import parse_reply_text
from app.llm.provider import LLMRequest

VISION_REGISTRY = "samples/mock_vision.json"


def _between(text: str, start: str, end: str) -> str:
    m = re.search(re.escape(start) + r"(.*?)" + re.escape(end), text, re.S)
    return m.group(1) if m else ""


def register_vision_fixture(data: bytes, parsed_json: str) -> None:
    path = Path(get_settings().storage_dir) / VISION_REGISTRY
    path.parent.mkdir(parents=True, exist_ok=True)
    reg = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    reg[hashlib.sha256(data).hexdigest()] = json.loads(parsed_json)
    path.write_text(json.dumps(reg, indent=1), encoding="utf-8")


def _vision_lookup(data: bytes) -> str | None:
    path = Path(get_settings().storage_dir) / VISION_REGISTRY
    if not path.exists():
        return None
    hit = json.loads(path.read_text(encoding="utf-8")).get(hashlib.sha256(data).hexdigest())
    return json.dumps(hit) if hit is not None else None


class MockProvider:
    name = "mock"

    def __init__(self) -> None:
        self.calls: list[LLMRequest] = []
        self.scripted: dict[str, list[str]] = defaultdict(list)

    def script(self, task: str, *responses: str) -> None:
        """Next calls for `task` return these raw strings, in order."""
        self.scripted[task].extend(responses)

    def reset(self) -> None:
        self.calls.clear()
        self.scripted.clear()

    def complete(self, req: LLMRequest) -> str:
        self.calls.append(req)
        if self.scripted.get(req.task):
            return self.scripted[req.task].pop(0)
        if req.task == "parse_quote":
            for a in req.attachments:
                return _vision_lookup(a.data) or "{}"
            today = date.fromisoformat(_between(req.prompt, "Today: ", "\n").strip())
            brands = [
                b.strip()
                for b in _between(req.prompt, "Known brands: ", "\n").split(",")
                if b.strip()
            ]
            doc = _between(req.prompt, "<document>", "</document>")
            return parse_text(doc, today, brands).model_dump_json(exclude_none=True)
        if req.task == "parse_reply":
            return parse_reply_text(_between(req.prompt, "<reply>", "</reply>")).model_dump_json(
                exclude_none=True
            )
        if req.task == "write_message":
            return _between(req.prompt, "Suggested wording: ", "\n").strip()
        raise NotImplementedError(f"mock has no behaviour for task {req.task!r}")


_mock = MockProvider()


def get_mock() -> MockProvider:
    return _mock

"""LLMProvider interface + the one way the app calls it.

The model only turns documents/text into a strict schema, or phrases a message
around numbers it was given. It never decides a number, and its output is always
validated: invalid -> one retry -> None (the caller then uses deterministic code)."""

import logging
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, ValidationError

log = logging.getLogger("llm")


@dataclass(frozen=True)
class Attachment:
    data: bytes
    mime_type: str  # application/pdf, image/png, image/jpeg, image/webp


@dataclass
class LLMRequest:
    task: str  # parse_quote | parse_reply | write_message
    system: str
    prompt: str
    attachments: list[Attachment] = field(default_factory=list)
    json_schema: dict[str, object] | None = None


class LLMProvider(Protocol):
    name: str

    def complete(self, req: LLMRequest) -> str:
        """Raw model text (JSON when json_schema is set). May raise on transport errors."""
        ...


def structured[T: BaseModel](provider: LLMProvider, req: LLMRequest, schema: type[T]) -> T | None:
    """Ask for JSON matching `schema`. Invalid or failed -> one retry -> None."""
    req.json_schema = schema.model_json_schema()
    for attempt in (1, 2):
        try:
            raw = provider.complete(req)
            return schema.model_validate_json(_strip_fences(raw))
        except ValidationError as e:
            log.warning(
                "%s: invalid %s output (attempt %d): %s",
                provider.name,
                req.task,
                attempt,
                e.error_count(),
            )
        except Exception as e:  # network, quota, provider bugs: never crash the app
            log.warning(
                "%s: %s failed (attempt %d): %s", provider.name, req.task, attempt, type(e).__name__
            )
    return None


def _strip_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()

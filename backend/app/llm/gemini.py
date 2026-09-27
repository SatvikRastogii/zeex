"""Gemini via the documented REST endpoint (models/{model}:generateContent).

Stdlib HTTP only, so no SDK dependency. The key goes in the x-goog-api-key header
(never the URL, which ends up in logs). Model IDs come from .env.
Not exercised by tests (no network in tests): the mock provider stands in."""

import base64
import json
import urllib.request

from app.llm.provider import LLMRequest

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT_SECONDS = 60


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, parse_model: str, write_model: str) -> None:
        if not api_key or not parse_model or not write_model:
            raise ValueError(
                "GEMINI_API_KEY, GEMINI_PARSE_MODEL and GEMINI_WRITE_MODEL must be set"
            )
        self.api_key = api_key
        self.models = {
            "parse_quote": parse_model,
            "parse_reply": parse_model,
            "write_message": write_model,
        }

    def complete(self, req: LLMRequest) -> str:
        prompt = req.prompt
        if req.json_schema is not None:
            prompt += "\n\nReturn only JSON that matches this JSON Schema:\n" + json.dumps(
                req.json_schema
            )
        parts: list[dict[str, object]] = [{"text": prompt}]
        for a in req.attachments:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": a.mime_type,
                        "data": base64.b64encode(a.data).decode(),
                    }
                }
            )
        body: dict[str, object] = {
            "system_instruction": {"parts": [{"text": req.system}]},
            "contents": [{"role": "user", "parts": parts}],
        }
        if req.json_schema is not None:
            body["generationConfig"] = {"response_mime_type": "application/json", "temperature": 0}
        http_req = urllib.request.Request(  # noqa: S310  fixed https endpoint
            ENDPOINT.format(model=self.models.get(req.task, self.models["parse_quote"])),
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
            method="POST",
        )
        with urllib.request.urlopen(http_req, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310
            data = json.loads(resp.read())
        return str(data["candidates"][0]["content"]["parts"][0]["text"])

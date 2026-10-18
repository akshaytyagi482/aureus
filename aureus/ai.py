"""Thin Gemini client shared by the AI features.

Every feature goes through `generate_json` or `generate_text`, so error handling,
the free-tier masking and the audit trail live in one place. The engines stay
deterministic: AI output is always a suggestion that the app validates (an HSN
code must exist in the GST directory, a notice field must parse) before it is shown.
"""

import json
import re

from . import config


class AIUnavailable(Exception):
    """No Gemini key configured."""


class AIError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def enabled() -> bool:
    return bool(config.GEMINI_API_KEY)


def status() -> dict:
    return {"enabled": enabled(), "provider": "gemini" if enabled() else None, "tier": config.AI_TIER,
            "model": config.GEMINI_MODEL if enabled() else None,
            "free_tier_notice": ("Free Gemini tier: Google may use what is sent to improve its products. "
                                 "Client names and GSTINs are masked in questions; uploaded notices are sent as they are, "
                                 "so use anonymised or test documents.") if config.AI_TIER == "free" else None}


_client = None


def _get_client():
    global _client
    if not enabled():
        raise AIUnavailable("AI features need a Gemini API key on the server (GEMINI_API_KEY)")
    if _client is None:
        from google import genai
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def _call(model: str, contents: list, system: str, schema: dict | None, max_tokens: int, json_mode: bool = False) -> str:
    """One generate_content call; returns the response text. Patched in tests."""
    from google.genai import errors, types
    cfg = types.GenerateContentConfig(system_instruction=system, temperature=0.1, max_output_tokens=max_tokens,
                                      response_mime_type="application/json" if schema or json_mode else "text/plain",
                                      response_json_schema=schema)
    try:
        response = _get_client().models.generate_content(model=model, contents=contents, config=cfg)
    except errors.APIError as exc:
        if exc.code == 429:
            raise AIError("Gemini usage limit reached (the free tier allows only a few requests a minute). "
                          "Try again in a minute.", 429)
        if exc.code in (401, 403):
            raise AIError("Gemini rejected the API key. Check GEMINI_API_KEY.", 503)
        raise AIError(f"Gemini error {exc.code}: {exc.status or 'request failed'}")
    except AIUnavailable:
        raise
    except Exception as exc:  # network, timeouts
        raise AIError(f"Could not reach Gemini: {exc.__class__.__name__}")
    finish = response.candidates[0].finish_reason.name if response.candidates and response.candidates[0].finish_reason else ""
    if finish and finish not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
        raise AIError(f"Gemini stopped early ({finish}). Try a shorter request.")
    if not response.text:
        raise AIError("Gemini returned an empty answer")
    return response.text


def generate_json(prompt: str, schema: dict | None, system: str, *, lite: bool = False, files: list[tuple[bytes, str]] = (),
                  max_tokens: int = 8192, model: str | None = None) -> dict:
    """JSON answer; with schema=None the model returns free-form JSON (used for the long policy-reader output)."""
    contents = _contents(prompt, files)
    model = model or (config.GEMINI_LITE_MODEL if lite else config.GEMINI_MODEL)
    text = _call(model, contents, system, schema, max_tokens, json_mode=True)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise AIError("Gemini returned malformed JSON")


def generate_text(prompt: str, system: str, *, lite: bool = False, max_tokens: int = 4096) -> str:
    return _call(config.GEMINI_LITE_MODEL if lite else config.GEMINI_MODEL, [prompt], system, None, max_tokens).strip()


def _contents(prompt: str, files) -> list:
    if not files:
        return [prompt]
    from google.genai import types
    return [*(types.Part.from_bytes(data=data, mime_type=mime) for data, mime in files), prompt]


# ---------------------------------------------------------------- masking for the free tier

GSTIN_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")


class Masker:
    """Swap client names and GSTINs for placeholders before text leaves the server, and swap them back after.

    Only active on the free tier; on a paid key the text is sent unchanged.
    """

    def __init__(self, active: bool | None = None):
        self.active = config.AI_TIER == "free" if active is None else active
        self.forward: dict[str, str] = {}

    def name(self, value: str, kind: str = "Supplier") -> str:
        if not self.active or not value:
            return value
        if value not in self.forward:
            self.forward[value] = f"{kind} {sum(1 for t in self.forward.values() if t.startswith(kind)) + 1}"
        return self.forward[value]

    def text(self, value: str) -> str:
        if not self.active:
            return value
        for real, token in sorted(self.forward.items(), key=lambda kv: -len(kv[0])):
            value = value.replace(real, token)
        return GSTIN_RE.sub(lambda m: self.name(m.group(0), "GSTIN"), value)

    def unmask(self, value: str) -> str:
        if not self.active:
            return value
        for real, token in sorted(self.forward.items(), key=lambda kv: -len(kv[1])):
            value = re.sub(rf"\b{re.escape(token)}\b", real, value)
        return value

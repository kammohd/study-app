"""Gemini client. Ported from the Apps Script callGemini: try the fast model, fall back to lite on 429."""
import httpx
from fastapi import HTTPException

from .config import Settings

MODELS = ("gemini-flash-latest", "gemini-flash-lite-latest")
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class Gemini:
    def __init__(self, settings: Settings):
        self.key = settings.gemini_api_key.strip()   # a key piped in from a clipboard carries a newline

    async def ask(self, prompt: str, *, temperature: float | None = None, json: bool = False,
                  thinking_budget: int | None = None, models: tuple[str, ...] = MODELS) -> str:
        """Return the model's text. Raises 503 if the key is missing or every model fails."""
        if not self.key:
            raise HTTPException(503, "AI is not configured on the server")
        last = ""
        async with httpx.AsyncClient(timeout=60) as c:
            for model in models:
                for extras in (True, False):   # a 400 with extra settings gets one plain retry
                    cfg = {}
                    if extras:
                        if temperature is not None:
                            cfg["temperature"] = temperature
                        if json:
                            cfg["responseMimeType"] = "application/json"
                        if thinking_budget is not None:
                            cfg["thinkingConfig"] = {"thinkingBudget": thinking_budget}
                    body = {"contents": [{"parts": [{"text": prompt}]}]}
                    if cfg:
                        body["generationConfig"] = cfg
                    r = await c.post(URL.format(model=model), params={"key": self.key}, json=body)
                    if r.status_code == 429:
                        break                    # rate limited: next model
                    text = _first_text(r.json()) if r.content else ""
                    if text:
                        return text
                    last = r.text[:200]
                    if r.status_code != 400 or not cfg:
                        break
        raise HTTPException(503, f"AI is busy, try again in a minute ({last[:80]})" if last
                            else "AI is rate limited, try again in a minute")


def _first_text(data: dict) -> str:
    parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
    texts = [p["text"] for p in parts if p.get("text") and not p.get("thought")]
    return texts[0] if texts else ""

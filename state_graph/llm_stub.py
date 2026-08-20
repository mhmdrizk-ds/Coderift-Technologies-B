from __future__ import annotations

import os
from typing import Optional


class LlmClient:
    def __init__(self, model: str = "gemini-1.5-flash"):
        self.model = model
        self._live_available = bool(os.environ.get("GOOGLE_API_KEY"))

    def complete(self, prompt: str, *, fallback: str) -> str:
        if not self._live_available:
            return fallback
        try:
            return self._call_live_model(prompt)
        except Exception:
            return fallback

    def _call_live_model(self, prompt: str) -> str:
        import google.generativeai as genai  

        genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
        model = genai.GenerativeModel(self.model)
        response = model.generate_content(prompt)
        return response.text
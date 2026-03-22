"""core/gemini_client.py — Unified LLM Agent (Google + OpenRouter)"""
from __future__ import annotations
import os, time
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
try:
    from google import genai
    from google.genai import types
except Exception:
    genai = None; types = None
try:
    from openai import OpenAI
except Exception:
    OpenAI = None
from core.logger import get_logger
logger = get_logger("Agent")

class Agent:
    def __init__(self, provider="google", model="gemini-2.5-flash", api_key=None,
                 max_retries=3, retry_delay=2, reasoning_enabled=True):
        normalized = (provider or "google").strip().lower()
        if normalized in {"openapi","openai"}: normalized = "openrouter"
        self.provider = normalized; self.model = model
        self.max_retries = max_retries; self.retry_delay = retry_delay
        self.reasoning_enabled = reasoning_enabled
        self.last_assistant_message = None; self.last_reasoning_details = None
        env_key = "OPENROUTER_API_KEY" if self.provider == "openrouter" else "GEMINI_API_KEY"
        key = api_key or os.getenv(env_key,"")
        self.client = None
        if self.provider == "openrouter":
            if key and OpenAI:
                self.client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)
        else:
            if key and genai:
                self.client = genai.Client(api_key=key)

    def _ask_google(self, prompt, max_tokens, temperature):
        if not self.client or not types: return ""
        response = self.client.models.generate_content(
            model=self.model, contents=prompt,
            config=types.GenerateContentConfig(max_output_tokens=max_tokens, temperature=temperature))
        return response.text or ""

    def _ask_openrouter(self, prompt, max_tokens, temperature):
        if not self.client: return ""
        response = self.client.chat.completions.create(
            model=self.model, messages=[{"role":"user","content":prompt}],
            max_tokens=max_tokens, temperature=temperature,
            extra_body={"reasoning":{"enabled":bool(self.reasoning_enabled)}})
        message = response.choices[0].message
        content = getattr(message,"content","") or ""
        self.last_reasoning_details = getattr(message,"reasoning_details",None)
        self.last_assistant_message = {"role":"assistant","content":content}
        return content

    def ask_with_messages(self, messages, max_tokens=8192, temperature=0.7):
        if self.provider != "openrouter" or not self.client:
            prompt = "\n".join(m.get("content","") for m in messages if m.get("role")=="user")
            content = self.ask(prompt, max_tokens=max_tokens, temperature=temperature)
            return {"role":"assistant","content":content}
        response = self.client.chat.completions.create(
            model=self.model, messages=messages, max_tokens=max_tokens, temperature=temperature,
            extra_body={"reasoning":{"enabled":bool(self.reasoning_enabled)}})
        msg = response.choices[0].message
        assistant = {"role":"assistant","content":getattr(msg,"content","") or ""}
        rd = getattr(msg,"reasoning_details",None)
        if rd is not None: assistant["reasoning_details"] = rd
        self.last_assistant_message = assistant; self.last_reasoning_details = rd
        return assistant

    def ask(self, prompt, max_tokens=8192, temperature=0.7):
        """Retry with exponential backoff + jitter."""
        import random
        for attempt in range(1, self.max_retries + 1):
            try:
                if self.provider == "openrouter":
                    return self._ask_openrouter(prompt, max_tokens, temperature)
                return self._ask_google(prompt, max_tokens, temperature)
            except Exception as exc:
                is_rate_limit = any(k in str(exc).lower() for k in ("429","rate","quota","limit"))
                logger.warning("LLM attempt %s/%s [%s] %s: %s",
                               attempt, self.max_retries, self.provider,
                               "rate-limit" if is_rate_limit else "error", exc)
                if attempt < self.max_retries:
                    # Exponential backoff: 2^attempt * base + jitter
                    sleep_t = (self.retry_delay * (2 ** (attempt - 1))
                               + random.uniform(0, 1))
                    if is_rate_limit:
                        sleep_t = max(sleep_t, 15.0)  # respect rate limits
                    time.sleep(min(sleep_t, 60))
                else:
                    logger.error("LLM failed after %s attempts [%s]",
                                 self.max_retries, self.provider)
        return ""

class GeminiClient(Agent):
    def __init__(self):
        super().__init__(model="gemini-2.5-flash")

"""
LLM client — supports local llama.cpp, Groq Cloud, and xAI/Grok APIs.

Local:  http://127.0.0.1:8080 (OpenAI-compatible, no auth needed)
Groq:   https://api.groq.com/openai (needs GROQ_API_KEY env var, FREE tier)
xAI:    https://api.x.ai/v1 (needs XAI_API_KEY env var, PAYG, no free tier)

All speak OpenAI /v1/chat/completions — same protocol, different backends.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

DEFAULT_HOST = os.environ.get("FINCH_LLM_HOST", "http://127.0.0.1:8080")
DEFAULT_MODEL = os.environ.get("FINCH_LLM_MODEL", "finch-brain")
DEFAULT_TIMEOUT = float(os.environ.get("FINCH_LLM_TIMEOUT", "45"))


class LocalLLM:
    def __init__(
        self,
        host: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 180,
        timeout: Optional[float] = None,
        api_key: Optional[str] = None,
    ):
        cfg_host = host
        self.host = (cfg_host or DEFAULT_HOST).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
        self.api_key = api_key or os.environ.get("GROQ_API_KEY") or os.environ.get("XAI_API_KEY")
        self._available: Optional[bool] = None
        self._is_groq = bool(self.api_key) or "groq" in self.host
        self._is_xai = "x.ai" in self.host or bool(os.environ.get("XAI_API_KEY"))

    def configure_from_dict(self, cfg: Dict[str, Any]) -> None:
        if not cfg:
            return
        provider = (cfg.get("provider") or "").lower()
        host = cfg.get("host") or cfg.get("base_url")

        # Auto-detect: env vars ALWAYS override config provider
        groq_key = os.environ.get("GROQ_API_KEY", "")
        xai_key = os.environ.get("XAI_API_KEY", "")
        if groq_key:
            provider = "groq"
        elif xai_key:
            provider = "xai"

        # Detect Groq
        if provider == "groq" or (host and "groq" in str(host)) or groq_key:
            self._is_groq = True
            self.host = "https://api.groq.com/openai"
            self.api_key = cfg.get("api_key") or groq_key
            if not cfg.get("model"):
                self.model = "llama-3.1-8b-instant"
        # Detect xAI/Grok
        elif provider in ("xai", "grok") or (host and "x.ai" in str(host)) or xai_key:
            self._is_xai = True
            self._is_groq = False
            self.host = "https://api.x.ai/v1"
            self.api_key = cfg.get("api_key") or xai_key
            if not cfg.get("model"):
                self.model = "grok-3-mini"
        elif provider in ("llama.cpp", "llamacpp", "local", "openai_compat"):
            if host and "11434" in str(host):
                host = DEFAULT_HOST
            self.host = str(host or DEFAULT_HOST).rstrip("/")
        elif host:
            if "11434" in str(host):
                host = DEFAULT_HOST
            self.host = str(host).rstrip("/")

        if cfg.get("model"):
            model = cfg["model"]
            # If Groq is active, override local model names with a Groq-compatible model
            if self._is_groq and str(model).lower() in ("finch-brain", "local", ""):
                model = "llama-3.1-8b-instant"
            elif not self._is_groq and any(x in str(model).lower() for x in ("llama3", "8b", "7b", "13b")):
                model = DEFAULT_MODEL
            self.model = model
        if cfg.get("temperature") is not None:
            self.temperature = float(cfg["temperature"])
        if cfg.get("max_tokens") is not None:
            self.max_tokens = int(cfg["max_tokens"])

    def _auth_headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json", "User-Agent": "Finch/1.0"}
        if self.api_key and (self._is_groq or self._is_xai):
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def health(self) -> bool:
        if self._is_groq or self._is_xai:
            try:
                req = urllib.request.Request(
                    f"{self.host}/v1/models",
                    method="GET",
                    headers=self._auth_headers(),
                )
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    self._available = resp.status == 200
                    return self._available
            except Exception:
                self._available = False
                return False
        try:
            req = urllib.request.Request(f"{self.host}/health", method="GET")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
                ok = resp.status == 200 and ("ok" in body.lower() or body.strip() == "")
                self._available = ok
                return ok
        except Exception:
            try:
                req = urllib.request.Request(f"{self.host}/v1/models", method="GET")
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    self._available = resp.status == 200
                    return self._available
            except Exception:
                self._available = False
                return False

    @property
    def available(self) -> bool:
        if self._available is None:
            return self.health()
        return self._available

    def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        stop: Optional[List[str]] = None,
    ) -> Optional[str]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "temperature": temperature if temperature is not None else self.temperature,
            "stream": False,
        }
        if stop:
            payload["stop"] = stop

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.host}/v1/chat/completions",
            data=data,
            headers=self._auth_headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw)
            choices = parsed.get("choices") or []
            if not choices:
                return None
            content = (choices[0].get("message") or {}).get("content")
            if not content:
                return None
            text = str(content).strip()
            self._available = True
            return text or None
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError) as e:
            self._available = False
            print(f"[LLM] chat failed: {e}")
            return None
        except Exception as e:
            self._available = False
            print(f"[LLM] unexpected error: {e}")
            return None


_process_llm: Optional[LocalLLM] = None

def get_llm(config: Optional[Dict[str, Any]] = None) -> LocalLLM:
    global _process_llm
    if _process_llm is None:
        _process_llm = LocalLLM()
        if config:
            _process_llm.configure_from_dict(config.get("llm") or config)
    return _process_llm

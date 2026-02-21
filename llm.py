"""LLM interface — calls any OpenAI-compatible local server (LM Studio, Ollama, etc.)."""

import os
import re
import requests


def _strip_reasoning(text: str) -> str:
    """Remove chain-of-thought tags emitted by reasoning models (QwQ, DeepSeek-R1, etc.).

    Strips <think>...</think> / <thinking>...</thinking> blocks entirely,
    then unwraps <answer>...</answer> tags keeping only the content inside.
    """
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<answer>\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*</answer>", "", text, flags=re.IGNORECASE)
    return text.strip()


def list_models(base_url: str = "http://localhost:1234/v1", timeout: int = 5) -> list[str]:
    """Return model IDs currently loaded in the server, or [] on failure."""
    try:
        resp = requests.get(f"{base_url}/models", timeout=timeout)
        resp.raise_for_status()
        return [m["id"] for m in resp.json().get("data", [])]
    except Exception:
        return []


def call(
    prompt: str,
    system: str,
    model: str,
    temperature: float,
    base_url: str = "http://localhost:1234/v1",
    timeout: int = 300,
) -> str:
    """Send a chat prompt to the server and return the response text.

    If model is empty, auto-detects whichever model is currently loaded in
    LM Studio. This happens fresh on every call, so switching models in LM
    Studio mid-run is picked up automatically.
    """
    if not model:
        loaded = list_models(base_url)
        if not loaded:
            raise RuntimeError(
                f"No model loaded at {base_url}. "
                "Load a model in LM Studio and enable the local server."
            )
        model = loaded[0]

    url = f"{base_url}/chat/completions"

    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("LM_STUDIO_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
    except requests.Timeout:
        raise TimeoutError(
            f"LM Studio did not respond within {timeout}s. "
            "Try a smaller model, or raise llm_timeout_seconds in config.json."
        )
    except requests.HTTPError:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise RuntimeError(f"LM Studio {resp.status_code}: {detail}")
    raw = resp.json()["choices"][0]["message"]["content"]
    return _strip_reasoning(raw)

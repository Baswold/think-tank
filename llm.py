"""LLM interface — calls any OpenAI-compatible local server (LM Studio, Ollama, etc.)."""

import os
import requests


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

    Works with LM Studio (default), Ollama (/v1 mode), or any OpenAI-compatible
    endpoint. Set LM_STUDIO_API_KEY if you have token auth enabled in LM Studio
    settings; otherwise no key is needed.
    """
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

    resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()

import requests

from src.config import OLLAMA_GENERATE_URL, OLLAMA_MODEL


def ask_ollama(prompt: str):
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }

    try:
        response = requests.post(OLLAMA_GENERATE_URL, json=payload, timeout=180)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Failed to call Ollama at {OLLAMA_GENERATE_URL}. "
            "Check that Ollama is running on the host and the model is pulled."
        ) from exc

    return response.json()

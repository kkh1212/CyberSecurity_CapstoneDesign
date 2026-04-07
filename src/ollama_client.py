import requests

from src.config import OLLAMA_GENERATE_URL, OLLAMA_MODEL


def ask_ollama(prompt: str):
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0,
            "top_p": 0.9,
            "repeat_penalty": 1.05,
        },
    }

    try:
        response = requests.post(OLLAMA_GENERATE_URL, json=payload, timeout=180)
        response.raise_for_status()
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        response_text = ""
        response_error = ""
        if exc.response is not None:
            try:
                response_text = exc.response.text.strip()
            except Exception:
                response_text = ""
            try:
                response_error = exc.response.json().get("error", "")
            except Exception:
                response_error = ""

        details = f"HTTP {status_code}"
        if response_text:
            details += f" | response={response_text[:300]}"

        if exc.response is not None and exc.response.status_code == 404:
            if response_error and "model" in response_error.lower() and "not found" in response_error.lower():
                raise RuntimeError(
                    f"Failed to call Ollama at {OLLAMA_GENERATE_URL}. {details}. "
                    f"The Ollama server is reachable, but the configured model `{OLLAMA_MODEL}` is not installed. "
                    f"Pull it first with `ollama pull {OLLAMA_MODEL}` or switch `OLLAMA_MODEL` to an installed model."
                ) from exc
            raise RuntimeError(
                f"Failed to call Ollama at {OLLAMA_GENERATE_URL}. {details}. "
                "A server is reachable on that host/port, but it does not expose the Ollama native endpoint "
                "`/api/generate`. Verify that Ollama itself is running there by checking `/api/tags`."
            ) from exc

        raise RuntimeError(
            f"Failed to call Ollama at {OLLAMA_GENERATE_URL}. {details}. "
            "Check that Ollama is running on the host and the model is pulled."
        ) from exc
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Failed to call Ollama at {OLLAMA_GENERATE_URL}. "
            "Check that Ollama is running on the host and the model is pulled."
        ) from exc

    return response.json()

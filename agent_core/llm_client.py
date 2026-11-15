import json
import re
import requests
from pathlib import Path
from config import OLLAMA_URL, ROLE_MODELS, DEFAULT_MODEL


class TaskStoppedException(Exception):
    pass


def get_available_models() -> list[str]:
    tags_url = OLLAMA_URL.replace("/api/chat", "/api/tags")
    try:
        resp = requests.get(tags_url, timeout=5)
        resp.raise_for_status()
        models = [m["name"] for m in resp.json().get("models", [])]
        return sorted(models) if models else [DEFAULT_MODEL]
    except Exception:
        return [DEFAULT_MODEL]


def call_agent(
    role: str,
    system_prompt: str,
    user_payload: str,
    stop_flag: Path = None,
    num_ctx: int = 8192,
    model_override: str = None
) -> str:
    if stop_flag and stop_flag.exists():
        raise TaskStoppedException("Остановлено пользователем")

    model = model_override or ROLE_MODELS.get(role, DEFAULT_MODEL)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_payload}
        ],
        "stream": True,
        "keep_alive": "30m",
        "options": {
            "num_ctx": num_ctx,
            "temperature": 0.1 if role in ["critic", "worker_code", "architect"] else 0.3
        }
    }

    chunks = []
    with requests.post(OLLAMA_URL, json=payload, stream=True, timeout=(10, 60)) as resp:
        resp.raise_for_status()
        for raw_line in resp.iter_lines():
            if stop_flag and stop_flag.exists():
                resp.close()
                raise TaskStoppedException("Остановлено пользователем во время генерации")
            if not raw_line:
                continue
            if isinstance(raw_line, bytes):
                raw_line = raw_line.decode("utf-8", errors="ignore")
            line = raw_line.strip()
            if line.startswith("data:"):
                line = line[5:].strip()
            if not line or line == "[DONE]":
                continue
            try:
                data = json.loads(line)
                if "error" in data:
                    raise RuntimeError(f"Ollama error: {data['error']}")
                chunks.append(data.get("message", {}).get("content", ""))
            except json.JSONDecodeError:
                continue

    raw_text = "".join(chunks)
    if not raw_text.strip():
        raise RuntimeError(f"Модель {model} вернула пустой ответ.")

    clean_text = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
    return clean_text

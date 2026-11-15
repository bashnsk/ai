import base64
import json
import re
import requests
from pathlib import Path
from config import OLLAMA_URL, ROLE_CONFIGS, DEFAULT_MODEL, DEFAULT_NUM_CTX


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


def extract_text_from_image(image_path: Path) -> str:
    b64_img = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    payload = {
        "model": "glm-ocr:latest",
        "messages": [{"role": "user", "content": "Распознай и выпиши весь текст и данные с этого изображения без комментариев.", "images": [b64_img]}],
        "stream": False,
        "options": {"num_gpu": 0, "num_thread": 10}
    }
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "").strip()
    except Exception as e:
        return f"[Ошибка OCR для {image_path.name}: {e}]"


def call_agent(
    role: str,
    system_prompt: str,
    user_payload: str,
    stop_flag: Path = None,
    num_ctx: int = None,
    model_override: str = None
) -> str:
    if stop_flag and stop_flag.exists():
        raise TaskStoppedException("Остановлено пользователем")

    rcfg = ROLE_CONFIGS.get(role, {"model": DEFAULT_MODEL, "num_gpu": -1, "num_ctx": DEFAULT_NUM_CTX})
    if not model_override or model_override == "AUTO":
        model = rcfg["model"]
        num_gpu = rcfg.get("num_gpu", -1)
        ctx_size = num_ctx or rcfg.get("num_ctx", DEFAULT_NUM_CTX)
    else:
        model = model_override
        num_gpu = -1
        ctx_size = num_ctx or DEFAULT_NUM_CTX

    options = {
        "num_ctx": ctx_size,
        "num_gpu": num_gpu,
        "temperature": 0.1 if role in ["critic", "worker_code", "architect"] else 0.3
    }
    if num_gpu == 0:
        options["num_thread"] = 10

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_payload}
        ],
        "stream": True,
        "keep_alive": "60m",
        "options": options
    }

    chunks = []
    with requests.post(OLLAMA_URL, json=payload, stream=True, timeout=(10, 90)) as resp:
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

    return re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()

import base64
import json
import re
import requests
from pathlib import Path
from config import OLLAMA_URL, DEFAULT_MODEL, DEFAULT_NUM_CTX
from core.model_profiler import build_model_profile


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
    profile = build_model_profile()
    vision_model = profile.get("vision")
    if not vision_model:
        return f"[В системе не найдена VLM/OCR модель для чтения {image_path.name}]"

    b64_img = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    prompt = "Подробно опиши, что изображено на этом фото/скриншоте, и если на нем есть текст или цифры — полностью перепиши их."
    payload = {
        "model": vision_model,
        "messages": [{"role": "user", "content": prompt, "images": [b64_img]}],
        "stream": False,
        "options": {"num_gpu": -1}
    }
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
        resp.raise_for_status()
        text = resp.json().get("message", {}).get("content", "").strip()
        return f"[{vision_model}]: {text}" if text else f"[{vision_model}]: Текст на изображении не обнаружен."
    except Exception as e:
        return f"[Ошибка анализа изображения {image_path.name} ({vision_model}): {e}]"


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

    profile = build_model_profile()
    rcfg = profile.get(role, {"model": DEFAULT_MODEL, "num_ctx": DEFAULT_NUM_CTX})

    if not model_override or model_override == "AUTO":
        model = rcfg["model"]
        ctx_size = num_ctx or rcfg.get("num_ctx", DEFAULT_NUM_CTX)
    else:
        model = model_override
        ctx_size = num_ctx or DEFAULT_NUM_CTX

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_payload}
        ],
        "stream": True,
        "keep_alive": "60m",
        "options": {
            "num_ctx": ctx_size,
            "num_gpu": -1,
            "temperature": 0.1 if role in ["critic", "worker_code", "architect"] else 0.3
        }
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

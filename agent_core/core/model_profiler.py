import json
import requests
from config import OLLAMA_URL, MEMORY_DIR, DEFAULT_MODEL, DEFAULT_NUM_CTX

PROFILE_PATH = MEMORY_DIR / "model_profile.json"


def _fetch_ollama_models() -> list[dict]:
    tags_url = OLLAMA_URL.replace("/api/chat", "/api/tags")
    try:
        resp = requests.get(tags_url, timeout=5)
        resp.raise_for_status()
        return resp.json().get("models", [])
    except Exception:
        return []


def _pick_best(models: list[dict], keywords: list[str], prefer_small: bool = False, exclude: list[str] = None) -> str:
    exclude = exclude or ["embed", "nomic", "bge", "ocr", "llava", "minicpm-v", "vision"]
    candidates = [m for m in models if not any(ex in m["name"].lower() for ex in exclude)]
    if not candidates:
        return DEFAULT_MODEL

    matched = [m for m in candidates if any(kw in m["name"].lower() for kw in keywords)]
    pool = matched if matched else candidates
    pool.sort(key=lambda m: m.get("size", 0), reverse=not prefer_small)
    return pool[0]["name"]


def _pick_vision(models: list[dict]) -> str:
    vision_kws = ["vl", "llava", "minicpm-v", "glm-ocr", "ocr", "vision", "moondream"]
    matched = [m for m in models if any(kw in m["name"].lower() for kw in vision_kws)]
    if not matched:
        return ""
    # Предпочитаем полноценные VLM (vl/llava/minicpm-v), если есть, иначе OCR
    vlm = [m for m in matched if any(k in m["name"].lower() for k in ["vl", "llava", "minicpm-v"])]
    pool = vlm if vlm else matched
    pool.sort(key=lambda m: m.get("size", 0), reverse=True)
    return pool[0]["name"]


def build_model_profile(force_refresh: bool = False) -> dict:
    if PROFILE_PATH.exists() and not force_refresh:
        try:
            return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    models = _fetch_ollama_models()
    if not models:
        return {
            "architect": {"model": DEFAULT_MODEL, "num_ctx": 16384},
            "critic": {"model": DEFAULT_MODEL, "num_ctx": 16384},
            "worker_code": {"model": DEFAULT_MODEL, "num_ctx": DEFAULT_NUM_CTX},
            "worker_general": {"model": DEFAULT_MODEL, "num_ctx": 16384},
            "optimizer": {"model": DEFAULT_MODEL, "num_ctx": 8192},
            "vision": ""
        }

    reasoner = _pick_best(models, ["deepseek-r1", "qwq", "qwen2.5:14b", "qwen2.5:32b", "llama3.1"])
    coder = _pick_best(models, ["coder", "codellama", "deepseek-coder", "starcoder"])
    general = _pick_best(models, ["qwen2.5:", "llama3.1", "gemma", "mistral"])
    fast = _pick_best(models, ["9b", "8b", "7b", "qwen3.5", "llama3.1"], prefer_small=True)
    vision = _pick_vision(models)

    profile = {
        "architect": {"model": reasoner, "num_ctx": 16384},
        "critic": {"model": reasoner, "num_ctx": 16384},
        "worker_code": {"model": coder, "num_ctx": DEFAULT_NUM_CTX},
        "worker_general": {"model": general, "num_ctx": 16384},
        "optimizer": {"model": fast, "num_ctx": 8192},
        "vision": vision
    }
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    return profile

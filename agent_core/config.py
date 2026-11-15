from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = BASE_DIR / "system"
MEMORY_DIR = BASE_DIR / "memory"
WORKSPACE_DIR = BASE_DIR / "workspace"

OLLAMA_URL = "http://localhost:11434/api/chat"

DEFAULT_MODEL = "qwen2.5-coder:14b"
DEFAULT_NUM_CTX = 32768

ROLE_CONFIGS = {
    "architect": {"model": "qwen2.5-coder:14b", "num_gpu": -1, "num_ctx": 16384},
    "critic": {"model": "qwen2.5-coder:14b", "num_gpu": -1, "num_ctx": 16384},
    "worker_code": {"model": "qwen2.5-coder:14b", "num_gpu": -1, "num_ctx": 32768},
    "worker_general": {"model": "qwen2.5:14b", "num_gpu": -1, "num_ctx": 16384},
    "curator": {"model": "qwen3.5:9b", "num_gpu": 0, "num_ctx": 8192},
    "optimizer": {"model": "qwen3.5:9b", "num_gpu": 0, "num_ctx": 8192}
}

ROLE_MODELS = {k: v["model"] for k, v in ROLE_CONFIGS.items()}

MAX_ATTEMPTS_PER_NODE = 3

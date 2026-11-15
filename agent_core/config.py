from pathlib import Path

BASE_DIR = Path("/home/sid/RAID/_AI/github/agent_core")
SYSTEM_DIR = BASE_DIR / "system"
MEMORY_DIR = BASE_DIR / "memory"
WORKSPACE_DIR = BASE_DIR / "workspace"

OLLAMA_URL = "http://localhost:11434/api/chat"

DEFAULT_MODEL = "qwen2.5-coder:14b"

ROLE_MODELS = {
    "architect": DEFAULT_MODEL,
    "critic": DEFAULT_MODEL,
    "worker_code": DEFAULT_MODEL,
    "worker_general": DEFAULT_MODEL,
    "curator": DEFAULT_MODEL,
    "optimizer": DEFAULT_MODEL
}

MAX_ATTEMPTS_PER_NODE = 3

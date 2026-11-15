import json
import shutil
import threading
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from config import WORKSPACE_DIR, BASE_DIR, MEMORY_DIR, DEFAULT_MODEL
from core.model_profiler import build_model_profile
from llm_client import get_available_models
from orchestrator import Orchestrator

app = FastAPI(title="Agent Core Orchestrator")
static_dir = BASE_DIR / "web" / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.on_event("startup")
def on_startup():
    build_model_profile(force_refresh=True)
    if not WORKSPACE_DIR.exists():
        return
    for task_dir in WORKSPACE_DIR.iterdir():
        tree_path = task_dir / "tree.json"
        if task_dir.is_dir() and tree_path.exists():
            tree = json.loads(tree_path.read_text(encoding="utf-8"))
            if tree.get("status") in ["IN_PROGRESS", "STOPPING..."]:
                tree["status"] = "STOPPED"
                tree_path.write_text(json.dumps(tree, indent=2, ensure_ascii=False), encoding="utf-8")


class RerunRequest(BaseModel):
    model: Optional[str] = None


def _spawn_task(goal: str, model: str, src_uploads_dir=None) -> str:
    base_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    task_id = base_id
    counter = 1
    while (WORKSPACE_DIR / task_id).exists():
        task_id = f"{base_id}_{counter}"
        counter += 1

    task_dir = WORKSPACE_DIR / task_id
    (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (task_dir / "nodes").mkdir(parents=True, exist_ok=True)
    uploads_dir = task_dir / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []
    if src_uploads_dir and src_uploads_dir.exists():
        for f in sorted(src_uploads_dir.iterdir()):
            if f.is_file():
                shutil.copy2(f, uploads_dir / f.name)
                saved_files.append(f.name)

    (task_dir / "goal.md").write_text(goal, encoding="utf-8")
    initial_tree = {"task_id": task_id, "goal": goal, "model": model, "uploads": saved_files, "status": "IN_PROGRESS", "nodes": {}}
    (task_dir / "tree.json").write_text(json.dumps(initial_tree, indent=2, ensure_ascii=False), encoding="utf-8")

    threading.Thread(target=Orchestrator(task_id).run_loop, daemon=True).start()
    return task_id


@app.get("/")
def index():
    return FileResponse(static_dir / "index.html")


@app.get("/api/models")
def list_models():
    return {"models": get_available_models(), "default": "AUTO"}


@app.post("/api/tasks")
async def create_task(goal: str = Form(...), model: Optional[str] = Form("AUTO"), files: Optional[List[UploadFile]] = File(None)):
    task_id = _spawn_task(goal, model or DEFAULT_MODEL)
    uploads_dir = WORKSPACE_DIR / task_id / "uploads"
    saved = []
    for f in (files or []):
        if f.filename:
            (uploads_dir / f.filename).write_bytes(await f.read())
            saved.append(f.filename)
    if saved:
        tree_path = WORKSPACE_DIR / task_id / "tree.json"
        tree = json.loads(tree_path.read_text(encoding="utf-8"))
        tree["uploads"] = saved
        tree_path.write_text(json.dumps(tree, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"task_id": task_id, "status": "STARTED"}


@app.post("/api/tasks/{task_id}/rerun")
def rerun_task(task_id: str, req: RerunRequest):
    old_dir = WORKSPACE_DIR / task_id
    if not old_dir.exists():
        return {"error": "Task not found"}
    old_tree = json.loads((old_dir / "tree.json").read_text(encoding="utf-8"))
    new_id = _spawn_task(old_tree.get("goal", ""), req.model or old_tree.get("model") or "AUTO", old_dir / "uploads")
    return {"task_id": new_id, "status": "STARTED"}


@app.post("/api/tasks/{task_id}/stop")
def stop_task(task_id: str):
    task_dir = WORKSPACE_DIR / task_id
    if not task_dir.exists():
        return {"error": "Task not found"}
    (task_dir / "stop.flag").write_text("STOP", encoding="utf-8")
    return {"task_id": task_id, "status": "STOPPING"}


@app.delete("/api/tasks/{task_id}")
def delete_single_task(task_id: str):
    task_dir = WORKSPACE_DIR / task_id
    if task_dir.exists() and task_dir.is_dir():
        (task_dir / "stop.flag").write_text("STOP", encoding="utf-8")
        shutil.rmtree(task_dir, ignore_errors=True)
    return {"deleted": task_id}


@app.delete("/api/tasks")
def clear_old_tasks():
    if not WORKSPACE_DIR.exists():
        return {"deleted": 0}
    for task_dir in WORKSPACE_DIR.iterdir():
        if task_dir.is_dir():
            (task_dir / "stop.flag").write_text("STOP", encoding="utf-8")
            shutil.rmtree(task_dir, ignore_errors=True)
    return {"status": "CLEARED"}


@app.get("/api/tasks")
def list_tasks():
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for d in sorted(WORKSPACE_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        tree_path = d / "tree.json"
        goal, status = d.name, "UNKNOWN"
        if tree_path.exists():
            tree = json.loads(tree_path.read_text(encoding="utf-8"))
            goal = tree.get("goal", d.name)
            status = tree.get("status", "UNKNOWN")
        items.append({"id": d.name, "goal": goal, "status": status})
    return {"tasks": items}


@app.get("/api/tasks/{task_id}")
def get_task_status(task_id: str):
    task_dir = WORKSPACE_DIR / task_id
    if not task_dir.exists():
        return {"error": "Not found"}
    tree = json.loads((task_dir / "tree.json").read_text(encoding="utf-8"))
    log_file = task_dir / "events.log"
    logs = log_file.read_text(encoding="utf-8") if log_file.exists() else ""

    node_details = {}
    nodes_dir = task_dir / "nodes"
    if nodes_dir.exists():
        for nd in nodes_dir.iterdir():
            if nd.is_dir():
                res_f, crit_f = nd / "result.md", nd / "critique.md"
                node_details[nd.name] = {
                    "result": res_f.read_text(encoding="utf-8") if res_f.exists() else "",
                    "critique": crit_f.read_text(encoding="utf-8") if crit_f.exists() else ""
                }

    artifacts_dir = task_dir / "artifacts"
    final_report = ""
    if artifacts_dir.exists():
        report_file = artifacts_dir / "final_report.md"
        if not report_file.exists():
            candidates = sorted(
                [f for f in artifacts_dir.iterdir() if f.is_file() and f.suffix.lower() in {".md", ".txt"}],
                key=lambda f: f.stat().st_mtime,
                reverse=True
            )
            if candidates:
                report_file = candidates
        if report_file.exists():
            final_report = report_file.read_text(encoding="utf-8", errors="ignore")
    return {"tree": tree, "logs": logs, "node_details": node_details, "final_report": final_report}


@app.get("/api/memory")
def get_memory():
    index_path = MEMORY_DIR / "index.json"
    skills = []
    if index_path.exists():
        data = json.loads(index_path.read_text(encoding="utf-8"))
        for s in data.get("skills", []):
            sf = MEMORY_DIR / "skills" / f"{s['skill_id']}.md"
            skills.append({**s, "recipe": sf.read_text(encoding="utf-8") if sf.exists() else ""})

    anti_dir = MEMORY_DIR / "anti_patterns"
    anti = []
    if anti_dir.exists():
        for f in sorted(anti_dir.iterdir(), reverse=True):
            if f.is_file():
                anti.append({"id": f.name, "content": f.read_text(encoding="utf-8")})
    return {"skills": skills, "anti_patterns": anti}


@app.delete("/api/memory/skills/{skill_id}")
def delete_skill(skill_id: str):
    index_path = MEMORY_DIR / "index.json"
    if index_path.exists():
        data = json.loads(index_path.read_text(encoding="utf-8"))
        data["skills"] = [s for s in data.get("skills", []) if s["skill_id"] != skill_id]
        index_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    (MEMORY_DIR / "skills" / f"{skill_id}.md").unlink(missing_ok=True)
    return {"deleted": skill_id}


@app.delete("/api/memory/anti_patterns/{file_name}")
def delete_anti_pattern(file_name: str):
    (MEMORY_DIR / "anti_patterns" / file_name).unlink(missing_ok=True)
    return {"deleted": file_name}

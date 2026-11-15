import json
import uuid
import shutil
import threading
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from config import WORKSPACE_DIR, BASE_DIR
from orchestrator import Orchestrator

app = FastAPI(title="Agent Core Orchestrator")

static_dir = BASE_DIR / "web" / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.on_event("startup")
def cleanup_stale_tasks():
    if not WORKSPACE_DIR.exists():
        return
    for task_dir in WORKSPACE_DIR.iterdir():
        tree_path = task_dir / "tree.json"
        if task_dir.is_dir() and tree_path.exists():
            tree = json.loads(tree_path.read_text(encoding="utf-8"))
            if tree.get("status") in ["IN_PROGRESS", "STOPPING..."]:
                tree["status"] = "INTERRUPTED"
                tree_path.write_text(json.dumps(tree, indent=2, ensure_ascii=False), encoding="utf-8")


class TaskRequest(BaseModel):
    goal: str


@app.get("/")
def index():
    return FileResponse(static_dir / "index.html")


@app.post("/api/tasks")
def create_task(req: TaskRequest):
    task_id = f"task_{uuid.uuid4().hex[:6]}"
    task_dir = WORKSPACE_DIR / task_id
    (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (task_dir / "nodes").mkdir(parents=True, exist_ok=True)
    (task_dir / "goal.md").write_text(req.goal, encoding="utf-8")

    initial_tree = {"task_id": task_id, "goal": req.goal, "status": "IN_PROGRESS", "nodes": {}}
    (task_dir / "tree.json").write_text(json.dumps(initial_tree, indent=2, ensure_ascii=False), encoding="utf-8")

    orch = Orchestrator(task_id)
    threading.Thread(target=orch.run_loop, daemon=True).start()
    return {"task_id": task_id, "status": "STARTED"}


@app.post("/api/tasks/{task_id}/stop")
def stop_task(task_id: str):
    task_dir = WORKSPACE_DIR / task_id
    if not task_dir.exists():
        return {"error": "Task not found"}

    (task_dir / "stop.flag").write_text("STOP", encoding="utf-8")
    tree_path = task_dir / "tree.json"
    if tree_path.exists():
        tree = json.loads(tree_path.read_text(encoding="utf-8"))
        if tree.get("status") == "IN_PROGRESS":
            tree["status"] = "STOPPING..."
            tree_path.write_text(json.dumps(tree, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"task_id": task_id, "status": "STOPPING"}


@app.delete("/api/tasks")
def clear_old_tasks():
    if not WORKSPACE_DIR.exists():
        return {"deleted": 0}
    deleted = 0
    for task_dir in WORKSPACE_DIR.iterdir():
        tree_path = task_dir / "tree.json"
        if task_dir.is_dir() and tree_path.exists():
            tree = json.loads(tree_path.read_text(encoding="utf-8"))
            if tree.get("status") not in ["IN_PROGRESS", "STOPPING..."]:
                shutil.rmtree(task_dir, ignore_errors=True)
                deleted += 1
    return {"deleted": deleted}


@app.get("/api/tasks")
def list_tasks():
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    tasks = [d.name for d in WORKSPACE_DIR.iterdir() if d.is_dir()]
    return {"tasks": sorted(tasks, reverse=True)}


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
                res_f = nd / "result.md"
                crit_f = nd / "critique.md"
                node_details[nd.name] = {
                    "result": res_f.read_text(encoding="utf-8") if res_f.exists() else "",
                    "critique": crit_f.read_text(encoding="utf-8") if crit_f.exists() else ""
                }

    artifacts_dir = task_dir / "artifacts"
    artifacts = [f.name for f in artifacts_dir.iterdir() if f.is_file()] if artifacts_dir.exists() else []
    return {"tree": tree, "logs": logs, "node_details": node_details, "artifacts": artifacts}

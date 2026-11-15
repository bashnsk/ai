import json
import threading
from datetime import datetime
from config import WORKSPACE_DIR
from core.context_builder import evaluate_node_complexity
from core.memory_manager import MemoryManager
from core.node_runner import NodeRunner
from core.planner import TaskPlanner
from llm_client import TaskStoppedException


class Orchestrator:
    def __init__(self, task_id: str):
        self.task_dir = WORKSPACE_DIR / task_id
        self.tree_path = self.task_dir / "tree.json"
        self.artifacts_dir = self.task_dir / "artifacts"
        self.nodes_dir = self.task_dir / "nodes"
        self.stop_flag = self.task_dir / "stop.flag"
        self.memory = MemoryManager()
        self.runner = NodeRunner(self)
        self.planner = TaskPlanner(self)
        self.failed_count = 0

    def load_tree(self) -> dict:
        return json.loads(self.tree_path.read_text(encoding="utf-8"))

    def save_tree(self, tree: dict):
        self.tree_path.write_text(json.dumps(tree, indent=2, ensure_ascii=False), encoding="utf-8")

    def log_event(self, node_id: str, message: str):
        ts = datetime.now().strftime("%H:%M:%S")
        with open(self.task_dir / "events.log", "a", encoding="utf-8") as f:
            f.write(f"[{ts}] [{node_id}] {message}\n")

    def get_ready_node(self, tree: dict):
        nodes = tree["nodes"]
        for nid, node in nodes.items():
            if node.get("type") == "ATOMIC" and node.get("status") in ["PENDING", "RETRY"]:
                if all(nodes[d].get("status") == "DONE" for d in node.get("dependencies", []) if d in nodes):
                    return nid, node
        return None, None

    def run_loop(self):
        self.log_event("SYSTEM", "Оркестратор запущен (семантический флаг неделимости is_primitive + смена инструментов).")
        try:
            self.planner.plan_initial()
            while True:
                if self.stop_flag.exists():
                    raise TaskStoppedException("Остановлено пользователем")
                tree = self.load_tree()
                node_id, node = self.get_ready_node(tree)
                if not node:
                    atomic = [n for n in tree["nodes"].values() if n.get("type") == "ATOMIC"]
                    if atomic and all(n.get("status") == "DONE" for n in atomic):
                        tree["status"] = "COMPLETED"
                        self.save_tree(tree)
                        self.log_event("SYSTEM", "Все узлы выполнены!")
                        threading.Thread(target=lambda: self.log_event("OPTIMIZER", self.memory.learn_from_task(tree, self.nodes_dir)), daemon=True).start()
                        break
                    failed = [k for k, v in tree["nodes"].items() if v.get("type") == "ATOMIC" and v.get("status") == "FAILED"]
                    if failed:
                        fid = next(iter(failed))
                        self.failed_count += 1
                        crit = (self.nodes_dir / fid / "critique.md").read_text(encoding="utf-8") if (self.nodes_dir / fid / "critique.md").exists() else "Ошибка"
                        self.memory.save_anti_pattern(self.task_dir.name, self.failed_count, tree["nodes"][fid]["title"], crit, tree.get("model"))
                        self.planner.handle_failed_node(tree, fid, crit)
                        continue

                if not node.get("is_primitive"):
                    sub_nodes = evaluate_node_complexity(node["title"], tree.get("model"), tree["goal"])
                    if sub_nodes and self.planner.insert_sub_nodes(tree, node_id, sub_nodes):
                        continue
                    node["is_primitive"] = True
                    self.save_tree(tree)

                self.runner.execute(node_id, node, tree)
        except TaskStoppedException:
            tree = self.load_tree()
            tree["status"] = "STOPPED"
            self.save_tree(tree)
            self.log_event("SYSTEM", "Задача остановлена пользователем.")
        except Exception as e:
            tree = self.load_tree()
            tree["status"] = "FAILED"
            self.save_tree(tree)
            self.log_event("ERROR", f"Системный сбой: {e}")

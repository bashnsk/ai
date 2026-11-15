import json
import time
import re
from datetime import datetime
from config import WORKSPACE_DIR, SYSTEM_DIR
from llm_client import call_agent, TaskStoppedException
from core.memory_manager import MemoryManager
from core.node_runner import NodeRunner

MAX_REPLANS = 2


class Orchestrator:
    def __init__(self, task_id: str):
        self.task_dir = WORKSPACE_DIR / task_id
        self.tree_path = self.task_dir / "tree.json"
        self.artifacts_dir = self.task_dir / "artifacts"
        self.nodes_dir = self.task_dir / "nodes"
        self.stop_flag = self.task_dir / "stop.flag"
        self.memory = MemoryManager()
        self.runner = NodeRunner(self)

    def load_tree(self) -> dict:
        return json.loads(self.tree_path.read_text(encoding="utf-8"))

    def save_tree(self, tree: dict):
        self.tree_path.write_text(json.dumps(tree, indent=2, ensure_ascii=False), encoding="utf-8")

    def log_event(self, node_id: str, message: str):
        ts = datetime.now().strftime("%H:%M:%S")
        with open(self.task_dir / "events.log", "a", encoding="utf-8") as f:
            f.write(f"[{ts}] [{node_id}] {message}\n")

    def plan_task(self, failure_report: str = ""):
        tree = self.load_tree()
        if tree.get("nodes") and not failure_report:
            return

        self.log_event("ARCHITECT", "Пересборка плана..." if failure_report else "Построение графа задач...")
        arch_sys = (SYSTEM_DIR / "architect.md").read_text(encoding="utf-8")
        prompt = f"Цель:\n{tree['goal']}\n\nНавыки в памяти:\n{self.memory.get_summary()}"
        if failure_report:
            prompt += f"\n\nПровал прошлого плана:\n{failure_report}\nПострой обходной план."

        raw_plan = call_agent("architect", arch_sys, prompt, stop_flag=self.stop_flag)
        match = re.search(r"```json\n(.*?)```", raw_plan, flags=re.DOTALL)
        json_str = match.group(1).strip() if match else raw_plan.strip()
        try:
            tree["nodes"] = json.loads(json_str)["nodes"]
            tree["status"] = "IN_PROGRESS"
        except Exception as e:
            self.log_event("ARCHITECT", f"Ошибка JSON ({e}), создаю единый узел.")
            tree["nodes"] = {"node_1": {"title": tree["goal"], "type": "ATOMIC", "status": "PENDING", "attempts": 0, "dependencies": []}}
        self.save_tree(tree)
        self.log_event("ARCHITECT", f"Создано узлов: {len(tree['nodes'])}.")

    def get_ready_node(self, tree: dict):
        nodes = tree["nodes"]
        for nid, node in nodes.items():
            if node["type"] == "ATOMIC" and node["status"] in ["PENDING", "RETRY"]:
                if all(nodes[d]["status"] == "DONE" for d in node.get("dependencies", [])):
                    return nid, node
        return None, None

    def run_loop(self):
        self.log_event("SYSTEM", "Оркестратор запущен.")
        replan_count = 0
        try:
            self.plan_task()
            while True:
                if self.stop_flag.exists():
                    raise TaskStoppedException("Остановлено пользователем")

                tree = self.load_tree()
                node_id, node = self.get_ready_node(tree)
                if not node:
                    statuses = [n["status"] for n in tree["nodes"].values() if n["type"] == "ATOMIC"]
                    if all(s == "DONE" for s in statuses):
                        tree["status"] = "COMPLETED"
                        self.save_tree(tree)
                        self.log_event("SYSTEM", "Все задачи выполнены!")
                        msg = self.memory.learn_from_task(tree, self.nodes_dir)
                        self.log_event("OPTIMIZER", msg)
                        break
                    elif any(s == "FAILED" for s in statuses) and replan_count < MAX_REPLANS:
                        replan_count += 1
                        fid = next(k for k, v in tree["nodes"].items() if v["status"] == "FAILED")
                        crit_f = self.nodes_dir / fid / "critique.md"
                        crit = crit_f.read_text(encoding="utf-8") if crit_f.exists() else "Ошибка"
                        self.memory.save_anti_pattern(self.task_dir.name, replan_count, tree["nodes"][fid]["title"], crit)
                        self.plan_task(failure_report=f"Шаг '{tree['nodes'][fid]['title']}' провалился:\n{crit}")
                        continue
                    tree["status"] = "FAILED"
                    self.save_tree(tree)
                    self.log_event("SYSTEM", "Задача остановлена после исчерпания попыток.")
                    break

                self.runner.execute(node_id, node, tree)
                time.sleep(1)

        except TaskStoppedException:
            tree = self.load_tree()
            tree["status"] = "STOPPED"
            for n in tree.get("nodes", {}).values():
                if n["status"] == "IN_PROGRESS":
                    n["status"] = "STOPPED"
            self.save_tree(tree)
            self.log_event("SYSTEM", "Задача мгновенно остановлена пользователем.")
        except Exception as e:
            tree = self.load_tree()
            tree["status"] = "FAILED"
            self.save_tree(tree)
            self.log_event("ERROR", f"Системный сбой: {e}")

import json
import re
import threading
from datetime import datetime
from config import WORKSPACE_DIR, SYSTEM_DIR
from llm_client import call_agent, TaskStoppedException
from core.memory_manager import MemoryManager
from core.node_runner import NodeRunner, IMAGE_EXTS


class Orchestrator:
    def __init__(self, task_id: str):
        self.task_dir = WORKSPACE_DIR / task_id
        self.tree_path = self.task_dir / "tree.json"
        self.artifacts_dir = self.task_dir / "artifacts"
        self.nodes_dir = self.task_dir / "nodes"
        self.stop_flag = self.task_dir / "stop.flag"
        self.memory = MemoryManager()
        self.runner = NodeRunner(self)
        self.failed_strategies = []

    def load_tree(self) -> dict:
        return json.loads(self.tree_path.read_text(encoding="utf-8"))

    def save_tree(self, tree: dict):
        self.tree_path.write_text(json.dumps(tree, indent=2, ensure_ascii=False), encoding="utf-8")

    def log_event(self, node_id: str, message: str):
        ts = datetime.now().strftime("%H:%M:%S")
        with open(self.task_dir / "events.log", "a", encoding="utf-8") as f:
            f.write(f"[{ts}] [{node_id}] {message}\n")

    def _normalize_nodes(self, raw_nodes: dict, fallback_goal: str) -> dict:
        if not isinstance(raw_nodes, dict) or not raw_nodes:
            return {"node_1": {"title": f"{fallback_goal} (сохранить в artifacts/final_report.md)", "type": "ATOMIC", "status": "PENDING", "attempts": 0, "dependencies": []}}
        clean = {}
        for nid, node in raw_nodes.items():
            if not isinstance(node, dict):
                continue
            deps = [d for d in node.get("dependencies", []) if isinstance(d, str) and d in raw_nodes]
            clean[str(nid)] = {
                "title": str(node.get("title", fallback_goal)),
                "type": "ATOMIC",
                "status": "PENDING",
                "attempts": 0,
                "dependencies": deps
            }
        return clean or {"node_1": {"title": f"{fallback_goal} (сохранить в artifacts/final_report.md)", "type": "ATOMIC", "status": "PENDING", "attempts": 0, "dependencies": []}}

    def _try_fast_path(self, tree: dict) -> bool:
        uploads_dir = self.task_dir / "uploads"
        files = [f for f in uploads_dir.iterdir() if f.is_file()] if uploads_dir.exists() else []
        if files:
            total_size = sum(f.stat().st_size for f in files)
            exts = {f.suffix.lower() for f in files}
            if exts.issubset(IMAGE_EXTS):
                title = f"Проанализировать изображения ({', '.join(f.name for f in files)}) по запросу '{tree['goal']}' и сохранить ответ в artifacts/final_report.md"
                tree["nodes"] = self._normalize_nodes({"node_1": {"title": title}}, tree["goal"])
                self.log_event("ROUTER", "Fast-Path (Изображения): создан 1 узел.")
                return True
            if total_size < 16000:
                title = f"Изучить содержимое файлов ({', '.join(f.name for f in files)}), ответить на вопрос '{tree['goal']}' и сохранить в artifacts/final_report.md"
                tree["nodes"] = self._normalize_nodes({"node_1": {"title": title}}, tree["goal"])
                self.log_event("ROUTER", f"Fast-Path (Файлы {total_size} Б): создан 1 узел.")
                return True
        return False

    def plan_task(self, is_replan: bool = False):
        tree = self.load_tree()
        if tree.get("nodes") and not is_replan:
            return
        if not is_replan and self._try_fast_path(tree):
            self.save_tree(tree)
            return

        selected_model = tree.get("model")
        self.log_event("ARCHITECT", f"Стратегия #{len(self.failed_strategies) + 1}: построение графа...")
        arch_sys = (SYSTEM_DIR / "architect.md").read_text(encoding="utf-8")
        prompt = f"Цель:\n{tree['goal']}\n\nНавыки в памяти:\n{self.memory.get_summary()}"
        if self.failed_strategies:
            history = "\n".join(f"- #{i+1}: {s}" for i, s in enumerate(self.failed_strategies))
            prompt += f"\n\nПроваленные подходы (ЗАПРЕЩЕНЫ):\n{history}\nПридумай принципиально новую стратегию!"

        raw_plan = call_agent("architect", arch_sys, prompt, stop_flag=self.stop_flag, model_override=selected_model)
        match = re.search(r"```json\n(.*?)```", raw_plan, flags=re.DOTALL)
        try:
            parsed = json.loads(match.group(1).strip() if match else raw_plan.strip(), strict=False)
            tree["nodes"] = self._normalize_nodes(parsed.get("nodes", {}), tree["goal"])
            tree["status"] = "IN_PROGRESS"
        except Exception as e:
            self.log_event("ARCHITECT", f"Ошибка JSON ({e}), создаю единый узел.")
            tree["nodes"] = self._normalize_nodes({}, tree["goal"])
        self.save_tree(tree)
        self.log_event("ARCHITECT", f"Создано узлов: {len(tree['nodes'])}.")

    def get_ready_node(self, tree: dict):
        nodes = tree["nodes"]
        for nid, node in nodes.items():
            if node.get("type") == "ATOMIC" and node.get("status") in ["PENDING", "RETRY"]:
                if all(nodes[d].get("status") == "DONE" for d in node.get("dependencies", []) if d in nodes):
                    return nid, node
        return None, None

    def _run_background_learning(self, tree: dict):
        msg = self.memory.learn_from_task(tree, self.nodes_dir)
        self.log_event("OPTIMIZER", msg)

    def run_loop(self):
        self.log_event("SYSTEM", "Оркестратор запущен.")
        try:
            self.plan_task()
            while True:
                if self.stop_flag.exists():
                    raise TaskStoppedException("Остановлено пользователем")

                tree = self.load_tree()
                node_id, node = self.get_ready_node(tree)
                if not node:
                    statuses = [n.get("status") for n in tree["nodes"].values() if n.get("type") == "ATOMIC"]
                    if statuses and all(s == "DONE" for s in statuses):
                        tree["status"] = "COMPLETED"
                        self.save_tree(tree)
                        self.log_event("SYSTEM", "Все задачи выполнены!")
                        threading.Thread(target=self._run_background_learning, args=(tree,), daemon=True).start()
                        break

                    if any(s == "FAILED" for s in statuses):
                        fid = next(k for k, v in tree["nodes"].items() if v.get("status") == "FAILED")
                        crit_f = self.nodes_dir / fid / "critique.md"
                        crit = crit_f.read_text(encoding="utf-8") if crit_f.exists() else "Ошибка"
                        self.failed_strategies.append(f"Шаг '{tree['nodes'][fid]['title']}' -> {crit[:300]}")
                        self.memory.save_anti_pattern(self.task_dir.name, len(self.failed_strategies), tree["nodes"][fid]["title"], crit, model_override=tree.get("model"))
                        self.plan_task(is_replan=True)
                        continue

                self.runner.execute(node_id, node, tree)

        except TaskStoppedException:
            tree = self.load_tree()
            tree["status"] = "STOPPED"
            for n in tree.get("nodes", {}).values():
                if n.get("status") == "IN_PROGRESS":
                    n["status"] = "STOPPED"
            self.save_tree(tree)
            self.log_event("SYSTEM", "Задача остановлена пользователем.")
        except Exception as e:
            tree = self.load_tree()
            tree["status"] = "FAILED"
            self.save_tree(tree)
            self.log_event("ERROR", f"Системный сбой: {e}")

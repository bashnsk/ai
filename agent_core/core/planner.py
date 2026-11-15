import json
import re
from config import SYSTEM_DIR
from core.node_runner import IMAGE_EXTS
from llm_client import call_agent


class TaskPlanner:
    def __init__(self, orch):
        self.orch = orch

    def normalize_nodes(self, raw_nodes: dict, fallback_goal: str, prefix: str = "") -> dict:
        if not isinstance(raw_nodes, dict) or not raw_nodes:
            nid = f"{prefix}node_1"
            return {nid: {"title": f"{fallback_goal} (сохранить в artifacts/final_report.md)", "type": "ATOMIC", "status": "PENDING", "attempts": 0, "dependencies": [], "is_primitive": False}}
        clean = {}
        id_map = {str(k): f"{prefix}{k}" for k in raw_nodes.keys()}
        for old_id, node in raw_nodes.items():
            if not isinstance(node, dict):
                continue
            new_id = id_map[str(old_id)]
            deps = [id_map[d] for d in node.get("dependencies", []) if isinstance(d, str) and d in id_map]
            clean[new_id] = {"title": str(node.get("title", fallback_goal)), "type": "ATOMIC", "status": "PENDING", "attempts": 0, "dependencies": deps, "is_primitive": False}
        return clean

    def try_fast_path(self, tree: dict) -> bool:
        uploads_dir = self.orch.task_dir / "uploads"
        files = [f for f in uploads_dir.iterdir() if f.is_file()] if uploads_dir.exists() else []
        if files and (sum(f.stat().st_size for f in files) < 16000 or {f.suffix.lower() for f in files}.issubset(IMAGE_EXTS)):
            title = f"Проанализировать прикрепленные файлы ({', '.join(f.name for f in files)}) по запросу '{tree['goal']}' и сохранить в artifacts/final_report.md"
            tree["nodes"] = self.normalize_nodes({"node_1": {"title": title}}, tree["goal"])
            tree["nodes"]["node_1"]["is_primitive"] = True
            self.orch.log_event("ROUTER", "Fast-Path: создан неделимый узел.")
            return True
        return False

    def plan_initial(self):
        tree = self.orch.load_tree()
        if tree.get("nodes"):
            return
        if self.try_fast_path(tree):
            self.orch.save_tree(tree)
            return
        self.orch.log_event("ARCHITECT", "Построение начального графа задач...")
        arch_sys = (SYSTEM_DIR / "architect.md").read_text(encoding="utf-8")
        raw = call_agent("architect", arch_sys, f"Цель:\n{tree['goal']}\n\nБаза знаний:\n{self.orch.memory.get_summary()}", stop_flag=self.orch.stop_flag, model_override=tree.get("model"))
        match = re.search(r"```json\n(.*?)```", raw, flags=re.DOTALL)
        try:
            parsed = json.loads(match.group(1).strip() if match else raw.strip(), strict=False)
            tree["nodes"] = self.normalize_nodes(parsed.get("nodes", {}), tree["goal"])
        except Exception:
            tree["nodes"] = self.normalize_nodes({}, tree["goal"])
        self.orch.save_tree(tree)
        self.orch.log_event("ARCHITECT", f"Создано узлов: {len(tree['nodes'])}.")

    def insert_sub_nodes(self, tree: dict, parent_id: str, raw_subs: dict) -> bool:
        if len(raw_subs) < 2:
            return False
        parent = tree["nodes"][parent_id]
        sub_nodes = self.normalize_nodes(raw_subs, parent["title"], prefix=f"{parent_id}_")
        parent["type"], parent["status"] = "COMPOSITE", "DECOMPOSED"
        first_sub, last_sub = next(iter(sub_nodes)), next(reversed(sub_nodes))
        sub_nodes[first_sub]["dependencies"] = parent.get("dependencies", [])
        for n in tree["nodes"].values():
            n["dependencies"] = [last_sub if d == parent_id else d for d in n.get("dependencies", [])]
        tree["nodes"].update(sub_nodes)
        self.orch.save_tree(tree)
        self.orch.log_event("HTN-SPLIT", f"Составной узел [{parent_id}] разбит на {len(sub_nodes)} под-узлов.")
        return True

    def handle_failed_node(self, tree: dict, failed_id: str, crit: str):
        failed_node = tree["nodes"][failed_id]
        # Если узел уже помечен как неделимый (is_primitive) — не дробим, а меняем инструмент!
        if failed_node.get("is_primitive"):
            self.orch.log_event("TOOL-SWITCH", f"Узел [{failed_id}] уже неделимый (is_primitive). Смена инструмента без дробления...")
            (self.orch.nodes_dir / failed_id / "dynamic_spec.json").unlink(missing_ok=True)
            failed_node["failed_tool_hint"] = f"{failed_node.get('failed_tool_hint', '')}\n- Провал: {crit[:300]}".strip()
            failed_node["status"] = "PENDING"
            failed_node["attempts"] = 0
            self.orch.save_tree(tree)
            return

        self.orch.log_event("ARCHITECT", f"Составной узел [{failed_id}] провалился. Дробление на подшаги...")
        arch_sys = (SYSTEM_DIR / "architect.md").read_text(encoding="utf-8")
        prompt = f"Шаг '{failed_node['title']}' провалился:\n{crit[:400]}\nРазбей его на 2–3 неделимых подшага (каждый создает файл в artifacts/)!"
        raw = call_agent("architect", arch_sys, prompt, stop_flag=self.orch.stop_flag, model_override=tree.get("model"))
        match = re.search(r"```json\n(.*?)```", raw, flags=re.DOTALL)
        try:
            parsed = json.loads(match.group(1).strip() if match else raw.strip(), strict=False)
            if self.insert_sub_nodes(tree, failed_id, parsed.get("nodes", {})):
                return
        except Exception:
            pass
        failed_node["is_primitive"] = True
        failed_node["status"], failed_node["attempts"] = "PENDING", 0
        self.orch.save_tree(tree)

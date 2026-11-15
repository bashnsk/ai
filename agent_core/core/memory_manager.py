import json
import re
from config import MEMORY_DIR, SYSTEM_DIR
from llm_client import call_agent


class MemoryManager:
    def __init__(self):
        self.index_path = MEMORY_DIR / "index.json"
        (MEMORY_DIR / "skills").mkdir(parents=True, exist_ok=True)
        (MEMORY_DIR / "anti_patterns").mkdir(parents=True, exist_ok=True)
        self._load_index()

    def _load_index(self) -> dict:
        if self.index_path.exists():
            raw = self.index_path.read_text(encoding="utf-8").strip()
            if raw:
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    pass
        default_data = {"skills": []}
        self.index_path.write_text(json.dumps(default_data, indent=2, ensure_ascii=False), encoding="utf-8")
        return default_data

    def get_summary(self) -> str:
        index_data = self._load_index()
        skills = index_data.get("skills", [])
        if not skills:
            return "База знаний пока пуста."
        return "\n".join(f"- [{s['skill_id']}] ({', '.join(s['tags'])}): {s['summary']}" for s in skills)

    def get_recipes(self, task_title: str) -> str:
        index_data = self._load_index()
        recipes = []
        title_lower = task_title.lower()
        for s in index_data.get("skills", []):
            if any(tag.lower() in title_lower for tag in s.get("tags", [])):
                skill_file = MEMORY_DIR / "skills" / f"{s['skill_id']}.md"
                if skill_file.exists():
                    recipes.append(f"### Рецепт: {s['summary']}\n{skill_file.read_text(encoding='utf-8')}")
        return "\n\n".join(recipes) if recipes else "Нет готовых рецептов."

    def save_anti_pattern(self, task_name: str, attempt: int, title: str, critique: str):
        path = MEMORY_DIR / "anti_patterns" / f"fail_{task_name}_{attempt}.md"
        path.write_text(f"Узел: {title}\nОшибка:\n{critique}", encoding="utf-8")

    def learn_from_task(self, tree: dict, nodes_dir) -> str:
        opt_sys = (SYSTEM_DIR / "optimizer.md").read_text(encoding="utf-8")
        report = f"Цель: {tree['goal']}\n\nУспешные шаги:\n"
        for nid, node in tree["nodes"].items():
            res_f = nodes_dir / nid / "result.md"
            res_txt = res_f.read_text(encoding="utf-8")[:1500] if res_f.exists() else ""
            report += f"\n--- {nid}: {node['title']} ---\n{res_txt}\n"

        raw_opt = call_agent("optimizer", opt_sys, report)
        match = re.search(r"```json\n(.*?)```", raw_opt, flags=re.DOTALL)
        json_str = match.group(1).strip() if match else raw_opt.strip()

        try:
            data = json.loads(json_str)
            if not data.get("should_save"):
                return "Новых уникальных паттернов не выявлено."
            skill_id = re.sub(r"[^a-zA-Z0-9_]", "_", data["skill_id"])
            (MEMORY_DIR / "skills" / f"{skill_id}.md").write_text(data["recipe"], encoding="utf-8")

            index_data = self._load_index()
            index_data["skills"] = [s for s in index_data["skills"] if s["skill_id"] != skill_id]
            index_data["skills"].append({
                "skill_id": skill_id,
                "tags": data.get("tags", []),
                "summary": data.get("summary", "")
            })
            self.index_path.write_text(json.dumps(index_data, indent=2, ensure_ascii=False), encoding="utf-8")
            return f"Сохранен новый навык: [{skill_id}] — {data.get('summary')}"
        except Exception as e:
            return f"Ошибка сохранения навыка: {e}"

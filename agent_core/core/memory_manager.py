import json
import re
from config import MEMORY_DIR, SYSTEM_DIR
from llm_client import call_agent

MAX_RAW_ANTI_PATTERNS = 15


class MemoryManager:
    def __init__(self):
        self.index_path = MEMORY_DIR / "index.json"
        self.rules_path = MEMORY_DIR / "rules.md"
        self.anti_dir = MEMORY_DIR / "anti_patterns"
        (MEMORY_DIR / "skills").mkdir(parents=True, exist_ok=True)
        self.anti_dir.mkdir(parents=True, exist_ok=True)
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
        skills_txt = "\n".join(f"- [{s['skill_id']}] ({', '.join(s['tags'])}): {s['summary']}" for s in skills) if skills else "Навыков пока нет."
        rules_txt = self.rules_path.read_text(encoding="utf-8").strip() if self.rules_path.exists() else "Правил пока нет."
        return f"### Проверенные навыки:\n{skills_txt}\n\n### Запрещенные грабли (избегать):\n{rules_txt}"

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
        path = self.anti_dir / f"fail_{task_name}_{attempt}.md"
        path.write_text(f"Узел: {title}\nОшибка:\n{critique[:800]}", encoding="utf-8")

        files = sorted(self.anti_dir.glob("fail_*.md"), key=lambda f: f.stat().st_mtime)
        if len(files) >= 5:
            self._consolidate_anti_patterns(files)
        while len(files) > MAX_RAW_ANTI_PATTERNS:
            files.pop(0).unlink(missing_ok=True)

    def _consolidate_anti_patterns(self, files: list):
        raw_errors = "\n---\n".join(f.read_text(encoding="utf-8")[:500] for f in files[-10:])
        old_rules = self.rules_path.read_text(encoding="utf-8") if self.rules_path.exists() else ""
        prompt = (
            f"Текущие правила:\n{old_rules}\n\nНовые ошибки:\n{raw_errors}\n\n"
            "Объедини это в список из максимум 10 коротких однострочных правил (чего НЕ делать при парсинге и поиске). Верни только маркированный список."
        )
        try:
            new_rules = call_agent("optimizer", "Ты — аналитик ошибок. Сжимай логи в короткие правила.", prompt)
            self.rules_path.write_text(new_rules.strip(), encoding="utf-8")
            for f in files:
                f.unlink(missing_ok=True)
        except Exception:
            pass

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
            index_data["skills"].append({"skill_id": skill_id, "tags": data.get("tags", []), "summary": data.get("summary", "")})
            self.index_path.write_text(json.dumps(index_data, indent=2, ensure_ascii=False), encoding="utf-8")
            return f"Сохранен новый навык: [{skill_id}] — {data.get('summary')}"
        except Exception as e:
            return f"Ошибка сохранения навыка: {e}"

import json
import re
import threading
from datetime import datetime
from config import MEMORY_DIR, SYSTEM_DIR
from llm_client import call_agent

SKILL_PROMOTION_THRESHOLD = 3


class MemoryManager:
    def __init__(self):
        self.index_path = MEMORY_DIR / "index.json"
        self.cand_path = MEMORY_DIR / "candidates.json"
        self.rules_path = MEMORY_DIR / "rules.md"
        self.anti_dir = MEMORY_DIR / "anti_patterns"
        (MEMORY_DIR / "skills").mkdir(parents=True, exist_ok=True)
        self.anti_dir.mkdir(parents=True, exist_ok=True)
        self._load_json(self.index_path, {"skills": []})
        self._load_json(self.cand_path, {"candidates": {}})

    def _load_json(self, path, default: dict) -> dict:
        if path.exists():
            raw = path.read_text(encoding="utf-8").strip()
            if raw:
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    pass
        path.write_text(json.dumps(default, indent=2, ensure_ascii=False), encoding="utf-8")
        return default

    def get_summary(self) -> str:
        skills = self._load_json(self.index_path, {"skills": []}).get("skills", [])
        skills_txt = "\n".join(f"- [{s['skill_id']}] ({', '.join(s['tags'])}): {s['summary']}" for s in skills) if skills else "Навыков пока нет."
        rules_txt = self.rules_path.read_text(encoding="utf-8").strip() if self.rules_path.exists() else "Правил пока нет."
        return f"### Проверенные навыки:\n{skills_txt}\n\n### Запрещенные грабли (избегать):\n{rules_txt}"

    def get_recipes(self, task_title: str) -> str:
        skills = self._load_json(self.index_path, {"skills": []}).get("skills", [])
        recipes = []
        title_lower = task_title.lower()
        for s in skills:
            if any(tag.lower() in title_lower for tag in s.get("tags", [])):
                sf = MEMORY_DIR / "skills" / f"{s['skill_id']}.md"
                if sf.exists():
                    recipes.append(f"### Рецепт: {s['summary']}\n{sf.read_text(encoding='utf-8')}")
        return "\n\n".join(recipes) if recipes else "Нет готовых рецептов."

    def save_anti_pattern(self, task_name: str, attempt: int, title: str, critique: str, model_override: str = None):
        path = self.anti_dir / f"fail_{task_name}_{attempt}.md"
        path.write_text(f"Узел: {title}\nОшибка:\n{critique[:800]}", encoding="utf-8")
        files = sorted(self.anti_dir.glob("fail_*.md"), key=lambda f: f.stat().st_mtime)
        if len(files) >= 5:
            threading.Thread(target=self._consolidate_anti_patterns, args=(list(files), model_override), daemon=True).start()

    def _consolidate_anti_patterns(self, files: list, model_override: str = None):
        raw_errors = "\n---\n".join(f.read_text(encoding="utf-8")[:500] for f in files[-10:] if f.exists())
        old_rules = self.rules_path.read_text(encoding="utf-8") if self.rules_path.exists() else ""
        prompt = f"Текущие правила:\n{old_rules}\n\nНовые ошибки:\n{raw_errors}\n\nСожми в максимум 10 коротких правил (чего НЕ делать). Только список."
        try:
            new_rules = call_agent("optimizer", "Ты — аналитик ошибок.", prompt, model_override=model_override)
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
            report += f"\n--- {nid}: {node['title']} ---\n{res_f.read_text(encoding='utf-8')[:1500] if res_f.exists() else ''}\n"

        raw_opt = call_agent("optimizer", opt_sys, report, model_override=tree.get("model"))
        json_match = re.search(r"```json\n(.*?)```", raw_opt, flags=re.DOTALL)
        md_match = re.search(r"```markdown\n(.*?)```", raw_opt, flags=re.DOTALL)
        try:
            data = json.loads(json_match.group(1).strip() if json_match else raw_opt.strip(), strict=False)
            if not data.get("should_save"):
                return "Повторяемых паттернов не выявлено."
            raw_id = re.sub(r"[^a-zA-Z0-9_]", "_", data.get("skill_id", "")).strip("_")
            skill_id = raw_id if raw_id else f"pat_{datetime.now().strftime('%H%M%S')}"
            recipe = md_match.group(1).strip() if md_match else data.get("recipe", "")

            c_data = self._load_json(self.cand_path, {"candidates": {}})
            cand = c_data["candidates"].get(skill_id, {"hits": 0, "tags": data.get("tags", []), "summary": data.get("summary", ""), "recipe": recipe})
            cand["hits"] += 1
            cand["recipe"] = recipe
            c_data["candidates"][skill_id] = cand
            self.cand_path.write_text(json.dumps(c_data, indent=2, ensure_ascii=False), encoding="utf-8")

            if cand["hits"] < SKILL_PROMOTION_THRESHOLD:
                return f"Кандидат в навыки [{skill_id}]: подтверждений {cand['hits']}/{SKILL_PROMOTION_THRESHOLD}."

            (MEMORY_DIR / "skills" / f"{skill_id}.md").write_text(recipe, encoding="utf-8")
            idx = self._load_json(self.index_path, {"skills": []})
            idx["skills"] = [s for s in idx["skills"] if s["skill_id"] != skill_id]
            idx["skills"].append({"skill_id": skill_id, "tags": cand["tags"], "summary": cand["summary"]})
            self.index_path.write_text(json.dumps(idx, indent=2, ensure_ascii=False), encoding="utf-8")
            del c_data["candidates"][skill_id]
            self.cand_path.write_text(json.dumps(c_data, indent=2, ensure_ascii=False), encoding="utf-8")
            return f"Кандидат [{skill_id}] подтвержден {SKILL_PROMOTION_THRESHOLD} раза и переведен в постоянные навыки!"
        except Exception as e:
            return f"Ошибка анализа навыка: {e}"

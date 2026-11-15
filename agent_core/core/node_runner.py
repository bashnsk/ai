import json
import re
import shutil
from config import SYSTEM_DIR, MAX_ATTEMPTS_PER_NODE, DEFAULT_MODEL
from core.context_builder import build_dynamic_spec
from core.model_profiler import build_model_profile
from llm_client import call_agent, extract_text_from_image
from sandbox import run_python_script

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


class NodeRunner:
    def __init__(self, orch):
        self.orch = orch

    def _prepare_uploads(self, node_dir) -> tuple[str, bool]:
        uploads_dir = self.orch.task_dir / "uploads"
        if not uploads_dir.exists():
            return "Нет прикрепленных файлов.", False
        parts = []
        for f in sorted(uploads_dir.iterdir()):
            if not f.is_file():
                continue
            shutil.copy2(f, node_dir / f.name)
            if f.suffix.lower() in IMAGE_EXTS:
                ocr_txt = extract_text_from_image(f)
                parts.append(f"### Изображение `{f.name}` (путь: `{f}`):\nРезультат анализа:\n{ocr_txt}")
            else:
                txt = f.read_text(encoding="utf-8", errors="ignore")[:8000]
                parts.append(f"### Файл `{f.name}` (путь: `{f}`):\n```\n{txt}\n```")
        return ("\n\n".join(parts), True) if parts else ("Нет прикрепленных файлов.", False)

    def _read_artifacts_preview(self) -> str:
        if not self.orch.artifacts_dir.exists():
            return "Файлы в artifacts/ не созданы."
        parts = []
        for f in sorted(self.orch.artifacts_dir.iterdir()):
            if f.is_file():
                content = f.read_text(encoding="utf-8", errors="ignore").strip()
                parts.append(f"### Файл {f.name} ({len(content)} симв.):\n```\n{content[:3500]}\n```")
        return "\n\n".join(parts) if parts else "Папка artifacts/ пуста."
    def execute(self, node_id: str, node: dict, tree: dict):
        node_dir = self.orch.nodes_dir / node_id
        node_dir.mkdir(parents=True, exist_ok=True)
        selected_model = tree.get("model")
        profile = build_model_profile()
        active_w_model = profile.get("worker_code", {}).get("model", DEFAULT_MODEL) if selected_model == "AUTO" else selected_model

        node["attempts"] = node.get("attempts", 0) + 1
        node["status"] = "IN_PROGRESS"
        self.orch.save_tree(tree)
        self.orch.log_event(node_id, f"Попытка #{node['attempts']} ({active_w_model}): {node['title']}")

        uploads_info, has_uploads = self._prepare_uploads(node_dir)
        spec_file = node_dir / "dynamic_spec.json"
        if not spec_file.exists():
            self.orch.log_event(node_id, "Генерация персональных критериев задачи...")
            spec = build_dynamic_spec(tree["goal"], node["title"], has_uploads, model_override=selected_model)
            spec_file.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            spec = json.loads(spec_file.read_text(encoding="utf-8"))

        dep_results = ""
        for dep_id in node.get("dependencies", []):
            res_file = self.orch.nodes_dir / dep_id / "result.md"
            if res_file.exists():
                dep_results += f"\n--- Шаг {dep_id} ---\n{res_file.read_text(encoding='utf-8')}\n"

        critique_file = node_dir / "critique.md"
        prev_crit = critique_file.read_text(encoding="utf-8") if critique_file.exists() else "Нет."
        recipes = self.orch.memory.get_recipes(node["title"])

        context_md = (
            f"# Задача\n{node['title']}\n\n"
            f"# Персональные технические требования (Curator)\n{spec['worker_rules']}\n\n"
            f"# Папка артефактов\n{self.orch.artifacts_dir}\n\n"
            f"# Прикрепленные пользователем файлы\n{uploads_info}\n\n"
            f"# Рецепты из памяти\n{recipes}\n\n"
            f"# Данные прошлых шагов\n{dep_results or 'Нет'}\n\n"
            f"# Замечания Критика с прошлой попытки\n{prev_crit}\n"
        )
        (node_dir / "context.md").write_text(context_md, encoding="utf-8")

        worker_sys = (SYSTEM_DIR / "worker.md").read_text(encoding="utf-8")
        worker_out = call_agent("worker_code", worker_sys, context_md, stop_flag=self.orch.stop_flag, model_override=selected_model)

        match = re.search(r"```python\n(.*?)```", worker_out, flags=re.DOTALL)
        code = match.group(1).strip() if match else ""
        exec_report = "\n## Выполнение кода: СКРИПТ НЕ ЗАПУСКАЛСЯ\n"

        if code:
            self.orch.log_event(node_id, "Запуск Python-скрипта...")
            res = run_python_script(code, node_dir)
            exec_report = (
                f"\n## Выполнение кода (exit: {res['returncode']})\n"
                f"STDOUT:\n```\n{res['stdout']}\n```\nSTDERR:\n```\n{res['stderr']}\n```\n"
            )
        elif has_uploads:
            self.orch.artifacts_dir.mkdir(parents=True, exist_ok=True)
            (self.orch.artifacts_dir / "final_report.md").write_text(worker_out, encoding="utf-8")

        artifacts_preview = self._read_artifacts_preview()
        result_md = f"{worker_out}\n{exec_report}\n## Содержимое папки artifacts/\n{artifacts_preview}\n"
        (node_dir / "result.md").write_text(result_md, encoding="utf-8")

        critic_sys = (SYSTEM_DIR / "critic.md").read_text(encoding="utf-8")
        critic_input = (
            f"# Исходная задача\n{tree['goal']}\n\n# Текущий узел\n{node['title']}\n\n"
            f"# Персональный чек-лист приемки (Curator)\n{spec['critic_checklist']}\n\n# Результат и файлы\n{result_md}"
        )
        critic_out = call_agent("critic", critic_sys, critic_input, stop_flag=self.orch.stop_flag, model_override=selected_model)
        critique_file.write_text(critic_out, encoding="utf-8")

        if "VERDICT: PASS" in critic_out.upper():
            node["status"] = "DONE"
            self.orch.log_event(node_id, "Критик: PASS. Узел закрыт.")
        elif node["attempts"] >= MAX_ATTEMPTS_PER_NODE:
            node["status"] = "FAILED"
            self.orch.log_event(node_id, "Критик: FAIL. Лимит попыток исчерпан.")
        else:
            node["status"] = "RETRY"
            self.orch.log_event(node_id, "Критик: FAIL. Отправлено на доработку.")

        self.orch.save_tree(tree)

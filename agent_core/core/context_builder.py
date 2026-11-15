import json
import re
from llm_client import call_agent

BUILDER_SYS = """Ты — Мета-инженер промптов (Curator). Твоя задача — сгенерировать точные технические правила выполнения и строгий чек-лист приемки строго под текущую задачу.

Правила:
1. Никаких общих слов. Определи тип задачи (анализ файлов/папок, веб-поиск, парсинг, генерация кода, работа с прикрепленным файлом) и сформулируй конкретные требования.
2. Запрети выдуманные данные (хардкод): все данные должны добываться динамически.
3. Верни ответ СТРОГО в формате JSON:
```json
{
  "worker_rules": "3-4 конкретных технических требования для Python-скрипта Исполнителя под эту задачу",
  "critic_checklist": "3-4 конкретных пункта проверки для Критика, без выполнения которых ставится FAIL"
}
```"""


def build_dynamic_spec(goal: str, node_title: str, has_uploads: bool, model_override: str = None) -> dict:
    prompt = (
        f"Глобальная цель: {goal}\n"
        f"Текущий шаг: {node_title}\n"
        f"Наличие прикрепленных пользователем файлов: {'Да' if has_uploads else 'Нет'}\n"
        "Сгенерируй правила для Исполнителя и чек-лист приемки для Критика."
    )
    raw = call_agent("curator", BUILDER_SYS, prompt, model_override=model_override)
    match = re.search(r"```json\n(.*?)```", raw, flags=re.DOTALL)
    try:
        data = json.loads(match.group(1).strip() if match else raw.strip(), strict=False)
        return {
            "worker_rules": str(data.get("worker_rules", "")).strip(),
            "critic_checklist": str(data.get("critic_checklist", "")).strip()
        }
    except Exception:
        return {
            "worker_rules": "Выполни задачу динамически через Python без захардкоженных примеров и сохрани результат в artifacts/final_report.md.",
            "critic_checklist": "1. Код выполнился без ошибок (exit 0). 2. Данные реальные, не выдуманные. 3. Выполнены все пункты запроса."
        }

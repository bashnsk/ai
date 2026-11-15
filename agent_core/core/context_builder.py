import json
import re
from llm_client import call_agent

SPLITTER_SYS = """Ты — HTN-анализатор сложности. Оцени, является ли шаг уже НЕДЕЛИМЫМ (примитивным) действием.
Правила:
1. Шаг является НЕДЕЛИМЫМ (`"is_primitive": true`), если он описывает одно конкретное техническое действие с одним результатом.
2. Шаг нужно делить (`"is_primitive": false`), ТОЛЬКО если он объединяет несколько этапов (сбор данных + фильтрация + отчет).
3. КРИТИЧЕСКИ ВАЖНО: В заголовок (`title`) КАЖДОГО подшага обязательно включай конкретную предметную область и ключевые слова из глобальной цели (НИКОГДА не пиши абстрактно «собрать ссылки через поиск» без указания темы поиска!).

Верни СТРОГО JSON:
Если узел неделимый: `{"is_primitive": true}`
Если узел составной:
```json
{
  "is_primitive": false,
  "sub_nodes": {
    "sub_1": {"title": "Собрать сырые ссылки по теме [КОНКРЕТНАЯ ТЕМА ИЗ ЦЕЛИ] в artifacts/step1.json", "dependencies": []},
    "sub_2": {"title": "Проверить и отфильтровать ссылки из artifacts/step1.json по критериям [КРИТЕРИИ ИЗ ЦЕЛИ] и сохранить в artifacts/step2.json", "dependencies": ["sub_1"]},
    "sub_3": {"title": "Сформировать итоговый отчет artifacts/final_report.md из artifacts/step2.json", "dependencies": ["sub_2"]}
  }
}
```"""

BUILDER_SYS = """Ты — Мета-инженер промптов (Curator). Сгенерируй точные правила для Python-скрипта Исполнителя и чек-лист приемки для Критика.

Обязательные требования:
1. Поисковые запросы обязаны содержать конкретные ключевые слова из глобальной цели (никаких абстрактных слов вроде «объявления» или «товары»).
2. Для поиска через `ddgs` используй `DDGS().text("конкретный запрос", region="ru-ru", max_results=15)` с паузой `time.sleep(1.5)`.
3. Если используется `Playwright`, ЗАПРЕЩЕНО вызывать `page.fill()`, `page.click()` или `wait_for_selector()`! Открывай сразу готовый URL поиска (`page.goto(url, wait_until="domcontentloaded")`), забирай HTML через `page.content()` и парси все ссылки `a[href]` через `BeautifulSoup`.
4. Верни СТРОГО JSON:
```json
{
  "worker_rules": "3-4 конкретных технических требования для Python-скрипта",
  "critic_checklist": "3-4 конкретных пункта проверки для Критика"
}
```"""


def evaluate_node_complexity(node_title: str, model_override: str = None, goal: str = "") -> dict | None:
    prompt = f"Глобальная цель: {goal}\nТекущий шаг: {node_title}"
    raw = call_agent("curator", SPLITTER_SYS, prompt, model_override=model_override)
    match = re.search(r"```json\n(.*?)```", raw, flags=re.DOTALL)
    try:
        data = json.loads(match.group(1).strip() if match else raw.strip(), strict=False)
        if data.get("is_primitive") is False and isinstance(data.get("sub_nodes"), dict) and len(data["sub_nodes"]) >= 2:
            return data["sub_nodes"]
    except Exception:
        pass
    return None


def build_dynamic_spec(goal: str, node_title: str, has_uploads: bool, model_override: str = None, failed_tool_hint: str = "") -> dict:
    prompt = f"Глобальная цель: {goal}\nТекущий шаг: {node_title}\nПрикрепленные файлы: {'Да' if has_uploads else 'Нет'}"
    if failed_tool_hint:
        prompt += f"\n\nВНИМАНИЕ! Предыдущий способ ПРОВАЛИЛСЯ:\n{failed_tool_hint}\nСмени инструмент/источник решения!"
    raw = call_agent("curator", BUILDER_SYS, prompt, model_override=model_override)
    match = re.search(r"```json\n(.*?)```", raw, flags=re.DOTALL)
    try:
        data = json.loads(match.group(1).strip() if match else raw.strip(), strict=False)
        return {"worker_rules": str(data.get("worker_rules", "")).strip(), "critic_checklist": str(data.get("critic_checklist", "")).strip()}
    except Exception:
        return {
            "worker_rules": f"Выполни шаг по теме '{goal}' динамически через Python (без wait_for_selector/fill) и сохрани результат в artifacts/.",
            "critic_checklist": "1. Код выполнился без ошибок (exit 0). 2. Запросы соответствуют глобальной цели. 3. Файл в artifacts/ создан и не пуст."
        }

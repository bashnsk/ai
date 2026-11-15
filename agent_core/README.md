# Agent Core — Самообучающийся HTN-агент для локальных LLM

Автономный оркестратор задач на базе **HTN (Hierarchical Task Network)** и файловой архитектуры **Blackboard**, оптимизированный под локальные языковые модели в **Ollama** с небольшим контекстным окном.

Каждое обращение к модели выполняется в чистой stateless-сессии с жестко ограниченным микро-контекстом, строгой проверкой каждого шага через **Критика (QA)** и автоматическим сохранением удачных решений в долговременную базу знаний (`/memory/`).

---

## Архитектура системы

1. **Архитектор (`architect.md`)** — разбивает глобальную цель на 2–4 атомарных шага (`tree.json`) и перестраивает граф (`REPLAN`), если узел уходит в тупик после 3 неудачных попыток.
2. **Исполнитель (`worker.md`)** — пишет и выполняет изолированные Python-скрипты (поиск через `ddgs`, парсинг через `BeautifulSoup4` и рендеринг JS-страниц через `Playwright`).
3. **Критик (`critic.md`)** — проверяет код выхода (`exit code`), логи и наличие реальных фактических данных. Отклоняет заглушки и пустые ответы (`VERDICT: FAIL`).
4. **Оптимизатор (`optimizer.md`)** — после успешного закрытия задачи анализирует путь решения и сохраняет проверенные рецепты в `memory/skills/` и `memory/index.json`.
5. **Веб-интерфейс (`web/`)** — дашборд на FastAPI с живым графом подзадач, логами, экстренной остановкой (`СТОП`) и вкладкой управления базой знаний.

---

## Системные требования

* **ОС:** Debian GNU/Linux (Sid / Bookworm) или Ubuntu 22.04+
* **GPU:** Видеокарта с поддержкой ROCm или CUDA (от 12–16 ГБ VRAM для комфортной работы 14B-моделей)
* **Сервер LLM:** Установленный и запущенный [Ollama](https://ollama.com) (`http://localhost:11434`)
* **Python:** 3.10+ (`python3-venv`, `python3-full`)

---

## Пошаговая установка и запуск на Linux

### 1. Подготовка локальной модели в Ollama

Убедитесь, что служба Ollama запущена, и скачайте рекомендуемую универсальную модель (`qwen2.5-coder:14b`):

```bash
sudo systemctl enable --now ollama
ollama pull qwen2.5-coder:14b
```

*(При необходимости привязку ролей к другим моделям можно изменить в `config.py`).*

### 2. Клонирование репозитория

```bash
git clone git@github.com:bashnsk/ai.git
cd ai/agent_core
```

### 3. Создание виртуального окружения и установка зависимостей

В современных версиях Debian/Ubuntu системный Python защищен от глобальной установки пакетов (PEP 668), поэтому создаем локальное окружение `.venv`:

```bash
# Установка системных пакетов для venv
sudo apt update && sudo apt install -y python3-venv python3-full

# Создание и активация виртуального окружения
python3 -m venv .venv
source .venv/bin/activate

# Установка Python-библиотек
pip install --upgrade pip
pip install fastapi uvicorn requests beautifulsoup4 lxml ddgs playwright

# Установка Headless Chromium и системных зависимостей для Playwright
playwright install chromium
sudo .venv/bin/playwright install-deps chromium
```

### 4. Настройка путей (`config.py`)

Откройте `config.py` и проверьте, что переменная `BASE_DIR` указывает на актуальный путь размещения проекта на вашей машине:

```python
BASE_DIR = Path(__file__).resolve().parent
```
*(Если в `config.py` прописан жесткий путь, замените его на `Path(__file__).resolve().parent`, чтобы проект работал из любой директории).*

### 5. Запуск веб-интерфейса и оркестратора

При активном виртуальном окружении `(.venv)` запустите сервер Uvicorn на порту `8001`:

```bash
uvicorn web.server:app --host 0.0.0.0 --port 8001 --reload --reload-exclude "workspace/*" --reload-exclude "memory/*"
```

После запуска откройте в браузере:
* **Локально:** `http://localhost:8001`
* **По локальной сети:** `http://<IP-адрес-сервера>:8001`

---

## Структура директорий

```text
agent_core/
├── config.py              # Настройки путей, моделей и лимитов попыток
├── llm_client.py          # Потоковый stateless-клиент Ollama с мгновенной остановкой
├── orchestrator.py        # Главный цикл управления графом задач
├── sandbox.py             # Безопасный запуск сгенерированных Python-скриптов
├── core/
│   ├── memory_manager.py  # Индексация навыков и журнал антипаттернов
│   └── node_runner.py     # Изолированный цикл выполнения одного узла
├── system/                # Системные промпты ролей (architect, worker, critic, optimizer)
├── memory/                # Долговременная самообучающаяся база знаний
├── workspace/             # Изолированные папки задач (формат YYYY-MM-DD_HH-MM-SS)
└── web/                   # Backend (FastAPI) и Frontend (HTML/CSS/JS)
```

# VisaPilot backend

Локальный прототип с вымышленными документами. Реализованы **пакеты 1 и 11**:
запуск, SQLite, общие модели, HTTP-контракт и аккаунты с cookie-сессиями/CSRF.
Остальные предметные маршруты после входа отвечают `503 module_not_ready`,
пока владельцы не подключат свои реализации.

Нужен Python 3.12. Команды из корня репозитория:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock -e '.[test]'
cp .env.example .env
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

- Health: <http://127.0.0.1:8000/api/v1/health>
- Swagger: <http://127.0.0.1:8000/docs>
- OpenAPI: <http://127.0.0.1:8000/openapi.json>, снимок [openapi.json](openapi.json).

Проверки:

```bash
python -m pytest -q
python -m pip check
python -m app.common.export_openapi --check
python tests/auth_http_smoke.py
```

Для изменения схем обновите снимок: `python -m app.common.export_openapi`.
Остановка сервера — Ctrl+C. Перезапуск сохраняет `local_data/visapilot.sqlite3`.

[Передача Ване и frontend, модели, хранение и ограничения](docs/backend-handoff.md).
[Аккаунты: Origin, cookie, CSRF и проверка владельца](docs/auth-handoff.md).
[Продуктовая документация и распределение пакетов](docs/README.md).

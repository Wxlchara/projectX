# Пакет 5: личный архив, извлечение и AI-адаптер

Пакет подключён к общим моделям/SQLite пакета 1 и реальным сессиям/CSRF
пакета 11. Все семь HTTP-операций архива реализованы. Ветка:
`codex/vanya-05-documents`. Общие схемы, DDL, app/main.py, pyproject.toml,
requirements.lock и корневой openapi.json не изменены.

## Установка и запуск

Из корня проекта, Python 3.12, один Uvicorn worker:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock -r app/documents/requirements.txt -r app/ai/requirements.txt
.venv/bin/python -m uvicorn app.documents.application:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

Это factory поверх общего create_app, с `feature_routers={5: router}`.
Адреса: `/api/v1/documents`, `/docs`, `/openapi.json`. Базовый
`app.main:app` по-прежнему запускает каркас Матвея с pending-пакетом 5.
Для общего запуска Матвей подключает наш router в своём composition root:

```python
from app.documents.router import router as documents_router
app = create_app(feature_routers={5: documents_router})
```

Если его приложение добавит сервис пакета 4, передать CaseRebuilder в
DocumentArchive либо объявить `application.state.case_rebuilder` при
подключении роутера. В нашем factory доступен параметр rebuilder.

Нужны Tesseract и языки rus/eng; OCR и AI не запускаются при старте.
Системная установка — по [официальной инструкции](https://tesseract-ocr.github.io/tessdoc/Installation).
Можно оставить модели внутри игнорируемого local_data, не меняя системные
языковые файлы. Пример после установки самого Tesseract:

```bash
mkdir -p local_data/tessdata
curl --fail --location --output local_data/tessdata/rus.traineddata https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/rus.traineddata
curl --fail --location --output local_data/tessdata/eng.traineddata https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/eng.traineddata
TESSDATA_PREFIX="$PWD/local_data/tessdata" tesseract --list-langs
```

При наличии обоих файлов сервис автоматически использует
`settings.data_dir/tessdata`; явный TESSDATA_PREFIX имеет приоритет.
Установка/скачивание никогда не выполняются приложением автоматически.
На этой машине проверен Tesseract 5.5.3, rus из tessdata_fast 4.1.0,
eng из Homebrew. В Git модели не входят. Для воспроизводимой установки на
другом ноутбуке можно использовать обе модели из одного указанного тега.

## HTTP и владение

Все пути после `/api/v1`, user_id берётся только из сессии.

| Метод и путь | Результат |
| --- | --- |
| POST /documents | multipart file + document_type -> 201 Document, uploaded |
| GET /documents | Items[Document], только свой архив |
| GET /documents/{id} | Общая модель Document, без путей/raw_text |
| GET /documents/{id}/file | Свой оригинал, attachment/octet-stream, no-store/nosniff |
| POST /documents/{id}/extract | Извлечение и новая facts_revision |
| PATCH /documents/{id}/fields | Исправление/подтверждение по expected_facts_revision |
| DELETE /documents/{id} | 204, оригинал и поля удалены, связи сняты |

На чтении require_user, на изменении require_csrf. Чужой/отсутствующий ID —
одинаковый 404; без сессии — 401; без CSRF — 403. На upload сессия проверяется
до чтения тела. Multipart допускает один файл и одно поле типа; лишний
owner_id отвергается. Фактический размер файла ограничивается в callback
парсера **до записи** лишних байтов в spool. Общий multipart-body ограничен
размером файла + 64 KiB; незавершённые/ошибочные части закрываются.

Файл проверяется по содержимому, не по расширению/заявленному MIME. PDF,
PNG, JPEG, <=10 MiB, <=10 страниц, <=20 млн пикселей; меньшие лимиты Settings
учитываются. Повреждённый/зашифрованный/слишком большой файл не попадает в
архив. Оригинал хранится в закрытом uploads с UUID-именем и mode 0600;
каждая загрузка создаёт новый ID, даже при одинаковом sha256. Исходное имя
только отображается и очищается от каталогов/управляющих символов.
Скачивание не следует symlink и сверяет размер/sha256. Публичной статики нет.

## Извлечение и ручное подтверждение

Сначала pypdf; страницы без текста -> pypdfium2 -> Pillow -> Tesseract TSV.
PDFium полностью закрывается внутри mutex в отдельном worker-процессе.
Одна обработка одновременно, дедлайн <=60 секунд, вход <=10 MiB, текст
<=100 тыс. символов, поток страницы <=8 MiB. Тайм-аут/отмена завершают группу
процессов; родитель удаляет временный каталог. HTTP event loop не блокируется;
SQLite-транзакции не удерживаются во время OCR/AI.

Парсер консервативный: строки с английскими/русскими метками, ISO/DD.MM.YYYY,
десятичные суммы. Неподдержанный бланк остаётся для ручного ввода. MRZ,
произвольные банковские таблицы и оценка правил страны здесь не реализованы.
Результат зависит от прочитанных байтов, не от имени файла/manifest образцов.

| Тип | Field.key |
| --- | --- |
| passport | given_name, surname, birth_date, passport_kind, issued_on, expires_on |
| bank_statement | holder_name, statement_on, balance, currency |
| insurance | holder_name, starts_on, ends_on, territory, coverage_amount, currency |

Даты ISO, суммы строки Decimal, неизвестное null. Валюта из трёх букв —
кандидат, а не доказательство признанного ISO-кода/сопоставимости валют.
Паспортный issued_on/expires_on нужно сопоставлять с passport_issued_on/
passport_expires_on профиля. source_page — 1-based; PDF confidence=null;
OCR confidence — минимальная уверенность слов строки [0,1].

Извлечение всегда сбрасывает подтверждения; quality_findings используют
общий Finding с origin=technical, severity=verify. Низкая уверенность,
пропуск/конфликт/невалидное поле -> needs_input. При реальном OCR образцов
встречается ALEXE! вместо ALEXEI: значение имени остаётся null с замечанием,
а не заменяется ожидаемым именем из фикстуры.

PATCH принимает общий DocumentFieldsPatch. Повторяющиеся/неподдержанные ключи,
нестроковые значения, невалидные даты/суммы и подтверждение null -> 422.
Строки нормализуются; изменённое значение получает source_kind=manual,
confidence/source_page=null. Подтверждение неизменённого значения сохраняет
его исходное происхождение. Любой непустой успешный PATCH увеличивает ревизию;
пустой PATCH — no-op. Подтверждения неизменённых значений переносятся на новую
ревизию; изменённое поле подтверждается только явным user_confirmed=true.
Неверный expected_facts_revision -> 409 без изменения данных.

`get_confirmed_facts(owner_id, document_ids)` реализует общий протокол:
только поля с user_confirmed=true и confirmed_revision=facts_revision,
с документом/типом/ревизией. Документ без подтверждений имеет fields=[], а не
ложный pass. Чужой ID запрещён и на внутреннем интерфейсе.

## Удаление, конкуренция и зависимые дела

После правки/подтверждения/извлечения/удаления для всех связанных дел в одной
транзакции: revision++, stale=1, причина, projection_json=NULL; checks,
letters и routes становятся stale. При удалении связи снимаются FK.
Чужие документы и дела не затрагиваются. После commit вызывается переданный
CaseRebuilder; без пакета 4 результат остаётся явно stale без старой готовности.
Реальная проекция чек-листа/прогресса — зона пакета 4, её успешность не заявляется.

Перед сохранением результата долгого OCR повторно проверяются owner,
существование документа и facts_revision. Параллельная правка -> 409,
удаление -> 404; старый OCR ничего не воссоздаёт/не перезаписывает.
Busy -> 409 без изменения документа; timeout -> needs_input с ручным вводом.

При удалении оригинал временно перемещается в закрытый tmp: rollback
восстанавливает его, commit удаляет физически. При неудаче записи metadata
новый файл удаляется. Внезапное завершение ОС между файловой операцией и
commit не является атомарной транзакцией SQLite+FS; отдельного crash-recovery
журнала в этом прототипе нет. Обычный перезапуск/доступ после login проверены.

## Проверки

```bash
TESSDATA_PREFIX="$PWD/local_data/tessdata" .venv/bin/python -m pytest -q
.venv/bin/python tests/integration/package5_http_smoke.py
.venv/bin/python -m pip check
.venv/bin/python -m app.common.export_openapi --check
```

Real OCR tests требуют rus+eng, иначе явно skip. TSV/AI/process fixtures не
выдаются за реальный OCR/AI. HTTP smoke поднимает настоящий Uvicorn только
на loopback, два настоящих аккаунта, свою временную БД/архив, проверяет
извлечение/подтверждение/чужой доступ, перезапускает процесс, проверяет
сохранность сессии и файла, logout/login и физическое удаление. Проектный
local_data не сбрасывается. Проверки инвалидации двух дел используют явно
обозначенные DB-фикстуры ещё отсутствующих пакетов 4/6/7/8.

Фактический итог этого шага: **121 passed, 0 skipped** при указанном
TESSDATA_PREFIX; одно существующее предупреждение Starlette о httpx TestClient.
Отдельный HTTP smoke — status=ok, включая перезапуск процесса.
`pip check` — No broken requirements; общий OpenAPI snapshot is current.
Изменения общих файлов для прохождения этих проверок не потребовались.

## Передача Матвею и общие контракты

Новые общие поля, DDL и endpoints не нужны. Используются его Document,
Field, DocumentFieldsPatch, ConfirmedDocumentFacts, AIResult и протоколы.
AI-адаптер теперь возвращает общий AIResult.output; занятость —
status=unavailable/reason=ai_busy. Дублирующей публичной модели нет.

Матвею нужно подключить router в общем запуске и добавить PDF/OCR/jsonschema
зависимости из файлов пакета в его общий манифест/lock. Пакету 4 — передать
CaseRebuilder и использовать get_confirmed_facts; до подключения пересчёта
старые проекции очищены, ложная готовность не возвращается.
Живая AI-проба через вход Матвея остаётся на его ноутбуке. Платного fallback,
покупки кредитов и передачи credentials нет. Состояние пробы — app/ai/README.md.

## Файлы этого рабочего шага

Новые:
- app/documents/service.py — архив, файлы, ревизии, подтверждение и инвалидация;
- app/documents/router.py — все HTTP-операции и ограниченный multipart;
- app/documents/application.py — запускаемый factory без изменения app/main.py;
- tests/package5/test_archive.py — интеграционные проверки 1+11+5;
- tests/integration/package5_http_smoke.py — настоящий сервер с перезапуском.

Изменены:
- app/documents/{types,readers,worker,extraction,fields}.py — проверка до
  сохранения, лимиты, локальный tessdata, TSV без системных configs, валидация имён;
- app/ai/provider.py — общий AIResult/AIProvider;
- tests/package5/{test_ai,test_documents}.py — общий контракт и реальный OCR;
- app/documents/README.md, app/ai/README.md, demo/README.md — актуальное состояние.

SAMPLE-файлы не перегенерированы. Чужих незакоммиченных изменений на старте
не было. Изменения этого шага не закоммичены и не опубликованы автоматически.

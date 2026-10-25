# Пакет 5: подготовительный шаг до каркаса Матвея

Ветка `codex/vanya-05-documents`. На старте в репозитории были только docs и
`.gitignore`, рабочее дерево было чистым. Пакеты 1 и 11 отсутствуют.
Общие модели, DDL, зависимости в корне, `app/main.py` и `docs/contracts.md`
не изменены. Этот шаг **не является готовым HTTP-архивом**.

## Что работает

`DocumentExtractor.extract(content: bytes, document_type)` — асинхронный вызов
локального обработчика. Имя файла, ожидаемый ответ и manifest образцов не
передаются обработчику. PDF-текст читается pypdf; страницы без текста
растеризуются pypdfium2 под mutex и передаются Tesseract `rus+eng`.
PNG/JPEG декодируются Pillow с учётом EXIF-поворота.

Лимиты: 10 MiB, 10 PDF-страниц, 20 млн пикселей, 100 тыс. символов,
8 MiB распакованного потока PDF-страницы, 60 секунд на процесс. Один процесс
извлечения одновременно для всех экземпляров; конкурирующий запрос получает
`processing_busy`. Тайм-аут/отмена убивают группу процессов, включая OCR;
родитель удаляет временный каталог. Временная копия не заменяет оригинал
архива: постоянное хранение будет подключено после пакета 1.

Результат — внутренний `ExtractionResult`, не публичная Pydantic-модель.
Он содержит кандидаты полей, качество, фактический MIME и число страниц.
Полного OCR-текста и файловых путей в результате нет. У PDF-текста
`confidence=null`; OCR сохраняет минимальную уверенность слов строки в [0,1].
Все кандидаты имеют `user_confirmed=false`, `confirmed_revision=null`.
`extracted` означает только извлечение, а не подтверждение или готовность.
Недостающие/неоднозначные/невалидные поля и низкая уверенность дают
`needs_input`. Повреждённый/зашифрованный/неподдерживаемый файл — `failed`.

Парсер консервативный: строки с известными английскими/русскими метками,
даты ISO или DD.MM.YYYY, десятичные суммы без группировки/экспоненты.
Нераспознанный свободный бланк остаётся для ручного ввода. MRZ, произвольные
банковские таблицы, классификация типа и сравнение с визовыми правилами здесь
не реализованы. Код валюты из трёх букв — лишь извлечённый кандидат;
распознавание ISO-кода и сопоставимость валют проверяет пакет 6.

## Запуск проверки

Из корня репозитория с Python 3.12:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r app/documents/requirements.txt -r app/ai/requirements.txt -r tests/package5/requirements.txt -r demo/requirements.txt
.venv/bin/python -m pytest tests/package5 -q
```

Для реального OCR нужен отдельно установленный Tesseract и оба языка:

```bash
tesseract --list-langs
```

Тесты реального OCR пропускаются с явной причиной, если `rus`/`eng` отсутствуют.
TSV-фикстура проверяет только разбор уверенности и очистку временного файла.
На этом компьютере реальные OCR-тесты пока не пройдены.

Фактический результат этого шага (25 октября 2026): `40 passed, 5 skipped`.
Проверены девять настоящих текстовых PDF, отличия прочитанного содержимого,
ошибочные/зашифрованные/слишком большие файлы, лимиты страниц/пикселей,
отсутствие OCR, конфликт/низкая уверенность полей, тайм-аут, отмена и занятость.
AI-транспорт проверен только фиктивным локальным executable, включая JSON
Schema, запрет API-env, login mismatch, ошибку CLI и очистку после отмены.
`compileall` прошёл. Все 10 PDF отрендерены Poppler и визуально просмотрены
в контактном листе: текст/пометки SAMPLE читаются, наложений/обрезки нет.
Пять пропусков — именно реальный Tesseract на PNG, JPEG и image-only PDF.
Живой AI, HTTP/БД/ownership и сквозная готовность пакета 5 не заявляются.

Пример вызова (в HTTP-обработчике нужно `await`, не `asyncio.run`):

```python
import asyncio
from pathlib import Path
from app.documents import DocumentExtractor

result = asyncio.run(DocumentExtractor().extract(
    Path("demo/documents/good_passport.pdf").read_bytes(), "passport"
))
print(result.to_dict())
```

## Передача Матвею: предлагаемые уточнения общего контракта

Изменения ниже **предложены, не согласованы и не внесены** в общие схемы.
Текущий HTTP-контракт достаточен для начала подключения, новых endpoints нет.

1. Зафиксировать реестр `Field.key` и формат коллекции `fields`:

   | Тип | Предлагаемые ключи |
   | --- | --- |
   | passport | given_name, surname, birth_date, passport_kind, issued_on, expires_on |
   | bank_statement | holder_name, statement_on, balance, currency |
   | insurance | holder_name, starts_on, ends_on, territory, coverage_amount, currency |

   Все значения кандидатов — строка/null; деньги — строка Decimal.
   Даты паспорта здесь названы `issued_on/expires_on`; поля профиля имеют
   `passport_issued_on/passport_expires_on`. Нужен явный mapping в общем слое.
   Источник страницы — 1-based. Ненайденные поля отсутствуют среди кандидатов
   и указаны в quality_issues; не приписывать им несуществующий источник.

2. Уточнить `PATCH /documents/{id}/fields`: например, `expected_facts_revision`
   и список `{key, value, user_confirmed}`. При несовпадении ревизии — 409.
   Рекомендуемая семантика: изменение значения/повторное извлечение увеличивает
   facts_revision и сбрасывает подтверждение изменённых полей; подтверждение
   записывает confirmed_revision текущего значения. Неизменённые поля могут
   сохранить подтверждение только с явной привязкой к текущей ревизии.
   Эту политику и транзакцию реализуем после фиксации схем Матвеем.

3. Адаптер результата: CandidateField -> общий Field; QualityIssue -> Finding
   с `origin=technical`, `severity=verify`, document_ids, field_paths,
   `rule_ref=null`, `resolved=false` и next_action. Необходим mapping кодов в
   HTTP: file_too_large -> 413; unsupported_type -> 415;
   invalid_file/invalid_pdf/invalid_image/encrypted_pdf/page_limit/
   image_too_large/text_limit -> 422; processing_busy -> 409.
   При занятости не перезаписывать существующие поля/состояние документа.
   OCR-unavailable/тайм-аут -> needs_input с ручным вводом, не pass.

4. Общий storage gateway должен проверять owner_id при каждом чтении,
   скачивании, обновлении и удалении; `get_confirmed_facts(owner_id, ids)`
   возвращает только текущие подтверждения с document_id/facts_revision.
   Чужой ID — 404; owner_id приходит из `require_user`, не из тела запроса.
   `extract_document(id)` следует вызывать с доверенным owner-контекстом после
   проверки доступа; по одному ID без контекста его не экспортируем.

5. Нужны атомарные операции сохранения ревизии и отмены зависимостей.
   Правка/повторное извлечение/удаление должны сделать checks/letters stale
   и вызвать `rebuild_case` для **всех** связанных дел. Извлечение не держит
   SQL-транзакцию; перед записью результата повторно проверить ревизию и
   существование документа. Удалённый во время OCR файл не воссоздавать.
   Удаление также снимает case_documents и удаляет оригинал/поля.

6. Ограничение размера нужно применять уже при потоковом multipart-чтении;
   проверка после `read()` не заменяет ограничение upload endpoint. Оригинал
   хранится в закрытом `local_data/uploads` с серверным UUID-именем. Файл
   валидируется до принятия в архив; входной MIME/расширение недостоверны.

HTTP, авторизация, повторное открытие после перезапуска, подтверждение полей,
SQLite и отмена готовности **не проверены**: для них нужен каркас 1/11/4.
Не создаём альтернативные таблицы/сессии или условно успешные заглушки.

## Файлы шага

- `app/documents/{__init__,types,fields,readers,worker,extraction}.py`, этот README,
  `app/documents/requirements.txt`.
- `app/ai/{__init__,provider}.py`, `app/ai/README.md`, `app/ai/requirements.txt`.
- `tests/package5/{test_documents,test_ai}.py`, requirements.txt.
- `demo/generate_documents.py`, `demo/README.md`, requirements.txt;
  `demo/documents/{good,bad,corrected}_{passport,bank_statement,insurance}.{pdf,png}`,
  scan_passport.pdf, photo_passport.jpg, manifest.json.

Источники реализации: [pypdf](https://pypdf.readthedocs.io/en/stable/user/extract-text.html),
[pypdfium2: потокобезопасность](https://pypdfium2.readthedocs.io/en/stable/python_api.html).

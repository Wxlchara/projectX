# Пакет 3: профиль, применимость и сравнение

Реализованы `GET/PUT /api/v1/profile` и `POST /api/v1/comparisons`.
Нужна cookie-сессия; PUT/POST требуют `X-CSRF-Token`. Ответы имеют
`Cache-Control: no-store`. Владельца сервер берёт из сессии, owner_id/id/
revision во входе запрещены. При регистрации уже создан пустой профиль
ревизии 1. Миграция SQLite v1 и новые зависимости не требуются.

## Профиль и ревизии

PUT полностью заменяет поля ProfileInput. Пропуски становятся null;
для редактирования одного поля frontend собирает полный текущий ввод,
исключая серверные id/revision. Повтор идентичного PUT сохраняет ревизию.
Изменённый профиль получает revision+1. Будущая дата рождения и срок
паспорта раньше даты выдачи дают 422, строковое "false" не принимается.

Пример **вымышленного** тела PUT:

```json
{
  "given_name": "SAMPLE",
  "surname": "TOURIST",
  "birth_date": "1995-01-01",
  "citizenship": "RU",
  "residence_country": "RU",
  "has_other_citizenship": false,
  "has_foreign_residence_permit": false,
  "passport_kind": "biometric",
  "passport_issued_on": "2023-01-01",
  "passport_expires_on": "2033-01-01",
  "employment_status": "employed",
  "existing_permissions": []
}
```

В одной короткой транзакции изменение делает собственные cases stale,
ставит stale_reason=profile_changed, очищает projection_json, помечает
связанные check_results/routes/letters stale. Чужие ресурсы не меняются.
Данные документов, привязки, история, текст/edited_by_user писем и прежние
input_signature сохраняются. Старый cases.profile_revision остаётся
версией прежнего расчёта: пакет 4 заменит её при rebuild_case, проверив
актуальность входов. Ручная отметка checklist_actions сохраняется вместе
со старой подписью и не должна автоматически засчитываться для новых входов.

## Сравнение для frontend

Пример POST /comparisons:

```json
{
  "trip": {
    "purpose": "tourism",
    "starts_on": "2027-01-20",
    "ends_on": "2027-01-31",
    "application_country": "RU",
    "accommodation_type": "hotel",
    "funding_type": "self"
  },
  "answers": {"schengen_prior_stays": []}
}
```

Один запрос возвращает `{items: [...]}` для всех 12 направлений из текущего
сохранённого профиля. destination_country из trip при сравнении заменяется
страной каждого сценария; для одного дела evaluate_applicability требует
совпадения страны со сценарием. Порядок каталога не является рейтингом.

У каждого элемента есть destination, profile_revision, applicability,
required_documents, known_required_documents, requirements_complete,
external_actions, route_notes, unknown_conditions. Это отдельные факторы:
объём известного пакета, внешние действия, маршрут и неопределённость.
Все текущие сценарии partial, поэтому полное required_documents=null и
requirements_complete=false. known_required_documents считает только
известно применимые обязательные документы. Null нельзя сортировать как
ноль. Маршруты/транзит/рейсы здесь ещё не проверялись. Процента одобрения нет.

Пример вызова после login/me (csrfToken из SessionResponse):

```javascript
const current = await fetch('/api/v1/profile', { credentials: 'include' });
if (!current.ok) throw (await current.json()).error;
const { id, revision, ...input } = await current.json();
const saved = await fetch('/api/v1/profile', {
  method: 'PUT', credentials: 'include',
  headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
  body: JSON.stringify({ ...input, passport_kind: 'biometric' }),
});
if (!saved.ok) throw (await saved.json()).error;
const response = await fetch('/api/v1/comparisons', {
  method: 'POST', credentials: 'include',
  headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
  body: JSON.stringify({ trip, answers }),
});
const payload = await response.json();
if (!response.ok) throw payload.error;
// payload.items; после PUT сравнение запрашивается заново.
```

## Применимость для пакетов 4/6

```python
profile = request.app.state.profile_service.get(owner)
registry = request.app.state.rule_registry
scenario = registry.get_scenario("FR_TOURISM", version=ruleset_version)
result = request.app.state.applicability_evaluator.evaluate_applicability(
    profile, trip, answers, scenario,
)
# Альтернатива вне HTTP: app.eligibility.service.evaluate_applicability(...).
```

Поддерживается один взрослый на дату начала поездки, гражданин/резидент РФ,
без второго гражданства/иностранного ВНЖ, цель tourism. Известное нарушение
этой области даёт unsupported_profile с причинами и пустыми требованиями.
При неизвестной области/возрасте/датах — needs_input и пустые требования.
Остальные неизвестные поля перечислены в missing_answers как profile.*,
trip.*, answers.*. null не равен false, пустой список истории означает
явное отсутствие поездок. Поля имени не обязательны для этого сравнения.

Далее движок возвращает requirements с applicability/explanation/rule_refs,
barriers, missing_answers и unknown_conditions. Статусы в порядке приоритета:
blocked — известный blocking по проверенному правилу; needs_input — нужны
ответы; needs_verification — fix/verify, источник или покрытие неизвестны;
applicable — нет этих ограничений. У partial-сценария applicable не выдаётся.
Это применимость по анкете: состояние satisfied/готовность документов
определяют пакеты 4/6 по подтверждённым доказательствам.

Условия all/any имеют трёхзначную логику. Например, при funding_type=sponsor
ветка self не требует ответа о предоплате жилья. Непроверенный, просроченный
или конфликтующий источник не создаёт подтверждённый барьер. Замечания
правил имеют rule_ref/version, source_refs и next_action; ограничения
покрытия имеют origin=technical. Документы не читаются и AI не вызывается.

Сроки паспорта считаются календарными месяцами. Французское правило 90/180
проверяет каждый день включительно, с объединением пересекающейся истории.
Соглашения Бразилии/Аргентины не заменены этим алгоритмом: прежние визиты
дают ручную проверку соответствующего периода. Наличие визы/разрешения,
релевантного направлению, требует отдельной сверки, а не молчаливого
освобождения от всего пакета. Денежные пороги/страховка передаются правилами
в пакет 6; баланс, валюта и само доказательство здесь не подтверждаются.

## Дополнительные вопросы

Схема questions публикуется в каждом Scenario: key/title/value_type,
границы/choices и source_ids. Для одного сценария допустимы только его
вопросы; /comparisons принимает объединение вопросов 12 активных сценариев.
Неверный ключ, тип, дата или интервал — 422 invalid_answers без значений
ответов в ошибке. История: до 50 объектов `{starts_on, ends_on}`; прежний
выезд должен быть раньше нового въезда. Всегда можно оставить null.

| Ключ | Тип | Где и зачем |
|---|---|---|
| application_date | YYYY-MM-DD | ES/IN: дата подачи для паспортного срока |
| blank_passport_pages | integer 0…100 | ES/IN/ZA: свободные страницы |
| schengen_prior_stays | stays | FR: прежние пребывания для 90/180 |
| jp_bank_original_available | boolean | JP: доступен банковский оригинал |
| jp_multiple_entry | boolean | JP: нужно дополнительное обоснование многократной визы |
| in_pakistani_origin | boolean | IN: покрытие выбранной e-Visa |
| in_authorised_entry | boolean | IN: разрешённый пункт въезда |
| za_yellow_fever_route | boolean | ZA: условие санитарного подтверждения |
| us_interview_required | boolean | US: подтверждённая необходимость интервью |
| ca_biometrics_requested | boolean | CA: инструкция IRCC о биометрии |
| br_prior_stays / ar_prior_stays | stays | BR/AR: проверка повторных поездок |
| nz_accommodation_prepaid | boolean | NZ: вариант финансового условия при self |

Недостающие ответы выбираются из реально активных обязательных условий;
не нужно задавать пользователю все вопросы сразу. Например, для FR нет
проверенного правила о числе страниц в этом снимке, а для ES нет
автоматического подсчёта истории 90/180. Эти пробелы не заполняются догадкой.

## Проверки и передача

`python -m pytest -q` — 91 тест; в том числе 2/3 и регрессия основы/auth.
`python tests/rules_profile_http_smoke.py` — отдельный Uvicorn, публичные
12 сценариев, настоящие регистрация/login, CSRF, две анкеты/сравнения,
новый процесс и та же БД/cookie. `python -m pip check` и проверка снимка
OpenAPI входят в общий набор команд README. Пакет 2 также проверен в wheel
вне каталога исходников.

В main переносится вся ветка `matvey/02-03-rules-profile` с обоими коммитами.
Ване нужны общие модели/interfaces/OpenAPI и правила, а также контракт
инвалидации для его проверок/маршрутов/писем. Frontend нужны публичные
сценарии/questions и реальные profile/comparisons. Модули Вани и frontend
в этой работе не изменялись. Следующая задача Матвея — пакет 4; пока
реальный архив пакета 5 не подключён, его стык проверяется фикстурами.

# Пакет 2: реестр направлений и правила

Активная версия: `2026-10-25.1`, 12 сценариев `<ISO>_TOURISM` для
FR/ES/JP/IN/EG/ZA/US/CA/BR/AR/AU/NZ. JSON лежит в
`data/rules/destinations/2026-10-25.1`; корневой manifest выбирает активную
версию и перечисляет сохраняемые версии. Данные входят и в Python-wheel.

Все страны имеют требования, условия и отдельный шаблон процесса.
**Покрытие каждого сценария частичное**, `coverage=partial`,
`data_quality=needs_verification`; это не полный официальный список.
Известный базовый режим въезда отделён от полноты требований. Отсутствие
проверенного пункта не означает, что требования нет.

## Фактическая проверка источников

Сопоставление выполнено 25 октября 2026, время фиксации
`2026-10-25T13:03:59Z`. Пересмотр до **1 ноября 2026** — инженерный срок,
а не срок юридической действительности. После него источник становится
needs_verification при чтении; режим въезда также становится неизвестным,
если просрочено его основание. Интернет при запуске приложения не нужен.

| Страна / режим снимка | Открытый первичный источник | Основной предел покрытия |
|---|---|---|
| Франция / visa | [France-Visas РФ](https://france-visas.gouv.fr/ru/russie), [въезд](https://www.france-visas.gouv.fr/en/votre-arrivee-en-france), [FAQ](https://www.france-visas.gouv.fr/en/faq) | Европейская территория, индивидуальный мастер/округ и запись отдельно |
| Испания / visa | [BLS, туристический список](https://blsspainrussia.ru/moscow/spanish/assets/pdf/Tourism_Spanish.pdf) | Консульство: 403; денежный порог 2026 не подтверждён и не применяется |
| Япония / visa | [Туристические документы](https://www.ru.emb-japan.go.jp/itpr_ja/tourism2022.html), [порядок](https://www.ru.emb-japan.go.jp/itpr_ru/visainfo.html) | Место приёма и физические оригиналы требуют отдельного подтверждения |
| Индия / evisa | [Государственный портал](https://www.indianvisaonline.gov.in/evisa/) | Выбран 30-дневный вариант; пункт въезда и дополнительные формальности уточняются |
| Египет / evisa | [FAQ МВД](https://www.visa2egypt.gov.eg/eVisa/FAQ) | Иной способ получения визы и срок допуска не моделируются |
| ЮАР / visa_free | [DIRCO, освобождение](https://dirco.gov.za/uk/visa-countries-waivers/), [посольство, общие условия](https://www.southafrica-embassy.or.kr/eng/visa/visas.php) | Точные санитарные/транзитные исключения и история визитов отдельно |
| США / visa | [Visitor](https://travel.state.gov/content/travel/en/us-visas/tourism-visit/visitor.html), [посты](https://travel.state.gov/content/travel/en/us-visas/visa-information-resources/list-of-posts-niv.html) | CBP: 403; исключение по сроку паспорта и доступность поста не подтверждены |
| Канада / visa | [Гражданства](https://www.canada.ca/en/immigration-refugees-citizenship/services/visit-canada/entry-requirements-country.html), [подача](https://www.canada.ca/en/immigration-refugees-citizenship/services/visit-canada/apply-visitor-visa.html) | Персональный перечень/биометрия определяются IRCC |
| Бразилия / visa_free | [Публикация соглашения](https://www.planalto.gov.br/ccivil_03/_ato2007-2010/2010/decreto/d7271.htm) | Страница посольства: JS/CAPTCHA; повторные поездки сверяются вручную |
| Аргентина / visa_free | [Соглашение](https://tratados.cancilleria.gob.ar/tratado_archivo.php?caso=pdf&id=mqGkng%3D%3D&tipo=kg%3D%3D&tratados_id=kp2mmpo%3D), [DNU 366/2025](https://www.argentina.gob.ar/normativa/nacional/decreto-366-2025-413297/texto) | Введение порядка декларации/страхования не подтверждено для выбранного въезда |
| Австралия / visa | [Home Affairs, необходимость визы](https://immi.homeaffairs.gov.au/entering-and-leaving-australia/entering-australia/can-i-go-to-australia) | Страница потока 600 отдаёт JS-каркас: его конкретные условия оставлены неизвестными |
| Новая Зеландия / visa | [Visitor Visa](https://www.immigration.govt.nz/visas/visitor-visa/), [waiver](https://www.immigration.govt.nz/visit/what-you-need-to-visit-new-zealand/visa-waiver-countries-and-territories/) | Воздушный туристический сценарий; история и индивидуальные ограничения отдельно |

В каждом JSON источники содержат точный URL, authority, locator, краткое
evidence, checked_at/review_after и качество. Неоткрытые страницы имеют
attempted_at/notes и **checked_at=null**. Поисковый текст и исторический
отчёт не выдаются за проверенную страницу. Страховка/выписка не добавляются
во все страны автоматически; рекомендованное отличается от обязательного,
а неизвестное обязательное условие сохраняется для последующей проверки.

## API и Python-интерфейс

Публичные `GET /api/v1/destinations` и `GET /api/v1/scenarios/{scenario_id}`
работают без сессии. Список — `{items: [...]}`, порядок каталога не является
рейтингом. В сценарии есть questions, entry_source_ids, coverage и
unverified_conditions; frontend должен показывать качество вместе с режимом.

`request.app.state.rule_registry` — FileRuleRegistry:

```python
scenario = registry.get_scenario("FR_TOURISM")
ruleset = registry.get_ruleset(scenario.version)
sources = registry.get_sources(["FR_RU", "FR_FAQ"])
```

Возвращаются независимые Pydantic-снимки. get_ruleset(version) поддерживает
версии, перечисленные в manifest; get_scenario и get_sources читают активную
или конкретную сохранённую через `version="..."`. Для старого снимка дела
все три чтения должны использовать одну ruleset_version.
Неизвестная версия/сценарий/источник — 404. Для воспроизводимых проверок у
get_scenario/get_ruleset есть необязательный keyword `as_of=date(...)`.
Новая версия добавляется рядом с прежней, затем переключается active_version
и перезапускается backend. Старую версию для существующих дел не удалять.

Загрузчик отказывает при неизвестном kind/поле, неподходящих параметрах,
битых ссылках, дубликатах, несовпадающих версиях, пустой/слишком глубокой
логике и выходе за каталог. applies_if — только all/any и eq/in/exists/gte/lte;
код из JSON не исполняется.

Ограниченный реестр в `app/rules/schema.py`:

| kind | Смысл / параметры |
|---|---|
| requirement | Наличие требования; параметры пустые |
| assertion | condition, expected, message, next_action, severity |
| passport_validity | anchor, календарные months и/или days, severity |
| passport_max_age | anchor, календарные years, allow_anniversary (по умолчанию true) |
| stay_limit | days, window_days, history_question, history_mode |
| insurance_coverage | territory, minimum_cover или null, covers_trip |
| minimum_funds | minimum или null, period; calculation=manual |
| manual_review | message, next_action |

Пакет 3 оценивает заявленные профиль/поездку. Пакет 6 применяет применимые
правила к подтверждённым документным фактам и проверяет страховку/деньги.
Параметр manual не разрешает придумывать курс, округление месяца или порог.
У Франции точная десятая годовщина выдачи уже выходит за «моложе 10 лет»;
в определении явно записано allow_anniversary=false.
Для новой разновидности правила сначала расширить валидатор у Матвея,
затем данные/проверки у владельца модуля; молча пропускать kind нельзя.

## Проверки

Проверены загрузка 12 стран, ветки процесса, различие обязательных документов,
неизвестные источники, истечение срока пересмотра, сохранность снимков и
отказ на повреждённых/исполняемых определениях. Команда:
`python -m pytest -q tests/test_rules.py`.

Общие модели дополнены Question, AppliedRequirement, сведениями о проверке
источников и полноте. Состояние `blocked` предусмотрено для известного
формального барьера; типы занятости/финансирования/проживания перечислены
в OpenAPI. Результат пакета 3 передаёт применимость каждого требования,
известное число документов отдельно от неизвестного полного числа.

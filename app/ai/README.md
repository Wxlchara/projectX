# AIProvider: контракт и состояние пробы

`await AIProvider.generate(task, facts, output_schema) -> AIResult`.
Пакет 8 использует тот же экземпляр/интерфейс. Общих HTTP-схем не создаёт.

- task: `smoke_test`, `explain_findings` или `cover_letter`; инструкции заданы
  кодом, facts передаются как JSON `untrusted_facts` через stdin.
- output_schema: серверная JSON Schema Draft 2020-12 с корневым object;
  только локальные `$ref`; проверка схемы, JSON и ответа выполняется Python
  jsonschema. Не принимать схему/задачу из произвольного HTTP-запроса клиента.
- status: available / unavailable / busy; data — объект только при available.
  reason — безопасный машинный код; model — настроенная модель.
- generation_mode=ai только при валидном ответе CLI. При недоступности
  generation_mode=null, data=null. Если пакет 8 создаст локальный шаблон,
  он сам помечает Letter.generation_mode=template.
- Кэш не реализован: from_cache всегда false. Повторов нет. При будущем
  кэше ключ обязан учитывать входы, модель, schema и версию шаблона.

`CodexCLIProvider()` по умолчанию выключен. После фактической пробы на ноутбуке
Матвея владелец конфигурации явно задаёт enabled/model. Название модели не
подставляется автоматически, доступность любой API-модели не предполагается.

Вызов: список аргументов, stdin, отдельная временная папка, read-only sandbox,
never approvals, ephemeral, ignore-user-config/ignore-rules; shell, apps,
hooks, multi_agent и web_search выключены. `forced_login_method=chatgpt` и
`model_provider=openai`; перед генерацией проверяется `codex login status`.
API-ключи и provider URLs из окружения не передаются. Собственные credentials
CLI не читаются/не копируются. Нет переключения на другой provider или платный
API, нет авторизации от имени аккаунта VisaPilot.

Один AI-вызов для всех экземпляров, общий дедлайн login+exec <=60 секунд,
вход вместе со schema <=64 KB, выход <=256 KB. Отмена/timeout убивают группу
процессов и очищают временную папку. Сбой возвращает безопасный код без
stderr, трассировки и локальных путей. Managed policy, запрещающая необходимые
настройки, должна привести к недоступности, без ослабления sandbox.

## Фактически проверено 25 октября 2026

На компьютере Вани `codex --version`: `0.163.0-alpha.11`;
`codex exec --help` подтверждает используемые ключи. `login status` и живой
AI-запрос здесь не запускались. Запись о входе на ноутбуке Матвея из ADR
не считается проверкой этого окружения.

`tests/package5/test_ai.py` запускает **вымышленный локальный executable**,
который отдаёт строку `LOCAL TRANSPORT FIXTURE, NOT AI`. Эти тесты подтверждают
транспорт, валидацию, недоступность, тайм-аут, очистку, ограничение параллелизма
и отсутствие передачи API-env, но не ответ AI и не пригодность модели.

## Проба на ноутбуке Матвея

После подключения зависимостей и выбора модели, доступной его подписке,
выполнить один минимальный `smoke_test` с object schema (например
`message: string`, required, additionalProperties=false) через адаптер.
Зафиксировать: CLI version, выбранную модель, login status=ChatGPT,
AIResult.status, валидность фактического JSON, длительность. Не сохранять
credentials или сырой служебный stderr. При timeout/лимите остановиться,
не покупать кредиты и не выдавать локальную фикстуру за результат пробы.

Фактическое выполнение этой пробы остаётся у Матвея. Поэтому пакет 5
пока не заявляется завершённым и демонстрация реального AI не пройдена.

Официальные источники проверены 25 октября 2026:
[неинтерактивный CLI, JSON Schema и stdin](https://learn.chatgpt.com/docs/non-interactive-mode),
[аутентификация](https://learn.chatgpt.com/docs/auth),
[настройки](https://learn.chatgpt.com/docs/config-file/config-reference).

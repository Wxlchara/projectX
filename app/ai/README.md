# AIProvider: общий контракт и состояние живой пробы

`await AIProvider.generate(task, facts, output_schema) -> AIResult` теперь
использует модели/протоколы из app.common, без параллельной версии схемы.

- task: smoke_test, explain_findings или cover_letter. Инструкции заданы
  серверным кодом; facts передаются как JSON untrusted_facts через stdin.
- output_schema: серверная JSON Schema Draft 2020-12, корень object,
  локальные refs; Python повторно проверяет схему, JSON и результат.
- AIResult.status: available/unavailable; output содержит объект только при
  available; reason — безопасный код; model — явно настроенная модель.
- Занятость: unavailable / ai_busy. Нет нового enum busy в общем контракте.
- Кэша нет: from_cache=false. Повторов нет. При добавлении кэша ключ должен
  учитывать факты, модель, schema и версию шаблона.
- Generation mode задаёт потребитель: пакет 8 помечает свой локальный
  шаблон как template, а валидную живую генерацию как ai. Сам AIResult не
  имеет generation_mode, в соответствии со схемой Матвея.

CodexCLIProvider() по умолчанию выключен. enabled/model задаются владельцем
окружения после пробы на его ноутбуке; доступность произвольной модели
не предполагается. При импорте/старте/извлечении документов AI не вызывается.

CLI запускается списком аргументов, без shell, с JSON stdin, в отдельной
временной папке: read-only sandbox, never approvals, ephemeral,
ignore-user-config/ignore-rules. Shell/apps/hooks/multi_agent/web_search
выключены. forced_login_method=chatgpt, model_provider=openai; перед exec
проверяется codex login status. API-ключи/provider URLs не передаются через
окружение. Приложение не читает/копирует credentials CLI.

Общий для всех экземпляров lock, deadline login+exec <=60 секунд, вход
со schema <=64 KB, выход <=256 KB. Timeout/отмена завершают группу процессов
и удаляют временную папку. Сбой возвращает безопасный reason без stderr,
токенов и путей. Нет перехода на платный API/другую модель, покупки кредитов
и циклических повторов при лимите/ошибке входа.

## Что проверено

На подготовительном шаге здесь проверялись только codex --version
(0.163.0-alpha.11) и exec --help. Живой запрос через вход Матвея на этом
компьютере не выполнялся. Его авторизация сюда не переносилась.

Тесты запускают **вымышленный локальный executable** с ответом
LOCAL TRANSPORT FIXTURE, NOT AI. Они проверяют транспорт, JSON Schema,
отсутствие передачи API-env, ошибки входа/CLI, timeout, отмену, очистку и
параллелизм. Это не подтверждение реальной генерации/доступности модели.

## Следующее действие Матвея

После подключения зависимостей выбрать доступную его подписке модель,
создать CodexCLIProvider(enabled=True, model=...), выполнить один smoke_test
на синтетических фактах с минимальной object schema. Зафиксировать CLI
version, имя модели, ChatGPT login status, фактический валидный JSON и
длительность. При лимите/timeout сообщить состояние, не подменять ответ
локальной фикстурой и не покупать кредиты. Запуск остаётся на его ноутбуке.

Официальные источники проверены при подготовке пакета:
[CLI/stdin/JSON Schema](https://learn.chatgpt.com/docs/non-interactive-mode),
[аутентификация](https://learn.chatgpt.com/docs/auth),
[настройки](https://learn.chatgpt.com/docs/config-file/config-reference).

# Пакет 11: аккаунты и сессии

Ветка `matvey/11-auth` создана от актуальной main с пакетом 1. Передавать
в main весь коммит: app/auth, подключение в main, общие зависимости/модель
Credentials, pwdlib/Argon2 в зависимостях, OpenAPI, тесты и документацию.
Схема SQLite v1 осталась прежней. Следующий пакет Матвея — 2; Ваня может
подключать настоящий вход к своему пакету 5 сразу после получения 11.

## Что работает

Все пути после `/api/v1`:

| Запрос | Успех | Поведение |
|---|---|---|
| POST /auth/register | 201 User | Создаёт аккаунт/пустой профиль, не выполняет вход |
| POST /auth/login | 200 SessionResponse | Проверяет пароль, выдаёт новую cookie и CSRF |
| GET /auth/me | 200 SessionResponse | Восстанавливает user и CSRF из действующей сессии |
| POST /auth/logout | 204, без тела | Требует CSRF, отзывает сессию и удаляет cookie |

Username — 3–64 ASCII-символа: буквы/цифры, `_`, `.`, `-`, первый символ
буква или цифра. Возвращается в нижнем регистре. Пароль — 8–128 символов,
не обрезается; Argon2id через pwdlib. В модели Credentials он SecretStr;
валидация/логи не показывают его исходное значение. В ответах нет хеша
пароля или bearer-токена; CSRF по контракту возвращается login/me.

Session cookie: `visapilot_session`, host-only, Path=/api/v1, HttpOnly,
SameSite=Lax, Max-Age=43200, expires через 12 часов. Secure=false для
синтетического HTTP-демо, true при HTTPS. Успешный login отзывает старую
предъявленную cookie и всегда создаёт новую; ошибочный login сохраняет
предыдущую сессию. Регистрация другого аккаунта не переключает вход.

В БД сохраняется SHA-256 случайного 256-битного bearer-токена и хеш CSRF.
CSRF детерминирован HMAC-SHA256 от cookie-токена с отдельной меткой;
он связан с конкретной сессией и воспроизводится после перезапуска без
сохранения открытого bearer-токена/дополнительного server secret.
Logout ставит revoked_at: повтор старой cookie даёт 401. GET /me не
продлевает срок действия. Пароли проверяются в рабочем потоке, вне
транзакции SQLite и основного event loop HTTP.

## Origin и frontend

Перед запуском скопировать `.env.example` в `.env` и задать точные browser
origins в VISAPILOT_ALLOWED_ORIGINS. Для общей страницы/API на ноутбуке:
`http://127.0.0.1:8000`; для dev proxy добавить, например,
`http://localhost:5173`; для LAN — `http://<IP ноутбука>:8000`.
Порт и схема имеют значение, wildcard и URL с путём не допускаются.
Не принимать client owner_id, не заменять Origin заголовками Host/Forwarded.
CORS не включён: frontend проксирует `/api`, в демо API и сайт имеют один origin.

Для register/login Origin обязателен, даже в curl/httpx. Браузер отправляет
его сам при POST; задавать Origin вручную в JavaScript не нужно.
Для авторизованных POST/PATCH/PUT/DELETE обязателен X-CSRF-Token. Если
Origin передан, он также проверяется; CLI с правильным CSRF может его опустить.

```javascript
async function jsonRequest(path, options = {}) {
  const response = await fetch(`/api/v1${path}`, {
    ...options, credentials: 'include',
  });
  if (response.status === 204) return null;
  const payload = await response.json();
  if (!response.ok) throw payload.error;
  return payload;
}

// Регистрация не выполняет вход.
await jsonRequest('/auth/register', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username, password }),
});
let session = await jsonRequest('/auth/login', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username, password }),
});

// После обновления страницы восстановить user и csrf_token.
session = await jsonRequest('/auth/me');

// Тот же заголовок нужен для изменений дел, документов, писем, событий.
await jsonRequest('/auth/logout', {
  method: 'POST', headers: { 'X-CSRF-Token': session.csrf_token },
});
session = null;
```

Bearer-cookie доступна браузеру и серверу; JavaScript её не читает.
CSRF хранить в памяти frontend и восстанавливать через /me. Авторизационные
ответы с данными имеют Cache-Control: no-store. Пароль и cookie не логировать.

Ошибки имеют прежний envelope `{error: {code, message, details}}`:

| Статус / code | Действие frontend |
|---|---|
| 401 unauthenticated | Открыть вход; сессия отсутствует/истекла/отозвана |
| 401 invalid_credentials | Показать ошибку входа без уточнения существования аккаунта |
| 403 origin_not_allowed | Проверить origin в конфигурации/proxy, не повторять вход циклом |
| 403 csrf_failed | Восстановить контекст через /me; не повторять изменение молча |
| 409 username_taken | Предложить другое имя |
| 422 validation_error | Проверить длины/формат полей |
| 503 module_not_ready | Показать недоступность ещё не реализованного пакета |

## Интерфейс для Вани и остальных пакетов

Оба прежних импорта app.common.dependencies совместимы; удобный общий вход:

```python
from typing import Annotated
from fastapi import Depends, Request
from app.auth.dependencies import (
    require_user, require_csrf, require_owned,
    require_owned_case_document, ResourceKind,
)
from app.common.models import UserContext

def read_document(document_id, request: Request,
                  owner: Annotated[UserContext, Depends(require_user)]):
    with request.app.state.database.connection() as connection:
        row = require_owned(connection, owner, ResourceKind.DOCUMENT, document_id)
    # row — внутренняя запись, не HTTP-ответ: storage_key не отдаётся клиенту.
    ...
```

На чтении — Depends(require_user), на изменении — Depends(require_csrf).
Обе возвращают UserContext с UUID user_id, автоматически документируют
cookie в OpenAPI; изменение также документирует X-CSRF-Token.
Реальный AuthService подключён create_app по умолчанию. Для подключения
роутера достаточно `create_app(feature_routers={5: documents_router})`;
дополнительный пользователь/пароль в модуль не передаётся.

Для изменения и проверки владельца использовать одну короткую транзакцию:

```python
with database.transaction() as connection:
    case, document = require_owned_case_document(
        connection, owner, case_id, document_id,
    )
    # INSERT/UPDATE со связанными id/owner; без OCR, AI и await.
```

require_owned поддерживает PROFILE, CASE, DOCUMENT, CHECK_RESULT, ROUTE,
LETTER, EVENT. Для ROUTE идентификатор — case_id. У вложенного ресурса
владелец проверяется JOIN с cases. Для неизвестного и чужого ID одинаковый
404 not_found, без данных о наличии ресурса. Два объекта привязки
проверяются независимо; составной FK остаётся дополнительной защитой.
До пакета 3 профиль в БД — ProfileInput с null-ответами, revision=1;
публичные id/revision формируются из столбцов таблицы.

## Выполненные проверки и следующий стык

```bash
python -m pytest -q
python tests/auth_http_smoke.py
python -m pip check
python -m app.common.export_openapi --check
```

pytest проверяет реальные Argon2-пароли двух пользователей, дубликаты,
одинаковую ошибку неправильного пароля/неизвестного имени, Origin,
cookie и CSRF, ротацию/отзыв/истечение, сохранение сессии после нового
lifespan, отсутствие пароля/токена в ответах и работу health при хешировании.
Ownership проверен для всех поддержанных ресурсов и тестовыми HTTP-роутерами
скачивания/привязки с реальными сессиями и сохранёнными записями SQLite.

`auth_http_smoke.py` поднимает настоящий Uvicorn на временном loopback-порту,
создаёт два вымышленных аккаунта, выполняет login/me/CSRF, перезапускает
процесс с той же БД и проверяет logout/запрет повтора старой cookie.
Он удаляет только собственный временный каталог, не local_data проекта.

Реальные HTTP-адаптеры пакетов 4/5 пока отсутствуют в main. В доступной
ветке Вани есть извлечение и AI-заготовки без HTTP-архива. Поэтому следующий
совместный стык после подключения 5: вход A → загрузка → скачивание;
B получает 404 на файл A, после logout A — 401. После 4A повторить попытки
привязать чужой документ/дело. Эти проверки не выдаются за уже пройденную
интеграцию с реализациями соседних пакетов. В модули Вани изменений нет.

Технические источники: [pwdlib](https://frankie567.github.io/pwdlib/guide/),
[FastAPI cookies](https://fastapi.tiangolo.com/advanced/response-cookies/),
[OWASP CSRF](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).

# Модули Server Control Web

Модуль — папка в `modules/`. Минимальный модуль — один файл `module.json`; обычно добавляют `main.py` (серверная часть) и `web/ui.js` (страница в интерфейсе). Рабочий образец со всеми возможностями — [`modules/hello-example`](../modules/hello-example).

> **Модуль — это код с полными правами панели.** Он выполняется в том же процессе, видит всё (`api.app`, файловую систему, ОС) и может изменить что угодно: сайт, API, авторизацию, систему. Ограничений нет — это сделано намеренно. Ставьте только доверенные модули.

```
modules/my-module/
├── module.json      манифест (обязателен)
├── main.py          серверная часть: def setup(api) / def teardown(api)
└── web/
    ├── ui.js        клиентская часть (грузится после входа)
    └── style.css    стили
```

## module.json

```json
{
  "id": "my-module",
  "name": "Мой модуль",
  "version": "1.0.0",
  "description": "Что делает модуль",
  "author": "Вы",
  "icon": "rocket",
  "order": 100,
  "roles": ["panel", "node"],
  "platforms": ["windows", "linux"],
  "min_core": "1.0.0",
  "requires": [],
  "default_enabled": false,
  "ui": "web/ui.js",
  "css": ["web/style.css"],
  "nav": {"id": "my", "title": "Мой раздел", "icon": "rocket", "page": "my"}
}
```

| Поле | Описание |
|---|---|
| `id` | Совпадает с именем папки: `a-z`, `0-9`, `_`, `-`. |
| `roles` | Где модуль работает: `"panel"` (хостинг и нода управления), `"node"` (нода). Модуль только для панели — `["panel"]`. |
| `platforms` | `windows`, `linux`. На несовместимой системе модуль не загружается, причина видна в *Настройки → Модули*. |
| `requires` | Список `id` модулей, которые загружаются раньше. |
| `default_enabled` | Включён ли модуль до явного решения администратора (у установленных из zip всегда выключен). |
| `ui`, `css` | Файлы из `web/`, подключаются в интерфейс. |
| `nav` | Пункт меню (вкладка в шапке). Один объект или список. `page` — id страницы, зарегистрированной в `ui.js`. |
| `icon`, `order` | Иконка (имя из `SCC_ICONS`) и порядок в меню (меньше — левее). |
| `entry` | Имя серверного файла, по умолчанию `main.py`. |

## Серверная часть (`main.py`)

```python
def setup(api):      # при включении модуля
    ...
def teardown(api):   # при выключении (необязательно)
    ...
```

Всё, что регистрируется через `api.*`, **автоматически снимается при выключении** модуля — маршруты, хуки, пункты меню, подмены (`api.patch`).

### Объект `api`

| Член | Что делает |
|---|---|
| `api.app` | Всё приложение: `app.config`, `app.auth`, `app.modules`, `app.updater`, `app.routes`, `app.log`… |
| `api.id`, `api.dir`, `api.data_dir` | id модуля, его папка, личная папка данных `data/modules/<id>/`. |
| `api.config`, `api.save_config()` | Настройки модуля — словарь, хранится в `data/modules/<id>.json` (права 0600). |
| `api.log` | Логгер (`api.log.info(...)`) → консоль и `data/logs/scc.log`. |
| `api.is_windows`, `api.platform` | `"windows"` / `"linux"`. |
| `api.run(args, timeout=30, shell=False, input=None, cwd=None, env=None)` | Запустить команду ОС → `{"code", "out", "err"}`. Никогда не бросает исключений. |
| `api.route(method, path, handler=None, access="admin")` | Маршрут `/api/m/<id>/<path>`. Декоратор или вызов. |
| `api.raw_route(method, path, handler=None, access="admin", prio=0)` | Маршрут по **любому** пути; `prio > 0` перекрывает системные; `method="*"` — любой метод. |
| `api.hook(event, fn=None, prio=0)` | Подписка на событие (см. ниже). |
| `api.add_nav(id, title, icon, page, order, badge)` | Пункт меню из кода (вместо `nav` в манифесте). |
| `api.add_script(url)`, `api.add_style(url)` | Подключить скрипт/стиль (в т.ч. из другого модуля: `/m/<id>/web/...`). |
| `api.inject_head(html)` | Добавить HTML в `<head>` главной страницы. |
| `api.patch(obj, name, replacement)` | Подменить любой атрибут/функцию ядра; вернёт оригинал; откатится при выключении. |
| `api.on_unload(fn)` | Выполнить `fn` при выключении модуля (остановить потоки и т.п.). |

Пути в маршрутах: `/api/m/files/<name>/logs`, `<path:rest>` захватывает остаток пути с `/`. Параметры — в `req.params`.

### Маршруты и права

`access`: `"public"` (без входа), `"user"` (любой вошедший, в т.ч. «просмотр»), `"admin"` (по умолчанию). Для записи (POST/PUT/DELETE) от браузера дополнительно требуется CSRF-заголовок — клиентский `SCC.api` ставит его сам.

Обработчик получает `req` и возвращает `dict`/`list` (JSON), `str`/`bytes`, `(данные, статус)`, `None` (`{"ok": true}`) или `Response`:

```python
from scc.server import HTTPError, Response

@api.route("GET", "ping", access="user")
def ping(req):
    return {"pong": True, "user": req.user["name"]}

@api.route("PUT", "upload")
def upload(req):
    with open(path, "wb") as f:
        req.stream_to(f)                      # потоковая загрузка больших файлов
    raise HTTPError(409, "Уже существует")    # ответ-ошибка

# файл, текст, HTML:
Response.send_file(path, download_name="a.zip"); Response.text("ok"); Response.html("<b>x</b>")
```

`req`: `method`, `path`, `arg("name")`, `query`, `raw_query`, `headers`, `cookies`, `ip`, `user` (`{"name", "role", "via"}`), `params`, `json()`, `body(limit)`, `read(n)`, `stream_to(fp)`, `content_length`.

### События (`api.hook`)

| Событие | Аргументы | Что можно |
|---|---|---|
| `request` | `req` | Вернуть `Response` — запрос обработан вами, маршрутизация пропускается. Вернуть `None` — продолжить. |
| `response` | `resp, req` | Вернуть изменённый `Response` (заголовки, тело) — для **любых** ответов. |
| `boot` | `data, req` | Изменить данные, которые интерфейс получает при загрузке (меню, имя, любые поля). |
| `head_html`, `page_html` | `html` | Изменить `<head>` / всю главную страницу. |
| `login`, `logout` | `username[, ip]` | Уведомление. |
| `setup_done`, `startup`, `shutdown`, `config_saved`, `update_available` | — | Уведомления. |

`prio` — порядок вызова (больше — раньше). Исключения в хуках логируются и не ломают панель.

### Примеры «изменить всё»

```python
# Свой маршрут вместо системного
api.raw_route("GET", "/api/boot", my_boot, access="public", prio=10)

# Менять функцию ядра: двухфакторка, LDAP, аудит...
orig = api.app.auth.verify
api.patch(api.app.auth, "verify", lambda name, pw: orig(name, pw) or ldap_check(name, pw))

# Править любой ответ
@api.hook("response")
def add_csp(resp, req):
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    return resp

# Системные действия
api.run(["systemctl", "restart", "nginx"])
```

## Клиентская часть (`web/ui.js`)

Скрипт загружается после входа, до отрисовки интерфейса. Глобальный объект `SCC`:

```js
(function () {
  var h = SCC.h, icon = SCC.icon, api = SCC.mod('my-module');

  SCC.registerPage('my', {                 // id из nav.page
    render: function (el, ctx) {           // el - контейнер страницы
      el.appendChild(h('div', { class: 'Box' },
        h('div', { class: 'Box-header' }, h('span', { class: 'Box-title' }, icon('rocket'), 'Привет')),
        h('div', { class: 'Box-body' }, 'Содержимое')));
      return api.get('ping').then(function (r) { /* ... */ });   // можно вернуть Promise или функцию очистки
    }
  });
})();
```

| API | Описание |
|---|---|
| `SCC.registerPage(id, {render(el, ctx)})` | Страница. `ctx`: `args` (части пути после страницы), `query` (`URLSearchParams`), `node`, `onLeave(fn)` (очистка таймеров), `isSeq()` (страница ещё активна?), `refresh()`. |
| `SCC.mod(id, local)` | Клиент API модуля: `.get(path, query)`, `.post(path, json, query)`, `.put(path, body, query, type)`, `.del(path, query)`, `.url(path, query)`. **Запросы идут на выбранную в шапке ноду**; `local=true` — всегда на эту панель. |
| `SCC.api(url, {method, json, body, type, raw})` | Низкоуровневый запрос (сам ставит CSRF, 401 → экран входа). `SCC.url(path)` добавляет префикс текущей ноды. |
| `SCC.h(tag, attrs, ...children)` | Создание DOM: `h('button', {class: 'btn', onclick: fn}, 'Текст')`. |
| `SCC.icon(name, size)` | Иконка Octicons. Свои: `SCC_ICONS['my'] = '<path d="…"/>'` (16×16). |
| `SCC.toast(msg, kind)`, `SCC.err(e)` | Уведомление (`info`/`success`/`error`). |
| `SCC.dialog({title, body, actions, size})`, `SCC.confirm()`, `SCC.prompt()` | Окна. |
| `SCC.menu(button, getItems)`, `SCC.flash(kind, content)`, `SCC.blank(icon, title, text)`, `SCC.copy(text)`, `SCC.busy(btn, fn)` | Меню, плашки, пустые состояния, буфер обмена, блокировка кнопки на время операции. |
| `SCC.addSettingsTab({id, title, icon, render(el, ctx)})` | Вкладка в *Настройках*. |
| `SCC.mount('header-right' \| 'page-actions', element)` | Элемент в шапке / в панели действий страницы. |
| `SCC.menuPlus.push({label, icon, onclick})` | Пункт в меню «+». |
| `SCC.addNav({id, title, icon, page})` | Пункт меню из JS. |
| `SCC.on('ready' \| 'route', fn)` | События интерфейса. |
| `SCC.state` | `user`, `boot`, `node`, `nodes`. `SCC.isAdmin()`, `SCC.fmt` (`bytes`, `ago`, `duration`…). |

Стили — классы в духе GitHub Primer: `Box`, `Box-header`, `Box-row`, `btn`, `btn-primary`, `form-control`, `Label`, `Counter`, `flash`, `UnderlineNav`… Цвета — CSS-переменные (`--bg`, `--fg`, `--accent`, `--border`, `--success`…), поэтому светлая и тёмная темы работают сами. Модуль может переопределить любой стиль или DOM — интерфейс не изолирован.

Content-Security-Policy разрешает только скрипты и стили с того же origin (`/m/<id>/web/...`): инлайн-`<script>` не сработает, пишите код в файлах модуля.

## Удалённые ноды

Прокси панели переправляет любой `/node/<id>/api/...` на ноду. Поэтому:

- модуль, установленный на ноде, **сразу** доступен из панели через `SCC.mod('id')` — ничего дополнительного писать не нужно;
- пункты меню модулей, которых нет на выбранной ноде, скрываются автоматически;
- прокси доступен только администраторам панели; на ноде запросы приходят с токеном и считаются административными.

## Установка и распространение

- Папка в `modules/` или zip с `module.json` в корне (или в единственной вложенной папке): *Настройки → Модули → Установить модуль* (файл или ссылка).
- Установленный модуль выключен, пока администратор его не включит; включение и выключение происходят без перезапуска (меню и страницы появляются после обновления страницы браузера).
- Обновление панели заменяет только модули, которые есть в релизе (встроенные). Ваши модули не трогаются.
- Ошибка в `setup()` не роняет панель: модуль помечается «Ошибка», регистрации откатываются, причина видна в списке.

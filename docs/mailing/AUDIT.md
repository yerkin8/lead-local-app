# Аудит перед модулем рассылки (этап 1)

Дата: 2026-10-30. Только чтение. Значения ключей, телефонов и ID не выводились; в отчёте только маски.

## 0. Исходное состояние кода

- Ветка `claude/nostalgic-stonebraker-3e1a25` создана от `b0991c9`, но рабочий продукт (этап 8: ссылки «Проверить ТГ», галерея, установщик, `stage9.js`) лежал в основной папке **незакоммиченным** (32 изменённых и ~40 новых файлов, даты 2026-10-17). Сборка `dist/LeadEqualMoney` (2026-10-18) собрана из этого состояния.
- Это состояние скопировано в ветку отдельным коммитом `15fae87` («snapshot»). Основная папка не менялась.

## 1. Desktop и Mini App

| Что | Факт | Источник |
|---|---|---|
| Desktop-процесс | Один процесс Python: pywebview 6.2.1 (WebView2, `gui="edgechromium"`), мост `js_api=Bridge`, без HTTP-сервера и без портов | `app/desktop.py:1`, `app/desktop.py:627` |
| Фоновая работа | Поток `LocalWorker` (поиск и медиа через Selenium) | `app/desktop_services.py`, `app/desktop.py:532` |
| Один экземпляр | Блокировка `msvcrt.locking` на `%APPDATA%\LeadEqualMoney\desktop.lock` | `app/desktop.py:488-506` |
| Данные | `%APPDATA%\LeadEqualMoney\`: `app.sqlite3` (WAL), `logs\app.log` (3 МБ × 3), `media\`, `webview\`, `window.json`, `backups\` | `app/desktop.py:204-235`, `app/desktop.py:253-258` |
| Навигация окна | Переходы за пределы `index.html` блокируются; внешние ссылки только через `open_external` | `app/desktop.py:169-178`, `app/desktop.py:474-484` |
| Mini App (исходный) | FastAPI `app/api.py` + бот `app/bot.py` (Bot API) + `app/worker.py`. Доступ — подпись `initData` + `OWNER_TELEGRAM_ID` | `app/api.py:31-55` |
| Адрес Mini App | Uvicorn в контейнере на `0.0.0.0:8000`, наружу опубликован только `127.0.0.1:8000`; systemd — `--host 127.0.0.1`; Caddy/nginx проксируют на `127.0.0.1:8000` | `Dockerfile:24`, `compose.yaml:7`, `deploy/lead-app.service:12`, `deploy/Caddyfile:3` |
| Mini App на сервере | Отдельный репозиторий `lead-mini-app` (Docker, `/opt/lead-mini-app`). Код рассылки там тот же `app/telegram_sender.py` | `lead-mini-app/app/telegram_sender.py` (отличие от parserYand — 2 строки, поиск шаблона) |
| Portable-сборка | `build.ps1`: чистый `.build-venv` → `requirements-exe-lock.txt` → PyInstaller onedir `packaging/LeadEqualMoney-onedir.spec` → `dist\LeadEqualMoney\LeadEqualMoney.exe` | `build.ps1:63-91` |
| Ярлык | `create_shortcut.ps1`: ярлыки на рабочем столе и в «Пуске» | `create_shortcut.ps1` |
| Прочие сборки | Inno Setup `installer/LeadEqualMoney.iss`, `scripts/make_installer.ps1`; экспорт исходников `publish_export/` (белый список, скан `scripts/scan_publish_export.py`) | `reports/8_export.md` |

## 2. База организаций

- Таблица `businesses`: `phones` (JSON-список строк), `socials` (JSON `[{url,label}]`), `website`, `site_type`, `site_engine`, `status` (CRM), `address`. Город и ниша — `business_tags`. Ссылки карточки — `links` (desktop). `app/db.py:112-118`, `app/db.py:259-262`.
- Отдельного поля username нет. Username берётся из ссылки `t.me/<name>` в `socials` (`app/telegram_sender.py:282-293`).
- Тип сайта: `domain.classify_site()` при парсинге → `no_site` / `weak_site` / `unavailable` / `other` (`app/domain.py:67-82`).
- Подсказка скрипта по типу сайта: `script_templates.FIRST_BY_SITE_TYPE` + `Bridge.business_scripts` + `domain.render_template` (переменные `{название} {ниша} {город} {тип_сайта}`) — `app/script_templates.py:60-64`, `app/desktop.py:375-387`, `app/domain.py:93-103`. В `lead-mini-app` есть более новая таксономия (`docs/rewrite/TAXONOMY.md`, `app/script_templates.py`, `app/script_texts.py`), но в parserYand её нет.
- Стоп-лист и согласия уже есть: `suppressions`, `contact_permissions` (`app/db.py:159-172`). Старые таблицы рассылки: `campaigns`, `campaign_recipients`, `telegram_send_attempts`, `business_telegram` (`app/db.py:148-185`).
- Статистика: `db.stats()` по `status_events` (`app/db.py:1131-1149`).
- Реальная база (`%APPDATA%\LeadEqualMoney\app.sqlite3`, только чтение, только счётчики): 40 организаций; телефон у 40, мобильный (+7 9…) у 34; любая ссылка t.me у 32, ссылка вида `t.me/<username>` у 18 (часть может быть каналами); VK 21, WhatsApp 27, сайт 17. Типы сайта: `no_site` 23, `weak_site` 11, `unavailable` 6. Все в статусе `not_started`. Одна старая кампания-черновик на 3 адресата, 0 попыток отправки, 0 записей `business_telegram`.

## 3. «Проверка ТГ» и запасной MTProto-код

- Сейчас: `app/phone_links.py` локально делает ссылки `https://t.me/+<E.164>` только для мобильных номеров, сети нет; мост `telegram_phone_links`, экран `telegramPage` в `app/desktop_ui/stage9.js`.
- Запас: `app/telegram_sender.py` (908 строк), Telethon. Один аккаунт; `api_hash` шифруется DPAPI (`_protect`), **файл сессии `personal_telegram.session` — обычный SQLite без шифрования** (`app/telegram_sender.py:21`, `:190`); требует подтверждённого согласия каждого адресата; лимит 1–20 в день и пауза 30–3600 с; «проверка номера» — `ImportContacts` с удалением контакта (`:295-355`).
- В Desktop отключён: `TELEGRAM_MTPROTO_ENABLED = False` (`app/desktop.py:23`), страж `scripts/check_desktop_telegram_surface.py` запрещает старые методы и экраны.
- Библиотека: Telethon **1.45.0** (закреплена в `requirements-exe-lock.txt`, установлена в `.venv`), выпуск 2026-09-10 — последний на PyPI; репозиторий codeberg.org/Lonami/Telethon не архивирован, 5 открытых задач (API Codeberg, 2026-10-30).
- Сессий и ключей Telegram на диске нет: файлов `*.session`, `telegram.json`, `login_state.json` в проектах и в `%APPDATA%\LeadEqualMoney` не найдено (есть только тестовая заглушка `.testdata/missing-api-field/telegram.json`).

## 4. Секреты и сетевые вызовы

- `parserYand\secrets\accounts.json` создан владельцем (351 байт), игнорируется git правилом `*secret*` (`.gitignore`). Поля: `api_id` (8 цифр, `35••••`), `api_hash` (32 hex), 3 аккаунта: 2 с `is_test: true`, 1 `main-1`. **Отклонение от формата:** вместо `"account_id": <id>` записано `"<id>": 0`; модуль это понимает. Файла `secrets\accounts.local.json` с `test_recipients` нет.
- Скан `scripts/mailing_leak_scan.py` с поиском точных значений из файла владельца (5 значений): дерево ветки — 0 находок; `dist\` и `publish_export\` — 0 (после исключения ложных срабатываний: пример `api_hash` в `telethon-1.45.0.dist-info/METADATA`, бинарный `selenium-manager.exe`); `%APPDATA%\LeadEqualMoney` — 0.
- Вся основная папка parserYand (21 755 файлов, вместе с `dist`, `verification/launch`, `.audit-backup`): значения ключей и телефонов найдены **только** в самом `secretsccounts.json`; ещё одна находка — тестовая заглушка `.testdata/missing-api-field/telegram.json` без ключей.
- `lead-mini-app\key.txt` (55 байт, игнорируется `.gitignore:16`) — похоже на ключ; не открывался. К задаче не относится.
- Сетевые вызовы Desktop: `yandex.ru` (Selenium/requests в `app/lead_parser.py`, `app/media.py`, `app/desktop_gallery.py`), сайты организаций (проверка типа сайта с защитой от локальных адресов, `app/lead_parser.py`), `webbrowser.open` по кнопке. Mini App: `api.telegram.org` (`app/bot.py`). MTProto-серверы Telegram — только в неподключённом `telegram_sender.py`.

## 5. Зависимости

- Закреплены в `requirements-exe-lock.txt` (48 пакетов). Проверка по базе OSV (`scripts/mailing_osv_audit.py`; pip-audit не запустился из-за длинного пути временной папки):
  - `certifi 2026.7.22` — GHSA-6q48-6c7w-9gw2 / PYSEC-2026-4320 (скомпрометированные корневые сертификаты emSign), исправлено в 2026.10.18. Влияет на HTTPS парсера (requests), не на MTProto.
  - `setuptools 84.0.0` — GHSA-xmj3-9gf7-h52w (установка старых wheel), исправлено в 84.1.0. Только сборка.
- Давно без выпусков: `pyaes 1.6.1` (2017, запасная AES-реализация Telethon), `proxy_tools 0.1.0` (2014, зависимость pywebview), `PySocks 1.7.1` (2019), `sortedcontainers 2.4.0` (2021). Pyrogram заброшен (последний выпуск 2023-04-30) — не использовать.

## 6. Известные открытые баги (не чиним)

| Баг | Где описан | Влияние на рассылку |
|---|---|---|
| Автообновление интерфейса | `reports/2_auto_refresh.md`: опрос только текущего раздела, 6 с, пауза при вводе; пост-трасса не подтверждена | Да, косвенно: новый экран не должен подписываться на опрос и не должен перерисовываться сам. Учтено в UI модуля |
| «Приложение не отвечает» | Отдельного отчёта нет; мост pywebview вызывает Python синхронно, долгие вызовы (Selenium) держат поток моста | Да: вызовы Telegram в модуле выполняются в своём фоновом потоке, мост только читает SQLite |
| Капча в «Найти разделы фото» | `reports/8_gallery.md`, `reports/8_FINAL.md`: живой DOM не подтверждён из-за ChromeDriver | Нет |

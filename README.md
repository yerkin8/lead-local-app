# LeadEqualMoney — настольное приложение (Windows)

Локальное приложение для поиска организаций на Яндекс Картах, CRM по найденной базе, фото и товаров из карточек, скриптов сообщений и (с собственными ключами Telegram) рассылки. Окно — pywebview + Microsoft Edge WebView2; HTTP-сервер не поднимается, данные хранятся только на компьютере пользователя.

Telegram Mini App, бот и серверное развёртывание в этот репозиторий не входят.

## Запуск из исходного кода

Нужны Windows 10/11 x64, Python 3.12–3.14, Microsoft Edge WebView2 и Google Chrome (для парсинга).

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
.\run-desktop.ps1
```

## Сборка

```powershell
.\build.ps1
```

Скрипт создаёт чистое окружение `.build-venv` из `requirements-exe-lock.txt` и собирает папку `dist\LeadEqualMoney\` с `LeadEqualMoney.exe` (PyInstaller, `packaging\LeadEqualMoney-onedir.spec`). Ярлык создаёт `create_shortcut.ps1`. Проверки: `scripts\verify.ps1`.

## Данные и секреты

В репозитории нет ключей, сессий Telegram, баз и выгрузок:

- база, логи, медиа и данные рассылки создаются в `%APPDATA%\LeadEqualMoney`;
- ключи Telegram (`api_id`, `api_hash`) и вход в аккаунт вводятся в окне приложения и хранятся там же в зашифрованном виде (DPAPI текущего пользователя Windows);
- в `secrets\` лежат только пустые примеры; настоящие файлы владелец создаёт сам, они исключены `.gitignore`.

Каждый пользователь вводит **свои** ключи Telegram: передавать `api_id/api_hash` третьим лицам Telegram запрещает.

Лицензии сторонних компонентов — в `THIRD_PARTY.md`.

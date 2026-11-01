# Что сделать владельцу

## 1. Файл с тестовыми адресатами

Создайте `C:\Users\User\Documents\projects\parserYand\secrets\accounts.local.json` (образец — `secrets/accounts.local.example.json`):

```json
{"test_recipients": ["@username_вашего_второго_аккаунта"]}
```

Только **ваши** аккаунты. Username надёжнее телефона: по номеру адресат находится, только если он уже есть в контактах тестового аккаунта.

## 2. Вход в тестовый аккаунт (код вводите только в окне программы)

1. Запустите новую сборку: `dist\LeadEqualMoney\LeadEqualMoney.exe` в рабочей копии ветки (или распакуйте `dist\release\LeadEqualMoney-portable-20261030.zip`).
2. «Рассылка» → «Загрузить из файла…» → `secrets\accounts.json` (ключи и три аккаунта).
3. Ещё раз «Загрузить из файла…» → `secrets\accounts.local.json` (тестовые адресаты).
4. Вкладка «Аккаунты» → у аккаунта `test` нажмите «Войти» → введите код из Telegram → пароль 2FA, если есть.
5. Закройте программу.

## 3. Живая проверка (≤3 сообщения только на ваши аккаунты)

Скажите Claude «вход выполнен» — он запустит `scripts/mailing_live_check.py` и приложит результат в `verification/mailing/live/live-run.json`. Или запустите сами:

```bash
.build-venv\Scripts\python.exe scripts\mailing_live_check.py
```

Сценарий займёт 5–10 минут из-за пауз между сообщениями.

## Как собрать

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1
```

```bash
.build-venv\Scripts\python.exe scripts\mailing_make_zip.py dist\release
```

Zip для других людей не содержит ключей и сессий: каждый проходит мастер первого запуска со своими api_id/api_hash.

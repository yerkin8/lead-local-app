# Приёмка модуля рассылки

Вердикты в колонке «Итог» ставит независимый проверяющий (раздел внизу), а не автор кода.
✔ — выполнено и подтверждено доказательством; ✘ — не выполнено или не подтверждено.

| № | Требование | Как проверить | Доказательство (путь) | Итог |
|---|---|---|---|---|
| A1 | Приложение запускается из любой папки и по ярлыку, без терминала | Распаковать zip в пустую папку, запустить exe; ярлык — «Создать ярлык.cmd» | `verification/mailing/clean-machine/report.json`, `01_app_first_run.png`; `create_shortcut.ps1` || ✔ |
| A2 | Мастер первого запуска: ввод ключей и вход в тестовый аккаунт | Мастер в собранном exe с тестовыми значениями; вход — код владельца | `verification/mailing/clean-machine/02_*.png`, `03_*.png`; вход — `verification/mailing/live/live-run.json` || ✘ (частично) |
| A3 | Сухой запуск на 5 записях: ожидаемый отчёт, ничего не отправлено | Тест `DryRunTests`, тур по окну, живой сценарий шаг 1 | `tests/test_mailing_core.py`, `verification/mailing/ui/07_dry_run_report.png`, `verification/mailing/ui/tour.json` || ✔ |
| A4 | Живая отправка ≤3 сообщений с тестового аккаунта на аккаунты владельца, id сохранены | `scripts/mailing_live_check.py` | `verification/mailing/live/live-run.json` || ✘ |
| A5 | FloodWait / PeerFlood / ограничение: поведение по разделу 4, карантин, остальные не затронуты | Тесты `TelegramErrorTests` | `tests/test_mailing_core.py`, `verification/mailing/tests.txt` || ✔ |
| A6 | Перезапуск посреди кампании: нет дубликатов, кампания продолжается | `IdempotencyTests`; живой сценарий (перезапуск после 1-го сообщения, сверка истории) | `tests/test_mailing_core.py`, `verification/mailing/live/live-run.json` || ✔ (на FakeGateway) |
| A7 | Аварийный стоп за ≤5 секунд | `EmergencyStopTests` (отправка «в полёте» 30 с); живой сценарий | `tests/test_mailing_core.py`, `verification/mailing/live/live-run.json` || ✔ (на FakeGateway) |
| A8 | Стоп-лист и дедупликация | `StopListTests` | `tests/test_mailing_core.py`, `verification/mailing/ui/10_stoplist.png` || ✔ |
| A9 | Статистика в интерфейсе = счётчики в базе | Тур: сравнение плиток с `service.campaign()` после сухого и живого (Fake) запуска | `verification/mailing/ui/tour.json` (`A9_*`) || ✔ |
| A10 | Нет секретов в логах, отчётах, репозитории и архивах | `SecretsTests`; `scripts/mailing_leak_scan.py` по репозиторию, zip, папке данных теста | `verification/mailing/leak-scan.txt` || ✔ |
| A11 | Оригиналы не изменены (SHA-256 до/после) | `scripts/mailing_hash_originals.py --compare` | `verification/mailing/hashes-before.txt`, `hashes-after.txt`, `hashes-compare.txt` || ✔ |
| A12 | «Назад» на всех экранах модуля, ввод не сбрасывается | Тур: `all_screens_have_back`, `A12_input_kept_after_refresh` | `verification/mailing/ui/tour.json`, скриншоты `verification/mailing/ui/` || ✔ |
| A13 | Автотесты ядра проходят, список приложен | `python -m unittest tests.test_mailing_core tests.test_mailing_bridge -v` | `verification/mailing/tests.txt` || ✔ |
| A14 | Вердикт независимого проверяющего | Субагент, не писавший код | раздел ниже || ✔ получен |

## Вердикт независимого проверяющего

Проверяющий — отдельный субагент, код не писал, исходники не менял, трафика в Telegram не было. Два прохода.

**Проход 1** — «не принимать»: 8 дефектов (ярлык не разбирается в PowerShell 5.1; кампания оставалась «Идёт» без задачи после ошибки подключения; ложный карантин после «Выйти» и нового входа; «Ошибка модуля» вместо «Нужен вход»; устаревший статус после «Обновить статистику»; несолёный `phone_key`; нестабильный тест «чистой машины»; косметика). Исправления — `docs/mailing/KNOWN_ISSUES.md` K9, коммит `c7c176c`.

**Проход 2** (после `c7c176c`, `d0bfdba`): все 8 дефектов исправлены, кроме части №7 (скрипт пишет в `verification/` — оставлено намеренно, это папка доказательств). Сам проверяющий прогнал: 39/39 тестов; сканер утечек с точными значениями из файла владельца — 0 находок в новом zip (892 файла), в отслеживаемых файлах + `verification` + `docs` (373) и в `git log -p --all`; хеши оригиналов — 250/250 без изменений; clean-machine на новом zip — 10/10 проверок с первой попытки; `create_shortcut.ps1` из zip — BOM, 0 ошибок разбора в PowerShell 5.1.

| № | Итог | Доказательство | Замечания |
|---|---|---|---|
| A1 | ✔ | clean-machine на новом zip 10/10; exe — GUI-подсистема; ps1 разбирается в PS 5.1 | Ярлыки реально не создавались (запрет на изменение рабочего стола), вывод по разбору и коду |
| A2 | ✘ (частично) | мастер ключей и добавление аккаунта в exe ✔ | Нет входа владельца, нет `verification/mailing/live/live-run.json` |
| A3 | ✔ | `DryRunTests`; тур: dry_sent 5, sent 0; сухой запуск не вызывает gateway | Живой шаг 1 не выполнялся |
| A4 | ✘ | — | Нужны вход владельца и `scripts/mailing_live_check.py` |
| A5 | ✔ | `TelegramErrorTests` 7/7, `ConnectionTests` 5/5 | Получатели в карантине не переходят на другой аккаунт |
| A6 | ✔ (Fake) | `IdempotencyTests` 4/4 | Живая часть не выполнена |
| A7 | ✔ (Fake) | `EmergencyStopTests`: < 5 с при отправке «в полёте» | Живая часть не выполнена |
| A8 | ✔ | `StopListTests` 3/3 | — |
| A9 | ✔ | тур: true после живой (Fake) отправки; после сухого — совпадает на скриншоте | Гонка в проверке тура исправлена после прохода 2, повторный тур: оба `A9_*` = true |
| A10 | ✔ | сканы — 0 находок; DPAPI, ACL только владелец и SYSTEM, соль для телефона | — |
| A11 | ✔ | хеши 250/250, changed=0 | — |
| A12 | ✔ | тур: «Назад» на всех экранах, ввод сохраняется, ошибок JS нет | — |
| A13 | ✔ | 39/39 OK (`verification/mailing/tests.txt`) | — |

**Итог проверяющего:** дефекты кода исправлены; A1, A3, A5–A13 приняты (A6, A7 — на FakeGateway). Модуль целиком не принят: A2 и A4 ✘ до входа владельца и живой проверки. Блокирующих дефектов для этого шага нет.

param(
  [switch]$Trace
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $ProjectRoot

$Python = Join-Path $ProjectRoot '.build-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
  throw 'Не найдено окружение .build-venv. Сначала подготовьте окружение проекта.'
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  throw 'Для проверки настольного интерфейса нужен Node.js.'
}

function Run-Check([string]$Name, [scriptblock]$Command) {
  Write-Host "Проверка: $Name"
  & $Command
  if ($LASTEXITCODE -ne 0) { throw "Проверка не пройдена: $Name (код $LASTEXITCODE)" }
}

Run-Check 'Тест менеджера опроса' { node --test tests\activity-poller.test.cjs }
Run-Check 'Тест снимка активности SQLite' { & $Python -m unittest tests.test_activity_snapshot -v }
Run-Check 'Тест ручных ссылок Telegram' { & $Python -m unittest tests.test_phone_links -v }
Run-Check 'Тест расширенных данных организаций Stage 3' { & $Python -m unittest tests.test_business_details -v }
Run-Check 'Тест динамических разделов галереи desktop Stage 4' { & $Python -m unittest tests.test_desktop_gallery -v }
Run-Check 'Тест иерархии запусков базы desktop Stage 5' { & $Python -m unittest tests.test_parse_runs -v }
Run-Check 'Нормализация ссылок Яндекса Stage 9' { & $Python -m unittest tests.test_yandex_urls -v }
Run-Check 'Тест данных превью папок Stage 6' { & $Python -m unittest tests.test_folder_preview_data -v }
Run-Check 'Ядро рассылки Telegram на FakeGateway' { & $Python -m unittest tests.test_mailing_core tests.test_mailing_bridge -v }
Run-Check 'Старый интерфейс Telegram отключён, рассылка только в app/mailing' { & $Python scripts\check_desktop_telegram_surface.py }
Run-Check 'Компиляция backend-парсера и моста' { & $Python -m compileall -q app\db.py app\lead_parser.py app\desktop_gallery.py app\media_service.py app\desktop_services.py app\desktop.py app\mailing }
Run-Check 'Синтаксис JavaScript desktop' {
  node --check app\desktop_ui\activity-poller.js
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  node --check app\desktop_ui\app.js
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  node --check app\desktop_ui\flows.js
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  node --check app\desktop_ui\stage9.js
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  node --check app\desktop_ui\glass-folder.js
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  node --check app\desktop_ui\mailing.js
}
Run-Check 'Запрет автопереходов и отдельных интервалов' { & $Python scripts\check_refresh_sources.py }
Run-Check 'Токены, контраст и локальные шрифты desktop' { & $Python scripts\check_desktop_theme.py --report verification\design\theme-static-check.txt }

if ($Trace) {
  Run-Check 'Оффлайн-трасса настольного интерфейса (9 разделов, 90 секунд)' {
    & $Python scripts\trace_refresh.py --phase after --duration 90 --workers 1
  }
}

Write-Host 'Проверки этапа автообновления завершены.'

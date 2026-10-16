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
Run-Check 'Компиляция backend-моста' { & $Python -m compileall -q app\db.py app\desktop.py }
Run-Check 'Синтаксис JavaScript desktop' {
  node --check app\desktop_ui\activity-poller.js
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  node --check app\desktop_ui\app.js
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  node --check app\desktop_ui\flows.js
}
Run-Check 'Запрет автопереходов и отдельных интервалов' { & $Python scripts\check_refresh_sources.py }
Run-Check 'Токены, контраст и локальные шрифты desktop' { & $Python scripts\check_desktop_theme.py --report verification\design\theme-static-check.txt }

if ($Trace) {
  Run-Check 'Оффлайн-трасса настольного интерфейса (9 разделов, 90 секунд)' {
    & $Python scripts\trace_refresh.py --phase after --duration 90 --workers 1
  }
}

Write-Host 'Проверки этапа автообновления завершены.'

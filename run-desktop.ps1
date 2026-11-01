$ErrorActionPreference = 'Stop'
$env:PYTHONPATH = (Resolve-Path .).Path
if (Test-Path .venv\Scripts\python.exe) {
    .\.venv\Scripts\python.exe -m app.desktop
} else {
    py -3.12 -m app.desktop
}

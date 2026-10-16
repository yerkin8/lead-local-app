param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
Set-Location -LiteralPath $ProjectRoot

function Assert-CommandSuccess([string]$Message) {
  if ($LASTEXITCODE -ne 0) { throw "$Message (код $LASTEXITCODE)" }
}

function Assert-NoReparsePoint([string]$Target) {
  if (Test-Path -LiteralPath $Target) {
    $Item = Get-Item -LiteralPath $Target -Force
    if (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "Отказ от изменения пути-ссылки: $Target"
    }
  }
}

$PythonCommand = $null
$PythonPrefix = @()
$PyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($PyLauncher) {
  foreach ($Version in @('3.14', '3.13', '3.12')) {
    try {
      & $PyLauncher.Source "-$Version" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == tuple(map(int, '$Version'.split('.'))) else 1)" 2>$null
      if ($LASTEXITCODE -eq 0) {
        $PythonCommand = $PyLauncher.Source
        $PythonPrefix = @("-$Version")
        break
      }
    } catch { }
  }
}
if (-not $PythonCommand) {
  $PythonExe = Get-Command python -ErrorAction SilentlyContinue
  if (-not $PythonExe) { throw 'Для сборки установите CPython 3.14 x64 и повторите запуск.' }
  $PythonCommand = $PythonExe.Source
}

$PythonInfo = & $PythonCommand @PythonPrefix -c "import json,platform,struct,sys; print(json.dumps({'version':sys.version_info[:2], 'bits':struct.calcsize('P')*8, 'impl':sys.implementation.name}))"
Assert-CommandSuccess 'Не удалось запустить выбранный Python'
$Info = $PythonInfo | ConvertFrom-Json
if ($Info.version[0] -ne 3 -or $Info.version[1] -notin @(12, 13, 14) -or $Info.bits -ne 64 -or $Info.impl -ne 'cpython') {
  throw 'Для сборки нужен CPython 3.12, 3.13 или 3.14 x64.'
}

$BuildVenv = Join-Path $ProjectRoot '.build-venv'
$BuildTemp = Join-Path $ProjectRoot '.build-tmp'
$BuildPath = [System.IO.Path]::GetFullPath($BuildVenv)
$TempPath = [System.IO.Path]::GetFullPath($BuildTemp)
if (-not $BuildPath.StartsWith($ProjectRoot + [System.IO.Path]::DirectorySeparatorChar,
    [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Путь виртуального окружения вышел за пределы проекта.' }
if (-not $TempPath.StartsWith($ProjectRoot + [System.IO.Path]::DirectorySeparatorChar,
    [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Путь временных файлов вышел за пределы проекта.' }
Assert-NoReparsePoint $BuildPath
Assert-NoReparsePoint $TempPath
if (Test-Path -LiteralPath $BuildPath) { Remove-Item -LiteralPath $BuildPath -Recurse -Force }
New-Item -ItemType Directory -Force -Path $TempPath | Out-Null
$env:TEMP = $TempPath
$env:TMP = $TempPath

& $PythonCommand @PythonPrefix -m venv $BuildVenv
Assert-CommandSuccess 'Не удалось создать чистое виртуальное окружение'
$BuildPython = Join-Path $BuildVenv 'Scripts\python.exe'
& $BuildPython -m pip install --disable-pip-version-check --no-input -r (Join-Path $ProjectRoot 'requirements-exe-lock.txt')
Assert-CommandSuccess 'Не удалось установить зафиксированные зависимости'
& $BuildPython (Join-Path $ProjectRoot 'scripts\make_app_icon.py')
Assert-CommandSuccess 'Не удалось подготовить иконку приложения'

$BuildDir = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot 'build'))
$DistDir = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot 'dist\LeadEqualMoney'))
$OldOneFileExe = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot 'dist\LeadEqualMoney.exe'))
foreach ($Target in @($BuildDir, $DistDir, $OldOneFileExe)) {
  if (-not $Target.StartsWith($ProjectRoot + [System.IO.Path]::DirectorySeparatorChar,
      [System.StringComparison]::OrdinalIgnoreCase)) { throw "Путь результата вышел за пределы проекта: $Target" }
}
Assert-NoReparsePoint $BuildDir
Assert-NoReparsePoint $DistDir
Assert-NoReparsePoint $OldOneFileExe
if (Test-Path -LiteralPath $BuildDir) { Remove-Item -LiteralPath $BuildDir -Recurse -Force }
if (Test-Path -LiteralPath $DistDir) { Remove-Item -LiteralPath $DistDir -Recurse -Force }
# Remove the previous one-file trial so users cannot launch the broken, stale build by mistake.
if (Test-Path -LiteralPath $OldOneFileExe) { Remove-Item -LiteralPath $OldOneFileExe -Force }

& $BuildPython -m PyInstaller --clean --noconfirm (Join-Path $ProjectRoot 'packaging\LeadEqualMoney-onedir.spec')
Assert-CommandSuccess 'PyInstaller не смог собрать приложение'
$DistExe = Join-Path $DistDir 'LeadEqualMoney.exe'
if (-not (Test-Path -LiteralPath $DistExe)) { throw 'Сборка завершилась без dist\LeadEqualMoney\LeadEqualMoney.exe.' }
$Bytes = (Get-ChildItem -LiteralPath $DistDir -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host "Готово: $DistExe (папка сборки $([math]::Round($Bytes / 1MB, 1)) МБ)"

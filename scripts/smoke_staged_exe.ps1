param(
  [Parameter(Mandatory = $true)][string]$Executable,
  [Parameter(Mandatory = $true)][string]$ExistingDatabaseCopy,
  [int]$IdleSeconds = 60
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$TempRoot = (Resolve-Path -LiteralPath (Join-Path $ProjectRoot '.build-tmp')).Path
$DistAppRoot = (Resolve-Path -LiteralPath (Join-Path $ProjectRoot 'dist\LeadEqualMoney')).Path
$Executable = (Resolve-Path -LiteralPath $Executable).Path
$ExistingDatabaseCopy = (Resolve-Path -LiteralPath $ExistingDatabaseCopy).Path

function Assert-InDirectory([string]$Path, [string]$Root, [string]$Description) {
  $FullPath = [System.IO.Path]::GetFullPath($Path)
  if (-not $FullPath.StartsWith($Root + [System.IO.Path]::DirectorySeparatorChar,
      [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "$Description находится вне разрешённого каталога $Root : $FullPath"
  }
}

try { Assert-InDirectory $Executable $TempRoot 'EXE staging' }
catch { Assert-InDirectory $Executable $DistAppRoot 'Установленный EXE' }
Assert-InDirectory $ExistingDatabaseCopy $TempRoot 'Копия существующей базы'
if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { throw 'Не найден EXE для smoke-проверки.' }
if ((Split-Path $ExistingDatabaseCopy -Leaf) -ne 'app.sqlite3') { throw 'Ожидалась только копия app.sqlite3.' }

$RunStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$DatabaseCopyHashBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $ExistingDatabaseCopy).Hash
$EmptyProfile = Join-Path $TempRoot "stage7-smoke-empty-$RunStamp"
$ExistingProfile = Join-Path $TempRoot "stage7-smoke-existing-$RunStamp"
foreach ($Profile in @($EmptyProfile, $ExistingProfile)) {
  if (Test-Path -LiteralPath $Profile) { throw "Профиль уже существует: $Profile" }
}
New-Item -ItemType Directory -Path $EmptyProfile | Out-Null
New-Item -ItemType Directory -Path $ExistingProfile | Out-Null
Copy-Item -LiteralPath $ExistingDatabaseCopy -Destination (Join-Path $ExistingProfile 'app.sqlite3')
Set-Content -LiteralPath (Join-Path $ExistingProfile 'first_run_complete') -Value '1.0.0' -NoNewline -Encoding utf8

$PreviousDataDir = $env:DATA_DIR
$PreviousHttpProxy = $env:HTTP_PROXY
$PreviousHttpsProxy = $env:HTTPS_PROXY
$env:DATA_DIR = $EmptyProfile
$env:HTTP_PROXY = 'http://127.0.0.1:9'
$env:HTTPS_PROXY = 'http://127.0.0.1:9'
$Results = [System.Collections.Generic.List[string]]::new()

function Invoke-AppSmoke([string]$Name, [string]$DataPath, [int]$Seconds, [bool]$ExpectEmpty) {
  $env:DATA_DIR = $DataPath
  $Process = Start-Process -FilePath $Executable -WindowStyle Minimized -PassThru
  try {
    $Deadline = (Get-Date).AddSeconds(60)
    $Title = ''
    $WindowHandle = [Int64]0
    while ((Get-Date) -lt $Deadline) {
      if ($Process.HasExited) { throw "${Name}: EXE завершился до показа окна, code=$($Process.ExitCode)" }
      $Process.Refresh()
      $Title = $Process.MainWindowTitle
      $WindowHandle = $Process.MainWindowHandle.ToInt64()
      if ($Title -eq 'LeadEqualMoney' -or $WindowHandle -ne 0) { break }
      Start-Sleep -Milliseconds 500
    }
    if ($WindowHandle -eq 0) { throw "${Name}: окно приложения не появилось за 60 секунд (title='$Title')" }
    Start-Sleep -Seconds $Seconds
    $Process.Refresh()
    if ($Process.HasExited) { throw "${Name}: окно закрылось во время ожидания" }

    $LogPath = Join-Path $DataPath 'logs\app.log'
    if (-not (Test-Path -LiteralPath $LogPath)) { throw "${Name}: журнал приложения не создан" }
    $LogText = Get-Content -LiteralPath $LogPath -Raw -Encoding utf8
    if ($LogText -match '(?im)\b(ERROR|CRITICAL)\b|Traceback') { throw "${Name}: в app.log найдено ERROR/CRITICAL/Traceback" }
    if ($ExpectEmpty -and -not (Test-Path -LiteralPath (Join-Path $DataPath 'app.sqlite3'))) {
      throw 'Пустой профиль не создал SQLite базу.'
    }
    $FirstRunState = if ($ExpectEmpty) {
      if (Test-Path -LiteralPath (Join-Path $DataPath 'first_run_complete')) { 'setup completed during wait' } else { 'setup remains pending' }
    } else { 'preconfigured profile' }

    if (-not $Process.CloseMainWindow()) { throw "${Name}: окно отказалось принимать штатное закрытие" }
    if (-not $Process.WaitForExit(15000)) { throw "${Name}: процесс не завершился штатно за 15 секунд" }
    if ($Process.ExitCode -ne 0) { throw "${Name}: код завершения $($Process.ExitCode), ожидался 0" }
    $Results.Add("${Name}: title='$Title'; window handle available; stayed open $Seconds s; graceful exit code $($Process.ExitCode); log errors=0; $FirstRunState")
  } finally {
    if (-not $Process.HasExited) {
      $Process.Refresh()
      $Actual = (Get-Process -Id $Process.Id -ErrorAction SilentlyContinue).Path
      if ($Actual -and [System.IO.Path]::GetFullPath($Actual) -eq $Executable) {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
      }
    }
  }
}

try {
  Invoke-AppSmoke 'Пустой профиль / бездействие' $EmptyProfile $IdleSeconds $true
  Invoke-AppSmoke 'Повторный запуск' $EmptyProfile 8 $true
  Invoke-AppSmoke 'Копия существующей базы' $ExistingProfile 8 $false
  $Python = Join-Path $ProjectRoot '.build-venv\Scripts\python.exe'
  $DatabaseCheck = & $Python -c "import json,pathlib,sqlite3,sys; p=pathlib.Path(sys.argv[1]); c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True); print(json.dumps({'integrity':c.execute('PRAGMA integrity_check').fetchone()[0],'businesses':c.execute('SELECT COUNT(*) FROM businesses').fetchone()[0]}))" (Join-Path $ExistingProfile 'app.sqlite3')
  if ($LASTEXITCODE -ne 0) { throw 'Не удалось проверить копию SQLite после запуска EXE.' }
  $DbState = $DatabaseCheck | ConvertFrom-Json
  if ($DbState.integrity -ne 'ok' -or $DbState.businesses -lt 1) { throw "Копия БД повреждена или пуста: $DatabaseCheck" }
  $DatabaseCopyHashAfter = (Get-FileHash -Algorithm SHA256 -LiteralPath $ExistingDatabaseCopy).Hash
  if ($DatabaseCopyHashAfter -ne $DatabaseCopyHashBefore) { throw 'Исходная тестовая копия базы изменилась во время запуска.' }
  $Results.Add("Existing profile clone: SQLite integrity=$($DbState.integrity), businesses=$($DbState.businesses); source fixture SHA-256 unchanged.")
  $Results.Add("HTTP_PROXY/HTTPS_PROXY set to closed 127.0.0.1:9; no queued network work or Telegram credentials were configured.")
  $Results.Add("Existing database source left untouched: $ExistingDatabaseCopy")
  $Results | Set-Content -LiteralPath (Join-Path $ProjectRoot "verification\build\stage7-smoke-$RunStamp.txt") -Encoding utf8
  $Results | ForEach-Object { Write-Output $_ }
} finally {
  if ($null -eq $PreviousDataDir) { Remove-Item Env:\DATA_DIR -ErrorAction SilentlyContinue } else { $env:DATA_DIR = $PreviousDataDir }
  if ($null -eq $PreviousHttpProxy) { Remove-Item Env:\HTTP_PROXY -ErrorAction SilentlyContinue } else { $env:HTTP_PROXY = $PreviousHttpProxy }
  if ($null -eq $PreviousHttpsProxy) { Remove-Item Env:\HTTPS_PROXY -ErrorAction SilentlyContinue } else { $env:HTTPS_PROXY = $PreviousHttpsProxy }
}

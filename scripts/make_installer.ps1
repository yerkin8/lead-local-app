param(
  [string]$CompilerPath
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$Spec = Join-Path $ProjectRoot 'installer\LeadEqualMoney.iss'
$Portable = Join-Path $ProjectRoot 'dist\LeadEqualMoney\LeadEqualMoney.exe'
$Output = Join-Path $ProjectRoot 'dist\LeadEqualMoney-Setup.exe'

if (-not (Test-Path -LiteralPath $Portable -PathType Leaf)) {
  throw "Сначала соберите portable-папку: $Portable"
}

if ([string]::IsNullOrWhiteSpace($CompilerPath)) {
  $Command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
  if ($Command) { $CompilerPath = $Command.Source }
}

if ([string]::IsNullOrWhiteSpace($CompilerPath)) {
  $Candidates = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 7\ISCC.exe'),
    (Join-Path $env:ProgramFiles 'Inno Setup 7\ISCC.exe')
  ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }
  $CompilerPath = $Candidates | Select-Object -First 1
}

if ([string]::IsNullOrWhiteSpace($CompilerPath) -or -not (Test-Path -LiteralPath $CompilerPath -PathType Leaf)) {
  throw 'Inno Setup не найден. Установите Inno Setup 6 или 7 либо передайте -CompilerPath с путём к ISCC.exe.'
}

& $CompilerPath $Spec
if ($LASTEXITCODE -ne 0) { throw "ISCC завершился с кодом $LASTEXITCODE" }
if (-not (Test-Path -LiteralPath $Output -PathType Leaf)) { throw "Установщик не создан: $Output" }
Write-Host "Готово: $Output"

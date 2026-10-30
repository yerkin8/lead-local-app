param(
  [string]$PortableDirectory
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($PortableDirectory)) {
  $Candidates = @(
    $PSScriptRoot,
    (Join-Path $PSScriptRoot 'dist\LeadEqualMoney')
  )
  $PortableDirectory = $Candidates | Where-Object {
    Test-Path -LiteralPath (Join-Path $_ 'LeadEqualMoney.exe')
  } | Select-Object -First 1
}

if ([string]::IsNullOrWhiteSpace($PortableDirectory)) {
  throw 'Не найдена LeadEqualMoney.exe. Укажите папку portable-сборки параметром -PortableDirectory.'
}

$PortableDirectory = (Resolve-Path -LiteralPath $PortableDirectory).Path
$Exe = Join-Path $PortableDirectory 'LeadEqualMoney.exe'
if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) {
  throw "В указанной папке нет LeadEqualMoney.exe: $PortableDirectory"
}

$Desktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
$StartMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\LeadEqualMoney'
New-Item -ItemType Directory -Force -Path $StartMenu | Out-Null
$Shell = New-Object -ComObject WScript.Shell

foreach ($ShortcutPath in @(
  (Join-Path $Desktop 'LeadEqualMoney.lnk'),
  (Join-Path $StartMenu 'LeadEqualMoney.lnk')
)) {
  $Shortcut = $Shell.CreateShortcut($ShortcutPath)
  $Shortcut.TargetPath = $Exe
  $Shortcut.Arguments = ''
  $Shortcut.WorkingDirectory = ''
  $Shortcut.IconLocation = "$Exe,0"
  $Shortcut.Description = 'Запуск LeadEqualMoney'
  $Shortcut.Save()
}

Write-Host "Ярлыки созданы. Приложение запускается из $PortableDirectory, пользовательские данные остаются в %APPDATA%\LeadEqualMoney."

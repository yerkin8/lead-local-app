#define AppName "LeadEqualMoney"
#define AppVersion "1.0.0"
#define AppExeName "LeadEqualMoney.exe"

[Setup]
AppId={{A6841853-AB0C-4F19-93C7-73F6A9B7D18B}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=LeadEqualMoney
DefaultDirName={localappdata}\Programs\LeadEqualMoney
DefaultGroupName=LeadEqualMoney
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64
OutputDir=..\dist
OutputBaseFilename=LeadEqualMoney-Setup
SetupIconFile=..\app\assets\lem-icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"; Flags: unchecked

[Files]
Source: "..\dist\LeadEqualMoney\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\LeadEqualMoney"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"
Name: "{autodesktop}\LeadEqualMoney"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Запустить LeadEqualMoney"; Flags: postinstall nowait skipifsilent

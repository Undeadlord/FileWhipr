#define AppName "FileWhipr"
#define AppVersion "0.2.1"
#define AppPublisher "Undeadlord"
#define AppExeName "FileWhipr.exe"
#define LauncherExeName "FileWhiprLauncher.exe"

[Setup]
AppId={{1B6B3FA9-54C2-4D22-8F24-4F9E2C6A53F7}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
OutputDir=..\installer-output
OutputBaseFilename=FileWhiprSetup-{#AppVersion}
SetupIconFile=..\FileWhipr.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest

[Files]
Source: "..\dist\FileWhipr\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"

[Registry]
Root: HKCU; Subkey: "Software\Classes\Directory\shell\FileWhip"; Flags: deletekey dontcreatekey uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\FileWhip"; Flags: deletekey dontcreatekey uninsdeletekey

Root: HKCU; Subkey: "Software\Classes\Directory\shell\FileWhipr"; ValueType: string; ValueName: ""; ValueData: "{#AppName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\shell\FileWhipr"; ValueType: string; ValueName: "MUIVerb"; ValueData: "{#AppName}"
Root: HKCU; Subkey: "Software\Classes\Directory\shell\FileWhipr"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\FileWhipr.ico"
Root: HKCU; Subkey: "Software\Classes\Directory\shell\FileWhipr"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Player"
Root: HKCU; Subkey: "Software\Classes\Directory\shell\FileWhipr\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#LauncherExeName}"" ""%V"""

Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\FileWhipr"; ValueType: string; ValueName: ""; ValueData: "{#AppName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\FileWhipr"; ValueType: string; ValueName: "MUIVerb"; ValueData: "{#AppName}"
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\FileWhipr"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\FileWhipr.ico"
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\FileWhipr"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Player"
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\FileWhipr\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#LauncherExeName}"" ""%V"""

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

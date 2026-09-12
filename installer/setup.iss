; ============================================================
; 成都建工 V3.1 Windows 安装包 - Runtime/Build SSOT
; ============================================================

#define MyAppName "成都建工控制台"
#define MyAppNameShort "ChengduConstructionConsole"
#ifndef MyAppVersion
#define MyAppVersion "3.1.0"
#endif
#define MyAppPublisher "成都建工集团"
#define MyAppURL "https://github.com/yvochehk-oss/chengdu-construction-tax-system-v2.0"
#define MyAppExeName "成都建工控制台3.1.exe"

[Setup]
AppId={{B6E4C9F1-3A28-4D7E-9C5F-8B2E1A4D7E6C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={localappdata}\Programs\{#MyAppNameShort}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
InfoBeforeFile=..\docs\install-prelude.txt
OutputDir=output
OutputBaseFilename=ChengduConstructionConsole-v{#MyAppVersion}-Setup
SetupIconFile=..\app.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
Uninstallable=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
VersionInfoVersion={#MyAppVersion}.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[CustomMessages]
chinesesimp.CreateDesktopIcon=创建桌面快捷方式
chinesesimp.LaunchAfterInstall=安装完成后启动 {#MyAppName}

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "附加图标:"; Flags: unchecked
Name: "autoStartup"; Description: "随 Windows 登录自动运行控制台"; GroupDescription: "系统集成:"; Flags: unchecked

[Files]
; Canonical .NET 10 / win-x64 single-file controller output.
Source: "..\desktop_apps\windows\publish\win-x64\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

; Launch/maintenance scripts. 99_STOP_ALL.bat intentionally preserves the
; existing force-kill-by-port operational behavior requested for V3.1.
Source: "..\windows_scripts\*"; DestDir: "{app}\windows_scripts"; Flags: ignoreversion recursesubdirs createallsubdirs

; Portable PostgreSQL payload. database/data is created/maintained at runtime.
Source: "..\database\*"; DestDir: "{app}\database"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

; Local model runtime only; GGUF weights may be downloaded after install.
Source: "..\models\local-llm\runtime-win-cpu-x64\*"; DestDir: "{app}\models\local-llm\runtime-win-cpu-x64"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

; Embedded Python runtime used by RAG/Tax/IDP/Boss static server.
Source: "..\runtime\python\*"; DestDir: "{app}\runtime\python"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

; Business services.
Source: "..\source_code\0.2_RAG系统\*"; DestDir: "{app}\source_code\0.2_RAG系统"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "**\.venv\**;**\__pycache__\**;**\*.pyc;**\.pytest_cache\**;**\.mypy_cache\**;**\.ruff_cache\**"
Source: "..\source_code\0.1_税务管理\*"; DestDir: "{app}\source_code\0.1_税务管理"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "**\.venv\**;**\__pycache__\**;**\*.pyc;**\.pytest_cache\**;**\.mypy_cache\**;**\.ruff_cache\**"
Source: "..\source_code\0.4_IDP文档录入引擎_V3.0\*"; DestDir: "{app}\source_code\0.4_IDP文档录入引擎_V3.0"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "**\.venv\**;**\__pycache__\**;**\*.pyc;**\.pytest_cache\**;**\.mypy_cache\**;**\.ruff_cache\**"
Source: "..\source_code\0.3_老板端安卓App_天府掌舵\dist\*"; DestDir: "{app}\source_code\0.3_老板端安卓App_天府掌舵\dist"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "..\models\boss-dist\*"; DestDir: "{app}\models\boss-dist"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

Source: "..\docs\install-postlude.md"; DestDir: "{app}\docs"; Flags: ignoreversion

[Dirs]
Name: "{app}\logs"
Name: "{app}\runtime"
Name: "{app}\runtime\state"
Name: "{app}\models\local-llm"
Name: "{app}\database"
Name: "{app}\database\data"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ChengduConstructionConsole"; ValueData: """{app}\{#MyAppExeName}"""; Tasks: autoStartup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchAfterInstall}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\temp"
Type: filesandordirs; Name: "{app}\runtime\state"

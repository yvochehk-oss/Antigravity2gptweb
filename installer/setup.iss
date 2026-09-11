; ============================================================
; 成都建工 V3.1 Windows 桌面控制台 安装包脚本 (Inno Setup)
; ============================================================
; 用途：生成单个 Setup.exe，内置依赖检测与一键静默安装
; 生成工具：Inno Setup 6.4+ (https://jrsoftware.org/isdl.php)
; 编译命令：iscc setup.iss
; ============================================================

#define MyAppName "成都建工控制台"
#define MyAppNameShort "ChengduConsole"
#define MyAppVersion "3.1.0"
#define MyAppPublisher "成都建工集团"
#define MyAppURL "https://github.com/yvochehk-oss/chengdu-construction-tax-system-v2.0"
#define MyAppExeName "成都建工控制台.exe"
#define MyAppCopyright "Copyright (C) 2026 成都建工集团"

[Setup]
; 安装包唯一标识符（不要改动，会被 Windows 用于卸载识别）
AppId={{B6E4C9F1-3A28-4D7E-9C5F-8B2E1A4D7E6C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
AppCopyright={#MyAppCopyright}
DefaultDirName={autopf}\{#MyAppNameShort}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
InfoBeforeFile=..\docs\install-prelude.txt
OutputDir=output
OutputBaseFilename=ChengduConstructionConsole-v{#MyAppVersion}-Setup
SetupIconFile=..\desktop_apps\windows\Resources\tray.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; 静默安装支持: /silent /verysilent
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
; 自定义中文安装消息
chinesesimp.CreateDesktopIcon=创建桌面快捷方式
chinesesimp.LaunchAfterInstall=安装完成后启动 {#MyAppName}
chinesesimp.DependencyWarning=依赖项检查提示
chinesesimp.VCRuntimeMissing=未检测到 Microsoft Visual C++ 2015-2022 Redistributable (x64)。建议安装以避免运行时错误。
chinesesimp.WebView2Missing=未检测到 Microsoft Edge WebView2 Runtime。系统托盘 UI 需要此组件。

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autoStartup"; Description: "随 Windows 启动自动运行控制台（托盘后台）"; GroupDescription: "系统集成:"; Flags: unchecked

[Files]
; 主程序
Source: "..\desktop_apps\windows\bin\Release\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; 全部项目资源
Source: "..\desktop_apps\windows\Resources\*"; DestDir: "{app}\Resources"; Flags: ignoreallsources recursesubdirs createallsubdirs
; 启动脚本
Source: "..\windows_scripts\*"; DestDir: "{app}\windows_scripts"; Flags: ignoreallsources recursesubdirs createallsubdirs
; 服务模型与数据库
Source: "..\database\*"; DestDir: "{app}\database"; Flags: ignoreallsources recursesubdirs createallsubdirs; Check: IncludePortableDatabase
; 大模型运行时（不含 GGUF 权重，由下载脚本按需获取）
Source: "..\models\local-llm\runtime-win-cpu-x64\*"; DestDir: "{app}\models\local-llm\runtime-win-cpu-x64"; Flags: ignoreallsources recursesubdirs createallsubdirs; Check: IncludeLlamaRuntime
; 嵌入式 Python 运行时（v3.1 新增）
Source: "..\runtime\python\*"; DestDir: "{app}\runtime\python"; Flags: ignoreallsources recursesubdirs createallsubdirs; Check: IncludeEmbeddedPython
; 前端 dist（v3.1 新增，零 Node.js 依赖）
Source: "..\source_code\0.3_老板端安卓App_天府掌舵\dist\*"; DestDir: "{app}\source_code\0.3_老板端安卓App_天府掌舵\dist"; Flags: ignoreallsources recursesubdirs createallsubdirs; Check: IncludeBossDist
; 文档
Source: "..\docs\install-postlude.md"; DestDir: "{app}\docs"; Flags: ignoreversion

[Dirs]
; 创建必要的空目录
Name: "{app}\logs"
Name: "{app}\runtime"
Name: "{app}\models\local-llm"

[Icons]
; 开始菜单快捷方式
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
; 桌面快捷方式
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; 安装完成后可选启动
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchAfterInstall}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时清理日志和临时文件（保留数据库）
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\temp"
; 不删除：{app}\database（用户数据保留）

[Code]
// ============================================================
// 安装前依赖检测：VC++ Runtime 与 WebView2
// ============================================================
function IsVCRuntimeInstalled: Boolean;
var
  Key: String;
begin
  // 检查 VC++ 2015-2022 Redistributable (x64)
  Result := RegKeyExists(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64') or
            RegKeyExists(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64') or
            RegKeyExists(HKCU, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64');
end;

function IsWebView2Installed: Boolean;
var
  Version: String;
begin
  // 优先 HKCU（用户级），再检查 HKLM
  Result := RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version);
  if not Result then
    Result := RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version);
  if not Result then
    Result := RegQueryStringValue(HKLM64, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version);
end;

function InitializeSetup: Boolean;
var
  MissingDeps: String;
begin
  MissingDeps := '';

  // 检测 VC++ 运行库
  if not IsVCRuntimeInstalled then
    MissingDeps := MissingDeps + '  - Microsoft Visual C++ 2015-2022 Redistributable (x64)' + #13#10;

  // 检测 WebView2
  if not IsWebView2Installed then
    MissingDeps := MissingDeps + '  - Microsoft Edge WebView2 Runtime' + #13#10;

  if MissingDeps <> '' then
  begin
    if MsgBox(
      '检测到以下依赖项缺失：' + #13#10 + #13#10 +
      MissingDeps +
      #13#10 +
      '安装程序仍会继续，但建议安装后再运行。' + #13#10 +
      '继续安装吗？',
      mbConfirmation, MB_YESNO) = IDNO then
    begin
      Result := False;
      Exit;
    end;
  end;

  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  // 安装完成后记录日志
  if CurStep = ssPostInstall then
  begin
    SaveStringToFile(
      ExpandConstant('{app}\logs\install.log'),
      '安装时间：' + GetDateTimeString('yyyy-mm-dd hh:nn:ss', #0) + #13#10 +
      '安装版本：{#MyAppVersion}' + #13#10 +
      '安装路径：' + ExpandConstant('{app}') + #13#10,
      False);
  end;
end;

// ============================================================
// 可选打包开关：根据构建脚本传入的环境变量决定是否包含
// ============================================================
function IncludePortableDatabase: Boolean;
begin
  Result := ExpandConstant('{param:IncludeDatabase|true}') <> 'false';
end;

function IncludeLlamaRuntime: Boolean;
begin
  Result := ExpandConstant('{param:IncludeLlama|true}') <> 'false';
end;

function IncludeEmbeddedPython: Boolean;
begin
  Result := ExpandConstant('{param:IncludePython|true}') <> 'false';
end;

function IncludeBossDist: Boolean;
begin
  Result := ExpandConstant('{param:IncludeBossDist|true}') <> 'false';
end;

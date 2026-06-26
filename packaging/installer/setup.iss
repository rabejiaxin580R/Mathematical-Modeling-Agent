; 数学建模助教 · Inno Setup 安装脚本
;
; 由 build_windows.ps1 调用，传入 /DSourceDir=<便携目录> /DOutputDir=<输出目录>。
; 也可手动在 Inno Setup 里打开本文件编译（需先定义 SourceDir，见下方默认值）。
;
; 设计要点：
;   - 每用户安装（PrivilegesRequired=lowest），默认装到 %LOCALAPPDATA%\Programs，
;     普通用户无需管理员权限；与 app 把可写数据放到 %LOCALAPPDATA% 的策略一致。
;   - 卸载只删程序文件，不动用户数据（聊天/档案在另一处用户目录）。
;   - 简体中文向导。

#ifndef SourceDir
  #define SourceDir "dist\数学建模助教"
#endif
#ifndef OutputDir
  #define OutputDir "dist"
#endif

#define AppName "数学建模助教"
#define AppVersion "1.0.0"
#define AppPublisher "Math Modeling Agent"
#define AppExe "启动助教.exe"

[Setup]
AppId={{8F3C6A21-4B7E-4E2A-9C5D-MMAGENT00001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename={#AppName}-安装程序-v{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式:"

[Files]
; 递归打包整个便携目录（内置 Python + app + 启动器 + 说明）
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}";        Filename: "{app}\{#AppExe}"
Name: "{group}\卸载 {#AppName}";   Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}";  Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; 安装完成后可勾选立即启动
Filename: "{app}\{#AppExe}"; Description: "立即启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[Messages]
chinesesimp.WelcomeLabel2=即将在你的电脑上安装 [name]。%n%n这是一个面向新手的数学建模 AI 助教，自带运行环境，安装后双击即可使用，无需另装 Python。%n%n建议关闭其它程序后再继续。

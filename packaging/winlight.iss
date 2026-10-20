; SPDX-License-Identifier: GPL-3.0-only
; Inno Setup 6 script for Winlight. Built by packaging/build.py, which passes
; AppVersion, VersionNumbers, SourceDir, OutputDir and IconFile with /D.
;
; Installs for the current user only (no administrator prompt), like
; Monitorian's and Twinkle Tray's per-user installs.

#ifndef AppVersion
  #error Build with packaging/build.py
#endif

[Setup]
; Never change AppId: it is how upgrades and the uninstaller find the install.
AppId={{F658C16C-1589-4566-8E3C-295499D77F23}
AppName=Winlight
AppVersion={#AppVersion}
AppVerName=Winlight {#AppVersion}
VersionInfoVersion={#VersionNumbers}
AppPublisher=Anurag Jha
AppCopyright=Copyright (C) 2026 Anurag Jha. Free software under the GNU GPL v3.0.
VersionInfoCompany=Anurag Jha
VersionInfoDescription=Winlight setup: brightness and night light for every display
AppPublisherURL=https://github.com/Arnie14/Night-Light-Windows
AppSupportURL=https://github.com/Arnie14/Night-Light-Windows/issues
AppUpdatesURL=https://github.com/Arnie14/Night-Light-Windows/releases
DefaultDirName={localappdata}\Programs\Winlight
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=Winlight-{#AppVersion}-setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\Winlight.exe
UninstallDisplayName=Winlight
WizardStyle=modern
; Shown before installing: a disclaimer, what clashes with Winlight, recommendations.
InfoBeforeFile=installer-notes.txt
Compression=lzma2/max
SolidCompression=yes
; Close a running copy through Windows' Restart Manager if --quit didn't.
CloseApplications=yes
RestartApplications=no
; Tell Windows about the PATH change (the optional command line task).
ChangesEnvironment=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "addtopath"; Description: "Add winlight-cli to PATH, to control Winlight from scripts and the command line"; GroupDescription: "Command line:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Winlight"; Filename: "{app}\Winlight.exe"; AppUserModelID: "Winlight.Winlight"
Name: "{autodesktop}\Winlight"; Filename: "{app}\Winlight.exe"; AppUserModelID: "Winlight.Winlight"; Tasks: desktopicon

[Registry]
; The user's own PATH, so no administrator rights are needed; removed again on uninstall.
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Tasks: addtopath; Check: NotOnPath(ExpandConstant('{app}'))

[Run]
Filename: "{app}\Winlight.exe"; Description: "{cm:LaunchProgram,Winlight}"; Flags: nowait postinstall skipifsilent

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  EnvKey = 'Environment';

function NotOnPath(Dir: String): Boolean;
var
  Path: String;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, EnvKey, 'Path', Path) then
    Path := '';
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Path) + ';') = 0;
end;

procedure RemoveFromPath(Dir: String);
var
  Path: String;
  P: Integer;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, EnvKey, 'Path', Path) then
    exit;
  P := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Path) + ';');
  if P = 0 then
    exit;
  { In ';' + Path + ';' the match starts at the separator before the entry,
    so in Path the entry starts at P. Remove it with one separator: the one
    before it, or after it when it is the first entry. }
  if P = 1 then
    Delete(Path, 1, Length(Dir) + 1)
  else
    Delete(Path, P - 1, Length(Dir) + 1);
  RegWriteExpandStringValue(HKEY_CURRENT_USER, EnvKey, 'Path', Path);
end;

{ Ask a running Winlight to quit, so it puts the screen colors back to
  normal before its files are replaced or removed. Killing it instead would
  leave the night light tint on screen until the next restart. }
procedure QuitRunningCopy(const Exe: String);
var
  ResultCode: Integer;
begin
  if FileExists(Exe) then
  begin
    Exec(Exe, '--quit', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(1500);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  QuitRunningCopy(ExpandConstant('{app}\Winlight.exe'));
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    QuitRunningCopy(ExpandConstant('{app}\Winlight.exe'));
  { The app writes this value when "Start Winlight when I sign in" is on;
    don't leave it pointing at a deleted program. Settings in
    %LOCALAPPDATA%\night-light are kept, as most apps do. }
  if CurUninstallStep = usPostUninstall then
  begin
    RegDeleteValue(HKEY_CURRENT_USER, RunKey, 'Winlight');
    RemoveFromPath(ExpandConstant('{app}'));
  end;
end;

; Inno Setup 스크립트 — Modbus TCP Server Simulator 설치 프로그램.
; 버전은 빌드 시 주입한다:  iscc /DMyAppVersion=1.0.0 packaging\windows\installer.iss
; (경로는 이 스크립트 파일 위치 기준 상대경로로 해석된다.)

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppName "Modbus TCP Server Simulator"
#define MyAppExeName "modbus_tcp_server.exe"
#define MyAppPublisher "CMES"

[Setup]
AppId={{8F3C2A10-4D5E-4B7A-9C21-0A1B2C3D4E5F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\ModbusTcpServer
DefaultGroupName=Modbus TCP Server
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=..\..\resources\app_icon.ico
OutputDir=output
OutputBaseFilename=modbus_tcp_server-{#MyAppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Modbus TCP Server"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,Modbus TCP Server}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Modbus TCP Server"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,Modbus TCP Server}"; Flags: nowait postinstall skipifsilent

[Code]
{ 설치 전에 같은 AppId 의 기존 설치본이 있으면 조용히 제거하고 새로 설치한다. }
const
  UninstKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{8F3C2A10-4D5E-4B7A-9C21-0A1B2C3D4E5F}_is1';

function GetUninstallString(): String;
var
  s: String;
begin
  s := '';
  if not RegQueryStringValue(HKLM, UninstKey, 'UninstallString', s) then
    RegQueryStringValue(HKCU, UninstKey, 'UninstallString', s);
  Result := s;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  unstr: String;
  code: Integer;
begin
  if CurStep = ssInstall then
  begin
    unstr := RemoveQuotes(GetUninstallString());
    if unstr <> '' then
      { 기존 버전 무인 제거(파일이 깨끗이 정리된 뒤 새로 설치됨) }
      Exec(unstr, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '',
           SW_HIDE, ewWaitUntilTerminated, code);
  end;
end;

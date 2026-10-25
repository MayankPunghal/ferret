; Inno Setup script for the Windows installer. Build (after packaging\build_windows.ps1):
;   iscc /DAppVersion=1.0.0 packaging\installer.iss      ->  dist\Ferret-Setup-1.0.0.exe
; Installs for the current user only (no admin prompt) into %LOCALAPPDATA%\Programs\Ferret.
; Documents, the index and models live in %USERPROFILE%\Ferret and survive updates and uninstalls (you're asked).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Ferret"
#define AppPublisher "Mayank Punghal"
#define AppURL "https://github.com/MayankPunghal/ferret"
#define Pythonw "{app}\python\pythonw.exe"

[Setup]
AppId={{8E3C2F4B-6D1A-4B7E-9C52-1F0A7D3E9B61}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} installer
VersionInfoProductName={#AppName}
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
UsePreviousTasks=yes
OutputDir=..\dist
OutputBaseFilename=Ferret-Setup-{#AppVersion}
SetupIconFile=ferret.ico
UninstallDisplayIcon={app}\ferret.ico
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
ShowLanguageDialog=no
CloseApplications=no

[Messages]
WelcomeLabel2=This installs [name/ver] on your computer.%n%nPrivate search for your documents: every result cites the passage it came from, and when nothing supports an answer, Ferret says so. Everything runs on this computer: no account, no cloud AI.%n%nOn first start Ferret downloads its search models (about 0.5 GB, one time) and picks settings that suit this computer.
FinishedLabel=Ferret is installed. It opens in your browser, where you create the administrator account.%n%nFerret keeps running in the system tray (near the clock): right-click its icon to open it, start it when you sign in, or quit.

[Tasks]
Name: "autostart"; Description: "Start Ferret when I sign in to Windows"; GroupDescription: "Options:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Options:"

[Files]
Source: "..\dist\Ferret\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; a clean Python folder on every update, so files from an older version never linger
Type: filesandordirs; Name: "{app}\python"

[UninstallDelete]
; files the app adds at run time (the PostgreSQL DLL copied next to its binaries, compiled bytecode)
Type: filesandordirs; Name: "{app}\python"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{#Pythonw}"; Parameters: "-m knowledge_search.tray"; WorkingDir: "{app}"; IconFilename: "{app}\ferret.ico"; Comment: "Private search for your documents"
Name: "{autodesktop}\{#AppName}"; Filename: "{#Pythonw}"; Parameters: "-m knowledge_search.tray"; WorkingDir: "{app}"; IconFilename: "{app}\ferret.ico"; Tasks: desktopicon

[Registry]
; the same value the tray's "Start when I sign in" writes (tray.start_command), so the two always agree
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; \
  ValueData: """{#Pythonw}"" -m knowledge_search.tray --background"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
; Started through Explorer, as a click on the shortcut would: a program the installer starts itself inherits
; its "untrusted mount point" protection and cannot read the model cache's links (WinError 448).
Filename: "{win}\explorer.exe"; Parameters: """{autoprograms}\{#AppName}.lnk"""; Description: "Start Ferret now"; Flags: postinstall nowait skipifsilent
; an update started from Settings or the tray (/UPDATE=1) runs silently and starts Ferret again
Filename: "{win}\explorer.exe"; Parameters: """{autoprograms}\{#AppName}.lnk"""; Flags: nowait; Check: IsUpdate

[Code]
function IsUpdate: Boolean;
begin
  Result := ExpandConstant('{param:UPDATE|0}') = '1';
end;

procedure StopFerret(Dir: String);
var
  Code: Integer;
begin
  { the database cleanly, then everything else started from this folder (stop.ps1) }
  if FileExists(Dir + '\stop.ps1') then
    Exec('powershell.exe', '-NoProfile -ExecutionPolicy Bypass -File "' + Dir + '\stop.ps1" -App "' + Dir + '"',
      '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopFerret(WizardDirValue);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and not WizardIsTaskSelected('autostart') then
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', '{#AppName}');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Data: String;
begin
  if CurUninstallStep = usUninstall then
    StopFerret(ExpandConstant('{app}'));
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
  begin
    Data := ExpandConstant('{%USERPROFILE}\Ferret');
    if DirExists(Data) and (MsgBox('Also delete your Ferret documents, search index, accounts and downloaded models?' + #13#10#13#10 +
        'Choose No to keep them for a later reinstall (they are in ' + Data + ').',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES) then
      DelTree(Data, True, True, True);
  end;
end;

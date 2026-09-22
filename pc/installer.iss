; Inno Setup script for the Windows installer. Build the app with PyInstaller first, then from the repo root:
;   iscc /DAppVersion=1.2.3 pc\installer.iss
; Writes dist\OverQueue-Setup-<version>.exe.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6C1E5F3A-8B2D-4E7A-9F41-2D0B7A3C9E15}
AppName=OverQueue
AppVersion={#AppVersion}
AppPublisher=Tomer Ady
AppPublisherURL=https://github.com/overjump1/overwatch-queue
DefaultDirName={autopf}\OverQueue
DefaultGroupName=OverQueue
DisableProgramGroupPage=yes
; Per-user install by default, so no UAC prompt; the user can still pick "all users".
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=OverQueue-Setup-{#AppVersion}
UninstallDisplayIcon={app}\OverQueue.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Closes a running copy before overwriting it.
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startup"; Description: "Start OverQueue when I sign in to Windows"; GroupDescription: "Other:"; Flags: unchecked

[Files]
Source: "..\dist\OverQueue\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Clears out the previous version's bundled libraries so stale ones don't linger.
Type: filesandordirs; Name: "{app}\_internal"
; The app was called OW Queue before this, and an upgrade installs over that one: without these
; the old exe stays behind and its shortcuts go on launching it, so you end up running both.
Type: files; Name: "{app}\OWQueue.exe"
Type: files; Name: "{autoprograms}\OW Queue.lnk"
Type: files; Name: "{autodesktop}\OW Queue.lnk"
Type: files; Name: "{userstartup}\OW Queue.lnk"

[Icons]
Name: "{autoprograms}\OverQueue"; Filename: "{app}\OverQueue.exe"
Name: "{autodesktop}\OverQueue"; Filename: "{app}\OverQueue.exe"; Tasks: desktopicon
Name: "{userstartup}\OverQueue"; Filename: "{app}\OverQueue.exe"; Tasks: startup

[Run]
Filename: "{app}\OverQueue.exe"; Description: "{cm:LaunchProgram,OverQueue}"; Flags: nowait postinstall skipifsilent
; The in-app updater installs with /SILENT, which skips the entry above; start the app again itself.
Filename: "{app}\OverQueue.exe"; Flags: nowait runasoriginaluser; Check: SilentInstall

[Code]
function SilentInstall: Boolean;
begin
  Result := WizardSilent;
end;

; Inno Setup script for the Windows installer. Build the app with PyInstaller first, then from the repo root:
;   iscc /DAppVersion=1.2.3 pc\installer.iss
; Writes dist\OWQueue-Setup-<version>.exe.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6C1E5F3A-8B2D-4E7A-9F41-2D0B7A3C9E15}
AppName=OW Queue
AppVersion={#AppVersion}
AppPublisher=Tomer Ady
AppPublisherURL=https://github.com/overjump1/overwatch-queue
DefaultDirName={autopf}\OW Queue
DefaultGroupName=OW Queue
DisableProgramGroupPage=yes
; Per-user install by default, so no UAC prompt; the user can still pick "all users".
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=OWQueue-Setup-{#AppVersion}
UninstallDisplayIcon={app}\OWQueue.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Closes a running copy before overwriting it.
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startup"; Description: "Start OW Queue when I sign in to Windows"; GroupDescription: "Other:"; Flags: unchecked

[Files]
Source: "..\dist\OWQueue\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Clears out the previous version's bundled libraries so stale ones don't linger.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\OW Queue"; Filename: "{app}\OWQueue.exe"
Name: "{autodesktop}\OW Queue"; Filename: "{app}\OWQueue.exe"; Tasks: desktopicon
Name: "{userstartup}\OW Queue"; Filename: "{app}\OWQueue.exe"; Tasks: startup

[Run]
Filename: "{app}\OWQueue.exe"; Description: "{cm:LaunchProgram,OW Queue}"; Flags: nowait postinstall skipifsilent
; The in-app updater installs with /SILENT, which skips the entry above; start the app again itself.
Filename: "{app}\OWQueue.exe"; Flags: nowait runasoriginaluser; Check: SilentInstall

[Code]
function SilentInstall: Boolean;
begin
  Result := WizardSilent;
end;

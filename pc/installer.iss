; Inno Setup script for the Windows installer. Build the app with PyInstaller first, then from the repo root:
;   iscc /DAppVersion=1.2.3 pc\installer.iss
; Writes dist\OverQueue-Setup-<version>.exe for OverQueue Dev, which installs beside the real app
; rather than over it (see channel.py). Add /DReal for the real app, as main's release does.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#ifdef Real
  #define AppName "OverQueue"
  #define AppGuid "{{6C1E5F3A-8B2D-4E7A-9F41-2D0B7A3C9E15}"
#else
  #define AppName "OverQueue Dev"
  #define AppGuid "{{D221E428-A600-4EE6-BB3F-00E64DC1961A}"
#endif

[Setup]
AppId={#AppGuid}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Tomer Ady
AppPublisherURL=https://github.com/overjump1/overwatch-queue
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user install by default, so no UAC prompt; the user can still pick "all users".
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=OverQueue-Setup-{#AppVersion}
UninstallDisplayIcon={app}\OverQueue.exe
#ifdef Real
SetupIconFile=icons\overqueue.ico
#else
SetupIconFile=icons\overqueue-dev.ico
#endif
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Closes a running copy before overwriting it.
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startup"; Description: "Start {#AppName} when I sign in to Windows"; GroupDescription: "Other:"; Flags: unchecked

[Files]
Source: "..\dist\OverQueue\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Clears out the previous version's bundled libraries so stale ones don't linger.
Type: filesandordirs; Name: "{app}\_internal"
#ifdef Real
; The app was called OW Queue before this, and an upgrade installs over that one: without these
; the old exe stays behind and its shortcuts go on launching it, so you end up running both.
Type: files; Name: "{app}\OWQueue.exe"
Type: files; Name: "{autoprograms}\OW Queue.lnk"
Type: files; Name: "{autodesktop}\OW Queue.lnk"
Type: files; Name: "{userstartup}\OW Queue.lnk"
#endif

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\OverQueue.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\OverQueue.exe"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\OverQueue.exe"; Tasks: startup

[Run]
Filename: "{app}\OverQueue.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; The in-app updater installs with /SILENT, which skips the entry above; start the app again itself.
Filename: "{app}\OverQueue.exe"; Flags: nowait runasoriginaluser; Check: SilentInstall

[Code]
function SilentInstall: Boolean;
begin
  Result := WizardSilent;
end;

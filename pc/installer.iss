; Inno Setup script for the Windows installer. Build the app with PyInstaller first, then from the repo root:
;   iscc /DAppVersion=1.2.3 pc\installer.iss
; Writes dist\windows-x64-<version>.exe for QueueFox Dev, which installs beside the real app
; rather than over it (see channel.py). Add /DReal for the real app, as main's release does.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#ifdef Real
  #define AppName "QueueFox"
  #define OldAppName "OverQueue"
  #define AppGuid "{{6C1E5F3A-8B2D-4E7A-9F41-2D0B7A3C9E15}"
#else
  #define AppName "QueueFox Dev"
  #define OldAppName "OverQueue Dev"
  #define AppGuid "{{D221E428-A600-4EE6-BB3F-00E64DC1961A}"
#endif

[Setup]
AppId={#AppGuid}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Tomer Ady
AppPublisherURL=https://github.com/overjump1/queuefox
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user install by default, so no UAC prompt; the user can still pick "all users".
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=windows-x64-{#AppVersion}
UninstallDisplayIcon={app}\QueueFox.exe
#ifdef Real
SetupIconFile=icons\queuefox.ico
#else
SetupIconFile=icons\queuefox-dev.ico
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
Source: "..\dist\QueueFox\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Clears out the previous version's bundled libraries so stale ones don't linger.
Type: filesandordirs; Name: "{app}\_internal"
; The app was called OverQueue before this, and an upgrade installs over that one: without these
; the old exe stays behind and its shortcuts go on launching it, so you end up running both.
Type: files; Name: "{app}\OverQueue.exe"
Type: files; Name: "{autoprograms}\{#OldAppName}.lnk"
Type: files; Name: "{autodesktop}\{#OldAppName}.lnk"
Type: files; Name: "{userstartup}\{#OldAppName}.lnk"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\QueueFox.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\QueueFox.exe"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\QueueFox.exe"; Tasks: startup

[Run]
Filename: "{app}\QueueFox.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; The in-app updater installs with /SILENT, which skips the entry above; start the app again itself.
Filename: "{app}\QueueFox.exe"; Flags: nowait runasoriginaluser; Check: SilentInstall

[Code]
function SilentInstall: Boolean;
begin
  Result := WizardSilent;
end;

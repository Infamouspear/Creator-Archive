#define AppVersion "0.1.4"
[Setup]
AppId=CreatorArchive
AppName=Creator Archive
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Creator Archive
DefaultGroupName=Creator Archive
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=CreatorArchive-{#AppVersion}-x64-Setup
OutputDir=dist
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
UninstallDisplayIcon={app}\CreatorArchive.exe
[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked
[Files]
Source: "dist\CreatorArchive\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
[Icons]
Name: "{autoprograms}\Creator Archive"; Filename: "{app}\CreatorArchive.exe"
Name: "{autodesktop}\Creator Archive"; Filename: "{app}\CreatorArchive.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\CreatorArchive.exe"; Description: "Launch Creator Archive"; Flags: nowait postinstall skipifsilent
[UninstallRun]
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""Creator Archive Background"" /F"; Flags: runhidden
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""Creator Archive Background Startup"" /F"; Flags: runhidden

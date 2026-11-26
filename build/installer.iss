; Inno Setup script - StemMixer-Setup.exe
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

[Setup]
AppId={{8C1F3A52-6B7E-4E0B-9C1D-5A2F7E3B9D41}
AppName=Stem Mixer
AppVersion={#MyAppVersion}
AppVerName=Stem Mixer {#MyAppVersion}
AppPublisher=fedco22
DefaultDirName={localappdata}\Programs\Stem Mixer
DefaultGroupName=Stem Mixer
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\Output
OutputBaseFilename=StemMixer-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\StemMixer.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\StemMixer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Stem Mixer"; Filename: "{app}\StemMixer.exe"
Name: "{autodesktop}\Stem Mixer"; Filename: "{app}\StemMixer.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\StemMixer.exe"; Description: "{cm:LaunchProgram,Stem Mixer}"; Flags: nowait postinstall skipifsilent

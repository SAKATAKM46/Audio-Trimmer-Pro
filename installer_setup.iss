[Setup]
AppName=Audio Trimmer Pro
AppVersion=2.6
AppPublisher=SAKATAKM46
DefaultDirName={autopf}\Audio Trimmer Pro
DefaultGroupName=Audio Trimmer Pro
UninstallDisplayIcon={app}\audio_trimmer.exe
Compression=lzma2/ultra64
SolidCompression=yes
OutputDir=Output
OutputBaseFilename=Audio_Trimmer_Pro_v2.6_Setup
SetupIconFile=app_icon.ico

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Όλα τα αρχεία του προγράμματος (συμπεριλαμβανομένου του _internal και του exe)
Source: "dist\audio_trimmer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Ο φάκελος bin που περιέχει τα ffmpeg.exe & ffprobe.exe
Source: "bin\*"; DestDir: "{app}\bin"; Flags: ignoreversion recursesubdirs createallsubdirs
; Το εικονίδιο αν υπάρχει στον φάκελο
Source: "app_icon.ico"; DestDir: "{app}"; Flags: ignoreversion; Tasks: desktopicon

[Icons]
Name: "{group}\Audio Trimmer Pro"; Filename: "{app}\audio_trimmer.exe"
Name: "{autodesktop}\Audio Trimmer Pro"; Filename: "{app}\audio_trimmer.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\audio_trimmer.exe"; Description: "{cm:LaunchProgram,Audio Trimmer Pro}"; Flags: nowait postinstall skipifsilent
; Inno Setup script for Claude Code TTS. Built by build.ps1, which passes AppVersion.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6D1B3F4E-2C8A-4E7B-9A51-3F2C7D9E8B10}
AppName=Claude Code TTS
AppVersion={#AppVersion}
AppPublisher=Claude Code TTS contributors
DefaultDirName={localappdata}\Programs\Claude Code TTS
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=ClaudeCodeTTS-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\icon.ico
; Tells Windows to refresh cached icons, so upgrades from a build with a different icon show the new one.
ChangesAssociations=yes
UninstallDisplayIcon={app}\claude-code-tts.exe
UninstallDisplayName=Claude Code TTS

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\claude-code-tts.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Claude Code TTS"; Filename: "{app}\claude-code-tts.exe"
Name: "{autodesktop}\Claude Code TTS"; Filename: "{app}\claude-code-tts.exe"; Tasks: desktopicon

[Run]
; Restarts the stop hotkey listener after an upgrade. It exits at once if the hotkey is off or not set up yet.
Filename: "{app}\claude-code-tts.exe"; Parameters: "--listen"; Flags: nowait runhidden
Filename: "{app}\claude-code-tts.exe"; Description: "Open Claude Code TTS to finish setup"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Removes the Claude Code hook and the downloaded engine/voices in ~/.claude/tts.
Filename: "{app}\claude-code-tts.exe"; Parameters: "--uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveHook"

[Code]
// Stop a running hotkey listener so its exe can be replaced; [Run] starts it again.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  if FileExists(ExpandConstant('{app}\claude-code-tts.exe')) then
    Exec(ExpandConstant('{app}\claude-code-tts.exe'), '--quit-listener', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;

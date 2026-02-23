#define MyAppName "PTA Suite"
#ifndef MyAppVersion
  #define MyAppVersion "0.2.1"
#endif
#define MyAppPublisher "PTA"
#define AgentExe "PTAAgent.exe"
#define DashboardExe "PTADashboard.exe"
#ifndef MyOutputDir
  #define MyOutputDir "Output"
#endif
#ifndef MyOutputBaseFilename
  #define MyOutputBaseFilename "PTA_setup"
#endif

[Setup]
AppId={{7D4AA948-4861-47D2-B89A-5A8DD4F69F72}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf64}\PTA
DefaultGroupName=PTA
OutputDir={#MyOutputDir}
OutputBaseFilename={#MyOutputBaseFilename}
Compression=lzma
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
CloseApplicationsFilter=PTAAgent.exe,PTADashboard.exe
RestartApplications=no

[Files]
Source: "..\dist\PTAAgent\*"; DestDir: "{app}\Agent"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\PTADashboard\*"; DestDir: "{app}\Dashboard"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "register_task.ps1"; DestDir: "{app}\Installer"; Flags: ignoreversion
Source: "unregister_task.ps1"; DestDir: "{app}\Installer"; Flags: ignoreversion

[InstallDelete]
Type: filesandordirs; Name: "{app}\Agent"
Type: filesandordirs; Name: "{app}\Dashboard"

[Dirs]
Name: "{commonappdata}\PTA"
Name: "{commonappdata}\PTA\logs"

[Icons]
Name: "{group}\PTA Dashboard"; Filename: "{app}\Dashboard\{#DashboardExe}"
Name: "{commondesktop}\PTA Dashboard"; Filename: "{app}\Dashboard\{#DashboardExe}"

[Tasks]
Name: "launchdashboard"; Description: "Launch PTA Dashboard"; Flags: unchecked

[Run]
Filename: "{app}\Dashboard\{#DashboardExe}"; Description: "Launch PTA Dashboard"; Flags: postinstall nowait skipifsilent; Tasks: launchdashboard

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\Installer\unregister_task.ps1"""; Flags: runhidden

[Code]
procedure AppendInstallerLog(const Msg: String);
var
  LogPath, Line: String;
begin
  ForceDirectories(ExpandConstant('{commonappdata}\PTA\logs'));
  LogPath := ExpandConstant('{commonappdata}\PTA\logs\installer.log');
  Line := GetDateTimeString('yyyy-mm-dd hh:nn:ss', '-', ':') + ' ' + Msg + #13#10;
  SaveStringToFile(LogPath, Line, True);
end;

function HasMatchingFile(const Pattern: String): Boolean;
var
  FindRec: TFindRec;
begin
  Result := FindFirst(Pattern, FindRec);
  if Result then
    FindClose(FindRec);
end;

function ValidateRuntimeFiles(): Boolean;
var
  AgentPattern, DashboardPattern, InstallerLogPath: String;
begin
  Result := True;
  InstallerLogPath := ExpandConstant('{commonappdata}\PTA\logs\installer.log');

  AgentPattern := ExpandConstant('{app}\Agent\_internal\numpy\core\_multiarray_umath*.pyd');
  if not HasMatchingFile(AgentPattern) then
  begin
    AppendInstallerLog('ERROR: Missing runtime file pattern: ' + AgentPattern);
    MsgBox('Agent runtime is incomplete (missing NumPy binary). Reinstall using the latest installer.', mbCriticalError, MB_OK);
    Result := False;
    Exit;
  end;

  DashboardPattern := ExpandConstant('{app}\Dashboard\_internal\numpy\core\_multiarray_umath*.pyd');
  if not HasMatchingFile(DashboardPattern) then
  begin
    AppendInstallerLog('ERROR: Missing runtime file pattern: ' + DashboardPattern);
    MsgBox('Dashboard runtime is incomplete (missing NumPy binary). Reinstall using the latest installer.', mbCriticalError, MB_OK);
    Result := False;
    Exit;
  end;

  AppendInstallerLog('Runtime file validation passed.');
end;

function VerifyAgentCliFlags(): Boolean;
var
  TempOut, Cmd, Params: String;
  OutputText: AnsiString;
  ResultCode: Integer;
begin
  Result := False;
  TempOut := ExpandConstant('{tmp}\pta_agent_ingest_help.txt');
  Cmd := ExpandConstant('{cmd}');
  Params := '/C ""' + ExpandConstant('{app}\Agent\{#AgentExe}') + '" ingest --help > "' + TempOut + '" 2>&1"';

  if not Exec(Cmd, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    AppendInstallerLog('ERROR: Failed to execute PTAAgent ingest --help for CLI flag verification.');
    Exit;
  end;

  if not LoadStringFromFile(TempOut, OutputText) then
  begin
    AppendInstallerLog('ERROR: Could not read PTAAgent ingest --help output for verification.');
    Exit;
  end;

  if (Pos('--lookback-minutes', String(OutputText)) > 0) and (Pos('--reset-watermark', String(OutputText)) > 0) then
  begin
    AppendInstallerLog('PTAAgent CLI flag verification passed.');
    Result := True;
    Exit;
  end;

  AppendInstallerLog('ERROR: PTAAgent CLI flags missing in ingest --help output.');
  AppendInstallerLog('Captured help output: ' + String(OutputText));
end;

procedure RunInitialIngestAndRegisterTask();
var
  ResultCode: Integer;
  Cmd, Params, InstallerLogPath: String;
begin
  InstallerLogPath := ExpandConstant('{commonappdata}\PTA\logs\installer.log');

  if not ValidateRuntimeFiles() then
    Exit;
  if not VerifyAgentCliFlags() then
  begin
    MsgBox(
      'Installed PTAAgent is missing required CLI flags (--lookback-minutes / --reset-watermark). Setup will abort. See ' + InstallerLogPath,
      mbCriticalError,
      MB_OK
    );
    Abort;
  end;

  Cmd := ExpandConstant('{app}\Agent\{#AgentExe}');
  Params := 'ingest --db "' + ExpandConstant('{commonappdata}\PTA\pta.duckdb') + '"';
  if not Exec(Cmd, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    AppendInstallerLog('ERROR: Failed to start initial ingest run.');
    MsgBox('Failed to start initial ingest run. See ' + InstallerLogPath, mbError, MB_OK);
  end
  else if ResultCode <> 0 then
  begin
    AppendInstallerLog('WARN: Initial ingest exited with non-zero code ' + IntToStr(ResultCode) + '.');
    if ResultCode = 1 then
      MsgBox(
        'Agent runtime dependency issue detected (MetaTrader5/NumPy mismatch). Reinstall with the latest PTA_Setup.exe.',
        mbError,
        MB_OK
      )
    else
      MsgBox(
        'MT5 not detected/logged in yet. The agent is installed; open MT5 and log in, then log off/on to trigger ingestion.',
        mbInformation,
        MB_OK
      );
  end
  else
    AppendInstallerLog('Initial ingest completed successfully.');

  Cmd := 'powershell.exe';
  Params :=
    '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\Installer\register_task.ps1') +
    '" -AgentExePath "' + ExpandConstant('{app}\Agent\{#AgentExe}') +
    '" -DbPath "' + ExpandConstant('{commonappdata}\PTA\pta.duckdb') +
    '" -ProgramDataDir "' + ExpandConstant('{commonappdata}\PTA') + '"';

  if not Exec(Cmd, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    AppendInstallerLog('ERROR: Failed to launch register_task.ps1.');
    MsgBox('Failed to register scheduled task "PTA MT5 Ingest". See ' + InstallerLogPath, mbCriticalError, MB_OK);
  end
  else if ResultCode <> 0 then
  begin
    AppendInstallerLog('ERROR: register_task.ps1 exited with code ' + IntToStr(ResultCode) + '.');
    MsgBox('Failed to register scheduled task "PTA MT5 Ingest". See ' + InstallerLogPath, mbCriticalError, MB_OK);
  end
  else
    AppendInstallerLog('Scheduled task registration completed.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    SaveStringToFile(ExpandConstant('{commonappdata}\PTA\version.txt'), '{#MyAppVersion}' + #13#10, False);
    RunInitialIngestAndRegisterTask();
  end;
end;

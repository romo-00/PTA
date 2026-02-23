param(
  [Parameter(Mandatory=$true)][string]$AgentExePath,
  [Parameter(Mandatory=$true)][string]$DbPath,
  [string]$ProgramDataDir = $null
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($ProgramDataDir)) {
  $base = if ($env:PROGRAMDATA) { $env:PROGRAMDATA } else { 'C:\ProgramData' }
  $ProgramDataDir = Join-Path $base 'PTA'
}

$logsDir = Join-Path $ProgramDataDir 'logs'
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
$installerLog = Join-Path $logsDir 'installer.log'

function Write-InstallerLog([string]$msg) {
  $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
  Add-Content -Path $installerLog -Value $line
}

$taskName = 'PTA MT5 Ingest'

try {
  Write-InstallerLog("Registering task '$taskName' with AgentExePath=$AgentExePath DbPath=$DbPath")

  $arg = "/C `"`"$AgentExePath`" ingest --db `"$DbPath`"`""
  $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $arg

  # Portable trigger setup: logon trigger + 5-minute repeating trigger created with cmdlet parameters.
  $logonTrigger = New-ScheduledTaskTrigger -AtLogOn
  $repeatTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 1)

  $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable

  Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($logonTrigger, $repeatTrigger) -Settings $settings -RunLevel Highest -Force | Out-Null

  $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
  if (-not $task) {
    Write-InstallerLog("ERROR: task '$taskName' missing immediately after registration")
    exit 2
  }

  try {
    Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
    Write-InstallerLog("Started task '$taskName' once after registration")
  }
  catch {
    Write-InstallerLog("WARN: failed to start task '$taskName' immediately. " + $_.Exception.Message)
  }

  Write-InstallerLog("Task '$taskName' registered and verified")
  exit 0
}
catch {
  Write-InstallerLog('ERROR: ' + $_.Exception.Message)
  exit 1
}

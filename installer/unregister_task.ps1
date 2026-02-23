$taskName = "PTA MT5 Ingest"
try {
  Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
} catch {
  # ignore if not present
}

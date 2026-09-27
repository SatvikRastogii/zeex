# Stops the dev API, worker and UI (Windows can orphan the uvicorn reloader child).
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'uvicorn app\.main|app\.jobs\.worker|next dev|next-server|spawn_main' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

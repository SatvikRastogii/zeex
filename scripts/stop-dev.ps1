# Stops the dev API, worker and UI (Windows can orphan the uvicorn reloader child).
# Matches on the app entry points, not the executable name (uvicorn runs as uvicorn.exe).
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'app\.main:app|app\.jobs\.worker|next dev|next-server|next\\dist\\server' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# Starts the Tradzen backend on http://127.0.0.1:8787 (localhost only). --reload picks up code changes automatically.
Set-Location $PSScriptRoot
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8787 --reload --reload-dir app

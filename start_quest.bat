@echo off
REM ============================================================
REM  Quest - start everything (backend API + frontend + browser)
REM ============================================================
cd /d "%~dp0"

echo Starting Quest backend (FastAPI on http://127.0.0.1:8000) ...
start "Quest backend" cmd /k "cd backend && ..\.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8000"

echo Starting Quest frontend (http://127.0.0.1:8123) ...
start "Quest frontend" cmd /k "cd frontend && ..\.venv\Scripts\python.exe -m http.server 8123 --bind 127.0.0.1"

ping 127.0.0.1 -n 4 >nul
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" (
    start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" http://127.0.0.1:8123/new.html
) else if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" (
    start "" "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" http://127.0.0.1:8123/new.html
) else if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" (
    start "" "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" http://127.0.0.1:8123/new.html
) else (
    start "" http://127.0.0.1:8123/new.html
)

echo.
echo Quest is running:
echo   Frontend : http://127.0.0.1:8123/new.html
echo   Backend  : http://127.0.0.1:8000/docs
echo Close the two "Quest" windows (or run stop_quest.bat) to stop.

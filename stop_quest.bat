@echo off
REM ============================================================
REM  Quest - stop both servers (backend :8000, frontend :8123)
REM ============================================================
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do taskkill /PID %%p /F >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8123 ^| findstr LISTENING') do taskkill /PID %%p /F >nul 2>&1
echo Quest servers stopped.

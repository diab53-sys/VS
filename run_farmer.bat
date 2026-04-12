@echo off
title QUEUE FARMER v2
cd /d "%~dp0"

REM ── Load .env file (sets env vars before Python starts) ──
if exist ".env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do (
        if not "%%a"=="" set "%%a=%%b"
    )
    echo [ENV] Loaded .env file
) else (
    echo [ENV] WARNING: .env file not found. Create one with ANTHROPIC_API_KEY=sk-ant-...
    echo.
)

REM ── Activate venv if it exists ──
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo WARNING: Virtual environment not found.
    echo Run install.bat first, or install dependencies manually.
    echo.
)

echo ================================================
echo   QUEUE FARMER v2 - Pre-stage Queue Sessions
echo ================================================
echo.
echo   Improvements over v1:
echo     * Structured logging with metrics tracking
echo     * Exponential backoff on all retries
echo     * Randomised timing for bot evasion
echo     * Scout watchdog (auto-restart)
echo     * Graceful shutdown (Ctrl+C)
echo     * State checkpointing for crash recovery
echo.
echo   Waves:
echo     Wave 1: slots 0-9   (jittered 1.5-4s gaps)
echo     Wave 2: slots 10-14 (20s +/- jitter)
echo     Wave 3: slots 15-19 (60s +/- jitter)
echo     Wave 4: slots 20-24 (60s +/- jitter)
echo.
echo   Options:
echo     --instances N        Total instances (default: 25)
echo     --account-start N    Starting account index (default: 50)
echo     --server-port N      Solve server port (default: 9099)
echo.
echo   Press Ctrl+C for graceful shutdown
echo.

python -u queue_farmer.py %*

pause

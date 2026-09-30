@echo off
title QA Tester Suite - Launcher
echo ============================================================
echo               Launching QA Tester Suite
echo ============================================================

:: 1. Start Docker / PostgreSQL if docker is installed and running
where docker >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    echo [1/4] Checking PostgreSQL container via docker-compose...
    docker-compose up -d >nul 2>nul
) else (
    echo [1/4] Docker not found; ensure local PostgreSQL is running on port 5432.
)

:: 2. Launch Local Agent (Port 3001)
echo [2/4] Starting Local Agent on port 3001...
start "QA Agent (Port 3001)" cmd /k "cd agent && npm start"

:: 3. Launch Backend API (Port 5000)
echo [3/4] Starting Backend API on port 5000...
start "QA Backend (Port 5000)" cmd /k "cd backend && (if exist venv\Scripts\activate (call venv\Scripts\activate) else (echo Virtualenv not found. Running with global python...)) && uvicorn app.main:app --host 127.0.0.1 --port 5000"

:: 4. Launch Frontend Web UI (Port 3000)
echo [4/4] Starting Frontend Web UI on port 3000...
start "QA Web UI (Port 3000)" cmd /k "cd web && npm run dev"

echo ============================================================
echo All services launched!
echo - Agent:    http://localhost:3001
echo - Backend:  http://localhost:5000
echo - Web UI:   http://localhost:3000
echo ============================================================
echo Opening http://localhost:3000 in your browser...
timeout /t 5 >nul
start http://localhost:3000

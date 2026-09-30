@echo off
title QA Tester Suite - Initial Setup
echo ============================================================
echo           QA Tester Suite - First-Time Setup
echo ============================================================

:: 1. Setup Environment Files (.env)
echo [1/5] Checking environment files...
if not exist "agent\.env" (
    echo Creating agent\.env from .env.example...
    copy agent\.env.example agent\.env >nul
)
if not exist "backend\.env" (
    echo Creating backend\.env from .env.example...
    copy backend\.env.example backend\.env >nul
)
if not exist "web\.env" (
    echo Creating web\.env from .env.example...
    copy web\.env.example web\.env >nul
)

:: 2. Setup Agent
echo [2/5] Installing Local Agent dependencies and WASM grammars...
cd agent
call npm install
cd ..

:: 3. Setup Web
echo [3/5] Installing Frontend Web dependencies...
cd web
call npm install
cd ..

:: 4. Setup Backend Virtualenv
echo [4/5] Setting up Python backend virtual environment...
cd backend
if not exist "venv" (
    echo Creating Python virtual environment (venv)...
    python -m venv venv
)
echo Installing backend Python requirements...
call venv\Scripts\activate
pip install -r requirements.txt
call deactivate
cd ..

:: 5. Create analysis_output directory
echo [5/5] Creating analysis_output folder...
if not exist "analysis_output" (
    mkdir analysis_output
)

echo ============================================================
echo Setup completed successfully!
echo.
echo NEXT STEPS:
echo 1. Edit backend\.env to set your LLM API key and PostgreSQL credentials.
echo 2. Start PostgreSQL (e.g. docker-compose up -d or local PostgreSQL service).
echo 3. Run start_all.bat to launch the application.
echo ============================================================
pause

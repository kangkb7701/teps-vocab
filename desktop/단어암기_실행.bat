@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=%USERPROFILE%\anaconda3\python.exe"
if not exist "%PY%" set "PY=python"
if not exist "vocab.json" (
    echo [1/2] Building word data ...
    "%PY%" parse_vocab.py
)
echo [2/2] Starting TEPS vocab trainer ...
"%PY%" teps_trainer.py
if errorlevel 1 (
    echo.
    echo [ERROR] Something went wrong. See message above.
    pause
)

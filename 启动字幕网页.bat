@echo off
cd /d "%~dp0"
title Subtitle Web App
where python >nul 2>nul
if %errorlevel%==0 (
    python app.py
) else (
    py app.py
)
echo.
echo Server stopped. Press any key to close...
pause >nul

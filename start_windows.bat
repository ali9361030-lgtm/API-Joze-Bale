@echo off
setlocal
chcp 65001 > nul
title Jozve Android - Local UI
cd /d "%~dp0"
set "PYTHON=python"
where py >nul 2>&1
if not errorlevel 1 set "PYTHON=py -3"
%PYTHON% -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>&1
if errorlevel 1 (
    echo Python 3.10 or newer is required. Install Python and enable its launcher or PATH option.
    pause
    exit /b 1
)
%PYTHON% -c "import requests, docx" >nul 2>&1
if errorlevel 1 (
    %PYTHON% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Dependency installation failed. Check the error above.
        pause
        exit /b 1
    )
)
%PYTHON% run.py %*
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" pause
exit /b %RESULT%

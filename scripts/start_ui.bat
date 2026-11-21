@echo off
chcp 65001 >nul
setlocal
rem The project root, one level above this file, holds .venv, data and the package.
cd /d "%~dp0.."
title Soil Console

echo.
echo   Soil Console
echo   ----------------------------------------
echo.

rem The virtual environment is created once. After that the launcher just runs.
if not exist ".venv\Scripts\python.exe" goto :install
goto :run

:install
echo   First run: creating the virtual environment.
echo   This installs numpy, pandas, scipy, scikit-learn, matplotlib, torch,
echo   flask and waitress. It downloads a few hundred megabytes and can take
echo   several minutes. It only happens once.
echo.
python -m venv .venv
if errorlevel 1 goto :nopython

".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :noinstall

:run
echo.
echo   Starting. The browser opens by itself.
echo   Close this window to stop the console.
echo.
".venv\Scripts\python.exe" -m soilcast web
echo.
echo   The console has stopped.
pause
exit /b 0

:nopython
echo.
echo   Python was not found on this computer.
echo   Install it from https://www.python.org/downloads/ and tick
echo   "Add Python to PATH" during the setup, then run this file again.
echo.
pause
exit /b 1

:noinstall
echo.
echo   The packages could not be installed.
echo   Check the network connection, then delete the .venv folder and run
echo   this file again.
echo.
pause
exit /b 1

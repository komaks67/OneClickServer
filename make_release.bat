@echo off
cd /d "%~dp0"
echo Making the release zip (run build.bat first)
echo.
set "PY="
py -3.13 -c "import sys" >nul 2>&1 && set "PY=py -3.13"
if not defined PY py -3.12 -c "import sys" >nul 2>&1 && set "PY=py -3.12"
if not defined PY py -3.11 -c "import sys" >nul 2>&1 && set "PY=py -3.11"
if not defined PY set "PY=py -3"
%PY% tools\make_release.py
echo.
pause

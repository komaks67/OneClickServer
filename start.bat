@echo off
cd /d "%~dp0"
py -3 -m pip install pywebview >nul 2>&1
py -3 main.py

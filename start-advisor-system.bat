@echo off
setlocal
cd /d "%~dp0"
echo Starting IMAC Guidance Advisor System...
echo URL: http://localhost:8600
echo Demo login: advisor@imac.local / advisor123
venv\Scripts\python.exe system_app.py

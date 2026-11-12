@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Chua cai dat moi truong. Dang chay setup.bat ...
  call setup.bat
)
set PYTHONUTF8=1
.venv\Scripts\python.exe -m streamlit run app.py --server.port 8501 --browser.gatherUsageStats false

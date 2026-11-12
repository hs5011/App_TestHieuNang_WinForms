@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==== PerfTool - Cai dat moi truong ====
where python >nul 2>nul || (echo [LOI] Chua cai Python 3.10+ . Tai tai https://www.python.org/downloads/ & pause & exit /b 1)
if not exist .venv (
  echo Tao moi truong ao .venv ...
  python -m venv .venv || (echo [LOI] Khong tao duoc venv & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || (echo [LOI] Cai thu vien that bai & pause & exit /b 1)
echo Cai trinh duyet Chromium cho Playwright ...
python -m playwright install chromium
echo.
where k6 >nul 2>nul && (echo [OK] Da co k6) || (echo [!] Chua co k6 - cai bang lenh: winget install k6 --source winget)
where jmeter.bat >nul 2>nul && (echo [OK] Da co JMeter trong PATH) || (echo [!] JMeter khong co trong PATH - khai bao tools.jmeter_path trong config\settings.yaml)
echo.
echo ==== Hoan tat. Chay run.bat de mo ung dung ====
pause

@echo off
REM Builds System Monitor into "dist":
REM   dist\SystemMonitor.exe  - CPU / GPU / RAM monitor with temperature alarms, with its own icon
REM Add "nopause" to skip the pause at the end:  build.bat nopause
setlocal
cd /d "%~dp0"

python -m pip install --upgrade pyinstaller -r requirements.txt || goto :error
python system_monitor.py icon system_monitor.ico || goto :error
python -m PyInstaller --noconfirm --onefile --windowed --icon system_monitor.ico --name SystemMonitor system_monitor.py || goto :error

echo.
echo Done:  dist\SystemMonitor.exe
echo Copy system_monitor.ini next to SystemMonitor.exe to keep your settings (it makes a new one otherwise).
if /i not "%~1"=="nopause" pause
exit /b 0

:error
echo.
echo System Monitor build failed - see the messages above.
if /i not "%~1"=="nopause" pause
exit /b 1

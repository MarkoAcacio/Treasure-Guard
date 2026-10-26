@echo off
REM Starts Treasury Guard so phones on the same Wi-Fi can open it.
REM Debug mode stays OFF here: the debugger must never be reachable from other devices.
cd /d "%~dp0"
echo.
echo Open one of these addresses on your phone (same Wi-Fi):
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do for /f "tokens=*" %%b in ("%%a") do echo    http://%%b:5000
echo.
echo Keep this window open. Close it to stop the server.
echo.
".venv\Scripts\python.exe" -m flask --app app run --host 0.0.0.0 --port 5000
pause

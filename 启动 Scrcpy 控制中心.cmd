@echo off
if exist "%~dp0ScrcpyControlCenterFinal.exe" (
    start "" "%~dp0ScrcpyControlCenterFinal.exe"
) else (
    start "" "%~dp0dist\ScrcpyControlCenterFinal.exe"
)
exit /b 0

@echo off
setlocal
set "ROOT=%~dp0"
where powershell.exe >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%atrium.ps1" %*
  exit /b %ERRORLEVEL%
)
where pwsh.exe >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%atrium.ps1" %*
  exit /b %ERRORLEVEL%
)
echo Missing PowerShell. Install Windows PowerShell or PowerShell 7, then rerun atrium.cmd.
exit /b 1

@echo off
setlocal
set "ROOT=%~dp0"

where powershell.exe >nul 2>nul
if errorlevel 1 goto try_pwsh
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%atrium.ps1" %*
exit /b %ERRORLEVEL%

:try_pwsh
where pwsh.exe >nul 2>nul
if errorlevel 1 goto try_system_powershell
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%atrium.ps1" %*
exit /b %ERRORLEVEL%

:try_system_powershell
if exist "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" (
    "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%atrium.ps1" %*
    exit /b %ERRORLEVEL%
)
if exist "%SystemRoot%\SysWOW64\WindowsPowerShell\v1.0\powershell.exe" (
    "%SystemRoot%\SysWOW64\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%atrium.ps1" %*
    exit /b %ERRORLEVEL%
)
if exist "%ProgramFiles%\PowerShell\7\pwsh.exe" (
    "%ProgramFiles%\PowerShell\7\pwsh.exe" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%atrium.ps1" %*
    exit /b %ERRORLEVEL%
)
if exist "%ProgramFiles(x86)%\PowerShell\7\pwsh.exe" (
    "%ProgramFiles(x86)%\PowerShell\7\pwsh.exe" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%atrium.ps1" %*
    exit /b %ERRORLEVEL%
)
goto missing_powershell

:missing_powershell
echo Missing PowerShell. Install Windows PowerShell or PowerShell 7, then rerun atrium.cmd.
exit /b 1

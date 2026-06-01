@echo off
:: PII Proxy - Windows installer launcher.
:: Double-click this file to start the installer.

cd /d "%~dp0"

:: Search for Python 3.9+ via common launchers.
:: Resolve real paths via 'where' and skip Windows Store app-execution-alias stubs
:: (stubs live under WindowsApps and return exit 0 without running Python).
set PYTHON=
for %%p in (python3 python py) do (
    for /f "tokens=*" %%q in ('where %%p 2^>nul') do (
        echo %%q | findstr /i "WindowsApps" >nul 2>&1
        if errorlevel 1 (
            "%%q" -c "import sys; exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
            if not errorlevel 1 ( set PYTHON=%%q & goto :found )
        )
    )
)

:: Python not found — show an error dialog via PowerShell
powershell -Command ^
    "Add-Type -AssemblyName PresentationFramework; ^
    [System.Windows.MessageBox]::Show( ^
        'PII Proxy needs Python 3.9 or newer.`n`nDownload it free from:`nhttps://www.python.org/downloads/`n`nAfter installing Python, try again.', ^
        'Python Required', 'OK', 'Warning')"
exit /b 1

:found
%PYTHON% install.py
if errorlevel 1 (
    echo.
    echo Installation encountered an error. See the window above for details.
    pause
)

#!/usr/bin/env python3
"""
Build self-extracting single-file installers for PII Proxy.

Usage:  python make_dist.py
Output: dist/pii-proxy-installer-mac.command   (double-click in Finder)
        dist/pii-proxy-installer-linux.sh       (bash script)
        dist/pii-proxy-installer-windows.bat    (double-click on Windows)

Each file is self-contained: it embeds all project source files as a
base64-encoded archive, extracts them to ~/pii-proxy on first run, then
launches the GUI installer. Packages (pip/spaCy) are downloaded at install
time and are NOT bundled.

Prerequisites are installed automatically when missing:
  macOS   — Homebrew (brew install python-tk), or downloads python.org .pkg
  Linux   — apt / dnf / pacman / zypper (sudo install python3 + python3-tk)
  Windows — winget, or downloads and silently runs the python.org .exe
"""

from __future__ import annotations

import base64
import io
import stat
import tarfile
import textwrap
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
DIST = ROOT / "dist"

# All project source files to bundle (relative to ROOT).
# pip packages and spaCy models are excluded — they're downloaded at install time.
SOURCE_FILES = [
    "pii_proxy.py",
    "anonymizer.py",
    "config.py",
    "pseudonymizer.py",
    "secret_scan.py",
    "session_map.py",
    "requirements.txt",
    "known_pii.example.yaml",
    "providers/__init__.py",
    "providers/anthropic.py",
    "providers/base.py",
    "providers/openai.py",
    "installer/__init__.py",
    "installer/install.py",
    "installer/manager.py",
]


def _make_targz() -> bytes:
    """tar.gz with every source file rooted at pii-proxy/ — for Mac/Linux."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for rel in SOURCE_FILES:
            path = ROOT / rel
            if not path.exists():
                print(f"  WARNING: {rel} not found, skipping")
                continue
            info = tf.gettarinfo(str(path), arcname=f"pii-proxy/{rel}")
            with path.open("rb") as fh:
                tf.addfile(info, fh)
    return buf.getvalue()


def _make_zip() -> bytes:
    """Zip with every source file rooted at pii-proxy/ — for Windows."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in SOURCE_FILES:
            path = ROOT / rel
            if not path.exists():
                print(f"  WARNING: {rel} not found, skipping")
                continue
            zf.write(path, f"pii-proxy/{rel}")
    return buf.getvalue()


def _b64(data: bytes, wrap: int = 76) -> str:
    return "\n".join(textwrap.wrap(base64.b64encode(data).decode(), wrap))


# ── macOS self-extracting .command ────────────────────────────────────────────

_MAC = r"""#!/bin/bash
# PII Proxy - macOS self-extracting installer.
# Double-click in Finder, or run: bash pii-proxy-installer-mac.command

set -eo pipefail

INSTALL_DIR="$HOME/pii-proxy"

# Confirm reinstall if already present
if [ -d "$INSTALL_DIR" ]; then
    ANSWER=""
    ANSWER=$(osascript <<'OSASCRIPT'
button returned of (display dialog "PII Proxy is already installed at ~/pii-proxy.

Reinstall (existing files will be overwritten)?" buttons {"Cancel", "Reinstall"} default button "Reinstall" with icon caution)
OSASCRIPT
    ) || true
    [ "$ANSWER" = "Reinstall" ] || { echo "Cancelled."; exit 0; }
fi

# Extract embedded archive
echo "Extracting PII Proxy to $INSTALL_DIR ..."
sed -n '/^__ARCHIVE__$/,$p' "$0" | tail -n +2 | base64 -d | tar xz -C "$HOME"
echo "Extraction complete."

# Helpers
_py_tk_ver() {
    "$1" -c "
try:
    import tkinter; print(tkinter.TkVersion)
except Exception: pass
" 2>/dev/null
}
_py_minor() {
    "$1" -c "import sys; print(sys.version_info.major*10+sys.version_info.minor)" 2>/dev/null
}

_find_python() {
    # Sets globals: PYTHON (usable), FOUND_NO_TK, FOUND_OLD_TK, FOUND_VER.
    PYTHON="" FOUND_NO_TK="" FOUND_OLD_TK="" FOUND_VER=""
    local candidates c m tk
    candidates=(
        /usr/local/bin/python3.13 /usr/local/bin/python3.12
        /usr/local/bin/python3.11 /usr/local/bin/python3.10
        /usr/local/bin/python3.9  /usr/local/bin/python3
        /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12
        /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.10
        /opt/homebrew/bin/python3.9  /opt/homebrew/bin/python3
        /Library/Frameworks/Python.framework/Versions/3.13/bin/python3
        /Library/Frameworks/Python.framework/Versions/3.12/bin/python3
        /Library/Frameworks/Python.framework/Versions/3.11/bin/python3
        /Library/Frameworks/Python.framework/Versions/3.10/bin/python3
        /Library/Frameworks/Python.framework/Versions/3.9/bin/python3
        python3.13 python3.12 python3.11 python3.10 python3.9 python3
    )
    for c in "${candidates[@]}"; do
        if [[ "$c" != /* ]]; then c=$(command -v "$c" 2>/dev/null) || continue; fi
        [ -x "$c" ] || continue
        m=$(_py_minor "$c"); [ "${m:-0}" -ge 39 ] 2>/dev/null || continue
        tk=$(_py_tk_ver "$c")
        if [ -z "$tk" ]; then
            [ -z "$FOUND_NO_TK" ] && {
                FOUND_NO_TK="$c"
                FOUND_VER=$("$c" -c "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}')" 2>/dev/null)
            }
            continue
        fi
        if awk "BEGIN{exit($tk >= 8.6 ? 0 : 1)}" 2>/dev/null; then
            PYTHON="$c"; return 0
        else
            [ -z "$FOUND_OLD_TK" ] && FOUND_OLD_TK="$c"
        fi
    done
    return 0
}

_install_python() {
    # Try Homebrew first; fall back to downloading the python.org .pkg.
    local BREW="" ANSWER="" VER URL PY_URL TMPKG

    if command -v brew &>/dev/null; then
        BREW=$(command -v brew)
    elif [ -x /opt/homebrew/bin/brew ]; then
        BREW=/opt/homebrew/bin/brew
    elif [ -x /usr/local/bin/brew ]; then
        BREW=/usr/local/bin/brew
    fi

    if [ -n "$BREW" ]; then
        ANSWER=$(osascript <<'OSASCRIPT'
button returned of (display dialog "Python with Tk support was not found.

Install it automatically via Homebrew? (takes 1-3 minutes)" buttons {"Cancel", "Install via Homebrew"} default button "Install via Homebrew" with icon note)
OSASCRIPT
        ) || true
        if [ "$ANSWER" = "Install via Homebrew" ]; then
            echo "Installing Python + Tk via Homebrew..."
            "$BREW" install python-tk@3.13 2>&1 \
                || "$BREW" install python-tk@3.12 2>&1 \
                || "$BREW" install python-tk 2>&1 \
                || { echo "Homebrew install failed."; return 1; }
            echo "Homebrew install complete."
            return 0
        fi
        return 1
    fi

    # No Homebrew - offer to download the official python.org installer.
    ANSWER=$(osascript <<'OSASCRIPT'
button returned of (display dialog "Python with Tk support was not found, and Homebrew is not installed.

Download and open the official Python installer from python.org? (~45 MB)" buttons {"Cancel", "Download Python"} default button "Download Python" with icon note)
OSASCRIPT
    ) || true

    if [ "$ANSWER" = "Download Python" ]; then
        PY_URL=""
        echo "Locating the latest Python installer..."
        for VER in 3.13.3 3.13.2 3.13.1 3.13.0 3.12.9 3.12.7; do
            URL="https://www.python.org/ftp/python/$VER/python-$VER-macos11.pkg"
            if curl --head --silent --fail "$URL" >/dev/null 2>&1; then
                PY_URL="$URL"; break
            fi
        done
        if [ -z "$PY_URL" ]; then
            osascript -e 'display alert "Download failed" message "Could not locate a Python installer.\n\nPlease visit https://python.org/downloads to install Python manually, then double-click this file again." buttons {"OK"} default button 1 as warning' || true
            exit 1
        fi
        TMPKG="/tmp/python-installer-$RANDOM.pkg"
        echo "Downloading $PY_URL ..."
        curl -L --progress-bar "$PY_URL" -o "$TMPKG" || {
            osascript -e 'display alert "Download failed" message "Could not download Python. Check your internet connection and try again." buttons {"OK"} default button 1 as warning' || true
            exit 1
        }
        echo "Opening installer. After installing Python, double-click this file again."
        open "$TMPKG"
        exit 0
    fi
    return 1
}

_show_error() {
    if [ -n "$FOUND_NO_TK" ]; then
        osascript -e "display alert \"Tk not found\" message \"Python $FOUND_VER was found but does not include Tk.\n\nFix:\n  brew install python-tk@$FOUND_VER\n\nOr download the official Python installer:\n  https://python.org/downloads\" buttons {\"OK\"} default button 1 as warning" || true
    elif [ -n "$FOUND_OLD_TK" ]; then
        local OLD_VER
        OLD_VER=$("$FOUND_OLD_TK" -c "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}')" 2>/dev/null)
        osascript -e "display alert \"Tk too old\" message \"Python $OLD_VER was found, but its bundled Tk 8.5 crashes on macOS 15.\n\nFix:\n  brew install python-tk@$OLD_VER\n\nOr download the official Python installer:\n  https://python.org/downloads\" buttons {\"OK\"} default button 1 as warning" || true
    else
        osascript -e 'display alert "Python 3.9+ required" message "PII Proxy needs Python 3.9 or newer.\n\nDownload it from:\n  https://python.org/downloads" buttons {"OK"} default button 1 as warning' || true
    fi
}

# First scan
_find_python
if [ -n "$PYTHON" ]; then
    "$PYTHON" "$INSTALL_DIR/installer/install.py"
    exit $?
fi

# Try auto-install, then re-scan
_install_python || true
_find_python
if [ -n "$PYTHON" ]; then
    "$PYTHON" "$INSTALL_DIR/installer/install.py"
    exit $?
fi

_show_error
exit 1

__ARCHIVE__
"""

# ── Linux self-extracting .sh ─────────────────────────────────────────────────

_LINUX = r"""#!/bin/bash
# PII Proxy - Linux self-extracting installer.
# Run with: bash pii-proxy-installer-linux.sh

set -eo pipefail

INSTALL_DIR="$HOME/pii-proxy"

# Confirm reinstall if already present
if [ -d "$INSTALL_DIR" ]; then
    echo ""
    read -r -p "PII Proxy already installed at $INSTALL_DIR. Reinstall? [y/N] " ans || true
    case "${ans:-}" in [yY]*) ;; *) echo "Cancelled."; exit 0 ;; esac
fi

# Extract embedded archive
echo "Extracting PII Proxy to $INSTALL_DIR ..."
sed -n '/^__ARCHIVE__$/,$p' "$0" | tail -n +2 | base64 -d | tar xz -C "$HOME"
echo "Extraction complete."

# Detect package manager
PKG_MGR=""
if   command -v apt-get &>/dev/null; then PKG_MGR="apt"
elif command -v dnf     &>/dev/null; then PKG_MGR="dnf"
elif command -v yum     &>/dev/null; then PKG_MGR="yum"
elif command -v pacman  &>/dev/null; then PKG_MGR="pacman"
elif command -v zypper  &>/dev/null; then PKG_MGR="zypper"
fi

_pkg_install_python() {
    echo "Installing Python 3 via $PKG_MGR..."
    case "$PKG_MGR" in
        apt)    sudo apt-get update -qq && sudo apt-get install -y python3 python3-tk python3-pip ;;
        dnf)    sudo dnf install -y python3 python3-tkinter python3-pip ;;
        yum)    sudo yum install -y python3 python3-tkinter python3-pip ;;
        pacman) sudo pacman -S --noconfirm python tk ;;
        zypper) sudo zypper install -y python3 python3-tk python3-pip ;;
        *)      return 1 ;;
    esac
}

_pkg_install_tk() {
    local pyver
    pyver=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
    echo "Installing Tk for Python $pyver via $PKG_MGR..."
    case "$PKG_MGR" in
        apt)    sudo apt-get install -y "python${pyver}-tk" 2>/dev/null || sudo apt-get install -y python3-tk ;;
        dnf)    sudo dnf install -y python3-tkinter ;;
        yum)    sudo yum install -y python3-tkinter ;;
        pacman) sudo pacman -S --noconfirm tk ;;
        zypper) sudo zypper install -y python3-tk ;;
        *)      return 1 ;;
    esac
}

# Find Python 3.9+
PYTHON=""
for c in python3 python3.13 python3.12 python3.11 python3.10 python3.9; do
    command -v "$c" &>/dev/null || continue
    V=$("$c" -c "import sys; print(sys.version_info.major*10+sys.version_info.minor)" 2>/dev/null)
    [ "${V:-0}" -ge 39 ] 2>/dev/null && { PYTHON="$c"; break; }
done

if [ -z "$PYTHON" ]; then
    if [ -n "$PKG_MGR" ]; then
        echo ""
        echo "Python 3.9+ not found. Attempting automatic installation..."
        if _pkg_install_python; then
            for c in python3 python3.13 python3.12 python3.11 python3.10 python3.9; do
                command -v "$c" &>/dev/null || continue
                V=$("$c" -c "import sys; print(sys.version_info.major*10+sys.version_info.minor)" 2>/dev/null)
                [ "${V:-0}" -ge 39 ] 2>/dev/null && { PYTHON="$c"; break; }
            done
        fi
    fi
fi

if [ -z "$PYTHON" ]; then
    echo ""
    echo "ERROR: Python 3.9+ could not be installed automatically."
    echo "Install manually:"
    echo "  sudo apt install python3      (Debian/Ubuntu)"
    echo "  sudo dnf install python3      (Fedora)"
    echo "  sudo pacman -S python         (Arch)"
    echo ""
    read -r -p "Press Enter to exit..." _ || true
    exit 1
fi

# Ensure tkinter is available
if ! "$PYTHON" -c "import tkinter" 2>/dev/null; then
    if [ -n "$PKG_MGR" ]; then
        echo ""
        echo "tkinter not found. Attempting automatic installation..."
        _pkg_install_tk || true
    fi
fi

if ! "$PYTHON" -c "import tkinter" 2>/dev/null; then
    echo ""
    echo "ERROR: tkinter could not be installed automatically."
    echo "Install manually:"
    echo "  sudo apt install python3-tk         (Debian/Ubuntu)"
    echo "  sudo dnf install python3-tkinter    (Fedora)"
    echo "  sudo pacman -S tk                   (Arch)"
    echo ""
    read -r -p "Press Enter to exit..." _ || true
    exit 1
fi

"$PYTHON" "$INSTALL_DIR/installer/install.py"

__ARCHIVE__
"""

# ── Windows self-extracting .bat ──────────────────────────────────────────────
# Uses a zip (not tar.gz) since PowerShell ships Expand-Archive natively.
# '__ARCHIVE__' also appears inside the PowerShell extraction code, so
# LastIndexOf is used to locate the actual payload boundary.

_WINDOWS = r"""@echo off
:: PII Proxy - Windows self-extracting installer.
:: Double-click this file to install.

setlocal enabledelayedexpansion

set "INSTALL_DIR=%USERPROFILE%\pii-proxy"

if exist "%INSTALL_DIR%" (
    set /p "REINSTALL=PII Proxy already installed at %INSTALL_DIR%. Reinstall? [y/N] "
    if /i not "!REINSTALL!"=="y" ( echo Cancelled. & pause & exit /b 0 )
)

echo Extracting PII Proxy to %INSTALL_DIR%...

set "TMPZIP=%TEMP%\pii-proxy-%RANDOM%.zip"

:: Decode the base64 payload (everything after the last __ARCHIVE__ line)
powershell -NoProfile -Command ^
    "$raw = [IO.File]::ReadAllText('%~f0'); " ^
    "$mark = '__ARCH' + 'IVE__'; " ^
    "$idx = $raw.LastIndexOf($mark); " ^
    "$b64 = $raw.Substring($idx + $mark.Length).Trim() -replace '[\r\n]',''; " ^
    "$bytes = [Convert]::FromBase64String($b64); " ^
    "[IO.File]::WriteAllBytes('%TMPZIP%', $bytes)"

if errorlevel 1 ( echo Failed to decode installer archive. & pause & exit /b 1 )

powershell -NoProfile -Command "Expand-Archive -LiteralPath '%TMPZIP%' -DestinationPath '%USERPROFILE%' -Force"
del "%TMPZIP%" 2>nul
echo Extraction complete.

:: Find Python 3.9+
set "PYTHON="
for %%p in (python3 python py) do (
    %%p -c "import sys; exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
    if not errorlevel 1 ( set "PYTHON=%%p" & goto :launch )
)

:: Not found - try winget (ships with Windows 10 1709+ and Windows 11)
echo Python 3.9+ not found. Attempting to install via Windows Package Manager...
winget install Python.Python.3.13 --silent --accept-package-agreements --accept-source-agreements >nul 2>&1
if errorlevel 1 (
    winget install Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements >nul 2>&1
)

:: Check expected user-install locations left by winget / official installer
for %%d in (Python313 Python312 Python311 Python310 Python39) do (
    if exist "%LOCALAPPDATA%\Programs\Python\%%d\python.exe" (
        "%LOCALAPPDATA%\Programs\Python\%%d\python.exe" ^
            -c "import sys; exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
        if not errorlevel 1 (
            set "PYTHON=%LOCALAPPDATA%\Programs\Python\%%d\python.exe"
            goto :launch
        )
    )
)

:: winget not available or failed - download directly from python.org
echo Downloading Python from python.org...
set "PY_EXE=%TEMP%\python-installer-%RANDOM%.exe"

powershell -NoProfile -Command ^
    "$versions = '3.13.3','3.13.2','3.13.1','3.13.0','3.12.9','3.12.7'; " ^
    "foreach ($v in $versions) { " ^
    "  $url = 'https://www.python.org/ftp/python/' + $v + '/python-' + $v + '-amd64.exe'; " ^
    "  try { " ^
    "    Invoke-WebRequest -Uri $url -OutFile '%PY_EXE%' -UseBasicParsing -ErrorAction Stop; " ^
    "    Write-Host ('Downloaded Python ' + $v); break " ^
    "  } catch {} " ^
    "}"

if not exist "%PY_EXE%" (
    powershell -NoProfile -Command ^
        "Add-Type -AssemblyName PresentationFramework; " ^
        "[System.Windows.MessageBox]::Show('Could not download Python.' + [char]10 + [char]10 + 'Please install Python 3.9+ from python.org and re-run this installer.','Python Required','OK','Warning')"
    exit /b 1
)

echo Installing Python silently...
"%PY_EXE%" /quiet InstallAllUsers=0 PrependPath=1 Include_tcltk=1 Include_pip=1
del "%PY_EXE%" 2>nul

:: Re-check known locations after silent install
for %%d in (Python313 Python312 Python311 Python310 Python39) do (
    if exist "%LOCALAPPDATA%\Programs\Python\%%d\python.exe" (
        "%LOCALAPPDATA%\Programs\Python\%%d\python.exe" ^
            -c "import sys; exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
        if not errorlevel 1 (
            set "PYTHON=%LOCALAPPDATA%\Programs\Python\%%d\python.exe"
            goto :launch
        )
    )
)

powershell -NoProfile -Command ^
    "Add-Type -AssemblyName PresentationFramework; " ^
    "[System.Windows.MessageBox]::Show('Python was installed but could not be found.' + [char]10 + [char]10 + 'Please close this window and double-click the installer again.','Setup','OK','Information')"
exit /b 1

:launch
%PYTHON% "%INSTALL_DIR%\installer\install.py"
if errorlevel 1 ( echo. & echo Installation failed. See the window above for details. & pause )

goto :eof
__ARCHIVE__
"""


def build_mac(payload_b64: str) -> str:
    return _MAC + payload_b64 + "\n"


def build_linux(payload_b64: str) -> str:
    return _LINUX + payload_b64 + "\n"


def build_windows(payload_b64: str) -> str:
    return _WINDOWS + payload_b64 + "\n"


def main() -> None:
    DIST.mkdir(exist_ok=True)

    print("Collecting source files...")
    tgz = _make_targz()
    zp  = _make_zip()

    tgz_b64 = _b64(tgz)
    zip_b64 = _b64(zp)

    print(f"  tar.gz  {len(tgz):>8,} B  ->  {len(tgz_b64):>9,} chars base64")
    print(f"  zip     {len(zp):>8,} B  ->  {len(zip_b64):>9,} chars base64")

    mac_out   = DIST / "pii-proxy-installer-mac.command"
    linux_out = DIST / "pii-proxy-installer-linux.sh"
    win_out   = DIST / "pii-proxy-installer-windows.bat"

    mac_out.write_text(build_mac(tgz_b64))
    mac_out.chmod(mac_out.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"  -> {mac_out.relative_to(ROOT)}  ({mac_out.stat().st_size:,} B)")

    linux_out.write_text(build_linux(tgz_b64))
    linux_out.chmod(linux_out.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"  -> {linux_out.relative_to(ROOT)}  ({linux_out.stat().st_size:,} B)")

    win_out.write_text(build_windows(zip_b64), encoding="utf-8")
    print(f"  -> {win_out.relative_to(ROOT)}  ({win_out.stat().st_size:,} B)")

    print("\nDone. Share the files in dist/ -- each is a standalone one-click installer.")
    print("Regenerate any time with:  python make_dist.py")


if __name__ == "__main__":
    main()

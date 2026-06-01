#!/bin/bash
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
H4sIAAfmHWoC/+29a3PjRrYg2J/5K7JR4ynQJkFSryqzTdsqSVWltkrSSCp7PCpdBEgmRVggQAOg
JLZKG/1l76eN2N2ZG7ERu3did7/MD+tfsD9hzzmZCSReJFUuu7vcYrdLJJB58nXy5MnztFpW69tj
5/Y1d4Y8/MOv8mmLT9Xfdnt9I/2Ozzvttc7aH9jtH36DzyyKnRCa/8M/52ftOZvE7oT3Os+et9e2
NjfX162tLzfX1tvPan94/PzuP1PXbU7D4Hbegm82fbOm84+//7c2Nir2f6ez8azzh87mWvtZZ73T
frYF+3+js7n1B9b+Lfd/GATxonLL3n+inyd/bM2isNV3/Rb3r9l0Ho8Df71mGEbteH+fbfuBP5+4
f3FiN/DZMaIH+9tf/41FbhyxPo9vOPfZjufMhpztBPBPix1Nub+9zwaey30o5PhDFo+5G7LZNIpD
7kzY9vF+ZNVqpzDxcZfJNpmOf7WdwB+5l122fXj2+uToeH/HfrF9ume/PTnojeN42m21OmvPrDb8
r9N93n6+xpg5CsJcT7b9eBwGU3fATne/q9eY/Bwd7x1u7y8FiB8CKgdEMOCY9OJxlw1mocdkRS8Y
ON44iGKq2BpTkdobZ9pFEAtKTpwpTXTNnUyDMGZecHnp+pfJb8cNsGJtFAYT9YPJdze8X5Mv5BLx
kCVwnKF95Qc3vg2T2hC/fU8CGtDUqrLZCW6w7w6Pfji0Yent4+2z1w12cPTKPtj7fu+gwd5sH8uH
x0cnZwIYLNi1C4xDZDnJZCvI6sGxLJOvEcC8Oq4qLmY5WzbiUQR4Z8NEqWKn4hHMbq0m58vqO5E7
EBhj0ip7/Jp7vUseO3EcmrKYNpZ6g4rB4k6cuGd8ZjrRAA/BesQ+M6my78hfE2jPuYTvhqgzdGI+
mmClH5ufTZqfDdlnr7ufvel+dgoF6tQlWIieWksLOnFAz0wjIbVGvVZ7wl55Qd/xIjYNpjMPoA6Z
EzMkRvFsatXsaIIIlA6XvWeHgc8BNP6p4Xom35O17jLPjeJzgODxc9htDQAYXlxAwfOLWs0+Pjn6
fn937+QUHtzRcIzWdaclxxgZXbVFiotnyjmjCoOxE7cGwQRaQbqAFbPLh6XvazjKv/3bX+H/7IT/
PONRzMZADjxYfPn8U/l/reZEc3/Ahnwkh2CGYkRd3ImWHF6dNb+Wv6MpzAuHRcOfp0T31MOuQL6b
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

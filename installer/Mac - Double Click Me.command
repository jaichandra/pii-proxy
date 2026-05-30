#!/bin/bash
# PII Proxy — macOS installer launcher.
# Double-click this file in Finder to start the installer.

cd "$(dirname "$0")"

# ── Python selection ──────────────────────────────────────────────────────────
#
# Requirements:
#   • Python 3.9+
#   • Tk 8.6+  (Apple's bundled Tk 8.5.9 calls abort() on macOS 15 — skip it)
#
# Homebrew Python lacks Tk by default; the user needs: brew install python-tk@X.Y
# The python.org installer bundles Tk 8.6+, so it always works.
# Apple's /usr/bin/python3 bundles Tk 8.5.9 — it will crash, so skip it.

_py_tk_version() {
    # Prints Tk version (e.g. "8.6") or nothing on failure.
    "$1" -c "
try:
    import tkinter
    print(tkinter.TkVersion)
except Exception:
    pass
" 2>/dev/null
}

_py_minor() {
    "$1" -c \
        "import sys; print(sys.version_info.major*10+sys.version_info.minor)" \
        2>/dev/null
}

PYTHON=""
FOUND_NO_TK=""     # Python found but Tk missing entirely
FOUND_OLD_TK=""    # Python found but Tk < 8.6 (crashes)
FOUND_VER=""       # version string for the first no-Tk candidate (for brew hint)

# Search order: Homebrew (Intel + Apple Silicon), python.org, PATH.
# /usr/bin/python3 is intentionally omitted — its Tk 8.5 crashes on macOS 15.
search_candidates=(
    /usr/local/bin/python3.13
    /usr/local/bin/python3.12
    /usr/local/bin/python3.11
    /usr/local/bin/python3.10
    /usr/local/bin/python3.9
    /usr/local/bin/python3
    /opt/homebrew/bin/python3.13
    /opt/homebrew/bin/python3.12
    /opt/homebrew/bin/python3.11
    /opt/homebrew/bin/python3.10
    /opt/homebrew/bin/python3.9
    /opt/homebrew/bin/python3
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3
    /Library/Frameworks/Python.framework/Versions/3.11/bin/python3
    /Library/Frameworks/Python.framework/Versions/3.10/bin/python3
    /Library/Frameworks/Python.framework/Versions/3.9/bin/python3
    python3.13 python3.12 python3.11 python3.10 python3.9 python3
)

for candidate in "${search_candidates[@]}"; do
    # Resolve bare name → full path
    if [[ "$candidate" != /* ]]; then
        candidate=$(command -v "$candidate" 2>/dev/null) || continue
    fi
    [ -x "$candidate" ] || continue

    # Must be Python 3.9+
    minor=$(_py_minor "$candidate")
    [ "${minor:-0}" -ge 39 ] 2>/dev/null || continue

    tkver=$(_py_tk_version "$candidate")

    if [ -z "$tkver" ]; then
        # No Tk at all
        if [ -z "$FOUND_NO_TK" ]; then
            FOUND_NO_TK="$candidate"
            FOUND_VER=$("$candidate" -c \
                "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}')" \
                2>/dev/null)
        fi
        continue
    fi

    # Require Tk 8.6+ (8.5.x aborts on macOS 15)
    if awk "BEGIN{exit($tkver >= 8.6 ? 0 : 1)}" 2>/dev/null; then
        PYTHON="$candidate"
        break
    else
        [ -z "$FOUND_OLD_TK" ] && FOUND_OLD_TK="$candidate"
    fi
done

# ── Launch or show a helpful error ────────────────────────────────────────────

if [ -n "$PYTHON" ]; then
    "$PYTHON" install.py
    exit $?
fi

if [ -n "$FOUND_NO_TK" ]; then
    osascript -e "
        display alert \"Tk support not found\" message \
            \"Python $FOUND_VER was found, but it does not include the Tk graphics library needed for the installer.\n\nFix — run this command in Terminal, then double-click this file again:\n\n  brew install python-tk@$FOUND_VER\n\nAlternatively, download the official Python installer (always includes Tk):\n  https://www.python.org/downloads/\" \
            buttons {\"OK\"} default button 1 as warning"
    exit 1
fi

if [ -n "$FOUND_OLD_TK" ]; then
    OLD_VER=$("$FOUND_OLD_TK" -c \
        "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}')" \
        2>/dev/null)
    osascript -e "
        display alert \"Tk version too old\" message \
            \"Python $OLD_VER was found, but its bundled Tk is version 8.5, which crashes on macOS 15.\n\nFix option 1 — install a newer Tk via Homebrew:\n  brew install python-tk@$OLD_VER\n\nFix option 2 — download the official Python installer (bundles Tk 8.6+):\n  https://www.python.org/downloads/\n\nThen double-click this file again.\" \
            buttons {\"OK\"} default button 1 as warning"
    exit 1
fi

osascript -e '
    display alert "Python 3.9 or newer is required" message \
        "PII Proxy needs Python 3.9 or newer.\n\nDownload it free from:\n  https://www.python.org/downloads/\n\nAfter installing, double-click this file again." \
        buttons {"OK"} default button 1 as warning'
exit 1

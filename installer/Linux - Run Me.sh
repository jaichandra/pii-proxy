#!/bin/bash
# PII Proxy — Linux installer launcher.
# Run with:  bash "installer/Linux - Run Me.sh"
# Or make it executable first:
#   chmod +x "installer/Linux - Run Me.sh"
#   "./installer/Linux - Run Me.sh"

cd "$(dirname "$0")"

# Find Python 3.9+
PYTHON=""
for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9; do
    if command -v "$candidate" &>/dev/null; then
        VER=$("$candidate" -c \
            "import sys; print(sys.version_info.major*10+sys.version_info.minor)" \
            2>/dev/null)
        if [ "${VER:-0}" -ge 39 ] 2>/dev/null; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    MSG="PII Proxy needs Python 3.9 or newer.\n\nInstall it with:\n  sudo apt install python3    (Debian/Ubuntu)\n  sudo dnf install python3    (Fedora)\n\nOr download from https://www.python.org/downloads/"

    # Try graphical error dialogs before falling back to text
    if command -v zenity &>/dev/null; then
        zenity --error --title="Python Required" --text="$MSG" --no-wrap 2>/dev/null
    elif command -v kdialog &>/dev/null; then
        kdialog --error "$MSG" --title "Python Required"
    else
        echo ""
        echo "ERROR: Python 3.9+ is required."
        echo ""
        echo "Install with:"
        echo "  sudo apt install python3   (Debian/Ubuntu)"
        echo "  sudo dnf install python3   (Fedora)"
        echo ""
        echo "Or download from: https://www.python.org/downloads/"
        echo ""
        read -r -p "Press Enter to exit..."
    fi
    exit 1
fi

# Check for tkinter (sometimes shipped separately on Linux)
if ! "$PYTHON" -c "import tkinter" 2>/dev/null; then
    echo ""
    echo "The tkinter GUI library is missing. Install it with:"
    echo "  sudo apt install python3-tk   (Debian/Ubuntu)"
    echo "  sudo dnf install python3-tkinter   (Fedora)"
    echo ""
    read -r -p "Press Enter to exit..."
    exit 1
fi

"$PYTHON" install.py

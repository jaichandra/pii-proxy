#!/usr/bin/env python3
"""
PII Proxy Installer
===================
Guides users through a complete installation with no technical knowledge needed.
Requires only Python 3.9+ — no additional packages.

Run via the platform launcher in this folder (double-click it), or:
    python3 installer/install.py
"""

from __future__ import annotations

import os
import sys
import platform
import subprocess

# Tk is not included with every Python build (e.g. Homebrew Python lacks it).
# Show a friendly error instead of a raw ImportError traceback.
try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:
    _msg = (
        "PII Proxy's installer needs Tk/tkinter for its graphical interface,\n"
        "but your Python installation does not include it.\n\n"
    )
    _py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    if platform.system() == "Darwin":
        _msg += (
            f"Fix option 1 — Homebrew:\n"
            f"  brew install python-tk@{_py_ver}\n\n"
            f"Fix option 2 — download the official Python installer (includes Tk):\n"
            f"  https://www.python.org/downloads/"
        )
        subprocess.run(
            [
                "osascript", "-e",
                f'display alert "Tk not found" message "{_msg}" '
                f'buttons {{"OK"}} default button 1 as warning',
            ],
            capture_output=True,
        )
    elif platform.system() == "Linux":
        _msg += (
            "Install with:\n"
            "  sudo apt install python3-tk   (Debian / Ubuntu)\n"
            "  sudo dnf install python3-tkinter   (Fedora)"
        )
        print(_msg, file=sys.stderr)
    else:
        print(_msg, file=sys.stderr)
    sys.exit(1)

import queue
import shutil
import socket
import threading
from pathlib import Path


# ── project layout ────────────────────────────────────────────────────────────
HERE  = Path(__file__).parent.resolve()   # .../pii-proxy/installer/
ROOT  = HERE.parent.resolve()              # .../pii-proxy/
VENV  = ROOT / "venv"

if platform.system() == "Windows":
    VENV_PY = VENV / "Scripts" / "python.exe"
else:
    VENV_PY = VENV / "bin" / "python"

PII_HOME       = Path.home() / ".pii-proxy"
KNOWN_PII_PATH = PII_HOME / "known_pii.yaml"
MAIN_SCRIPT    = ROOT / "pii_proxy.py"
MANAGER_SCRIPT = HERE / "manager.py"
PLIST_LABEL    = "com.jai.pii-proxy"


# ── design tokens ─────────────────────────────────────────────────────────────
BG      = "#f8f9fa"
ACCENT  = "#2563eb"
WHITE   = "#ffffff"
FG_MAIN = "#111827"
FG_BODY = "#374151"
FG_MUTE = "#6b7280"
SUCCESS = "#16a34a"
DANGER  = "#dc2626"

F_TITLE = ("Helvetica Neue", 22, "bold")
F_HEAD  = ("Helvetica Neue", 14, "bold")
F_BODY  = ("Helvetica Neue", 12)
F_SMALL = ("Helvetica Neue", 10)
F_MONO  = ("Courier", 11)
F_BTN   = ("Helvetica Neue", 12, "bold")

PAD = 24


# ── install steps (run in background thread) ──────────────────────────────────

def _put(q: queue.Queue, kind: str, data=None) -> None:
    q.put((kind, data))


def step_create_venv(q: queue.Queue) -> None:
    _put(q, "log", "Creating Python environment…")
    subprocess.run(
        [sys.executable, "-m", "venv", str(VENV)],
        check=True, capture_output=True,
    )
    _put(q, "log", "✓ Python environment ready")


def step_install_packages(q: queue.Queue) -> None:
    _put(q, "log", "Installing packages (this may take a minute)…")
    py = str(VENV_PY)
    subprocess.run(
        [py, "-m", "pip", "install", "-q", "--upgrade", "pip"],
        check=True, capture_output=True,
    )
    subprocess.run(
        [py, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")],
        check=True, capture_output=True,
    )
    _put(q, "log", "✓ Packages installed")


def step_download_model(q: queue.Queue) -> None:
    _put(q, "log", "Downloading language model (~200 MB, one-time)…")
    py = str(VENV_PY)
    for model in ("en_core_web_lg", "en_core_web_sm"):
        r = subprocess.run(
            [py, "-m", "spacy", "download", model],
            capture_output=True,
        )
        if r.returncode == 0:
            _put(q, "log", f"✓ Language model ready ({model})")
            return
    raise RuntimeError("Could not download the spaCy language model.")


def step_write_pii_config(names: list, emails: list, phones: list) -> None:
    PII_HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    if KNOWN_PII_PATH.exists():
        return  # preserve an existing config

    def _yaml_list(items: list) -> str:
        clean = [s.strip() for s in items if s.strip()]
        if not clean:
            return "[]"
        return "\n" + "\n".join(f"    - {v}" for v in clean)

    content = (
        f"identity:\n"
        f"  names: {_yaml_list(names)}\n"
        f"  emails: {_yaml_list(emails)}\n"
        f"  phones: {_yaml_list(phones)}\n"
        f"  addresses: []\n\n"
        f"employer:\n"
        f"  names: []\n"
        f"  domains: []\n\n"
        f"ignore:\n"
        f"  - 8082\n"
        f"  - 127.0.0.1\n"
        f"  - localhost\n"
    )
    KNOWN_PII_PATH.write_text(content)
    KNOWN_PII_PATH.chmod(0o600)


def step_setup_service(q: queue.Queue) -> None:
    _put(q, "log", "Configuring auto-start service…")
    system = platform.system()
    if system == "Darwin":
        _service_mac()
        _put(q, "log", "✓ Auto-start configured (launchd)")
    elif system == "Linux":
        _service_linux()
        _put(q, "log", "✓ Auto-start configured")
    elif system == "Windows":
        _service_windows()
        _put(q, "log", "✓ Auto-start configured (Task Scheduler)")
    else:
        _put(q, "log", f"⚠ Auto-start not supported on {system}")


def _service_mac() -> None:
    plist_dir  = Path.home() / "Library" / "LaunchAgents"
    plist_path = plist_dir / f"{PLIST_LABEL}.plist"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"\n'
        f'  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        f'<plist version="1.0"><dict>\n'
        f'  <key>Label</key><string>{PLIST_LABEL}</string>\n'
        f'  <key>ProgramArguments</key>\n'
        f'  <array>\n'
        f'    <string>{VENV_PY}</string>\n'
        f'    <string>{MAIN_SCRIPT}</string>\n'
        f'  </array>\n'
        f'  <key>WorkingDirectory</key><string>{ROOT}</string>\n'
        f'  <key>RunAtLoad</key><true/>\n'
        f'  <key>KeepAlive</key><true/>\n'
        f'  <key>StandardOutPath</key><string>/tmp/pii-proxy.log</string>\n'
        f'  <key>StandardErrorPath</key><string>/tmp/pii-proxy.err</string>\n'
        f'  <key>EnvironmentVariables</key>\n'
        f'  <dict>\n'
        f'    <key>PATH</key><string>/usr/local/bin:/usr/bin:/bin</string>\n'
        f'  </dict>\n'
        f'</dict></plist>\n'
    )
    uid = str(os.getuid())
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}", str(plist_path)],
        capture_output=True,
    )
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
        check=True, capture_output=True,
    )


def _service_linux() -> None:
    py = str(VENV_PY)
    if shutil.which("systemctl"):
        svc_dir = Path.home() / ".config" / "systemd" / "user"
        svc_dir.mkdir(parents=True, exist_ok=True)
        (svc_dir / "pii-proxy.service").write_text(
            f"[Unit]\nDescription=PII Proxy\nAfter=network.target\n\n"
            f"[Service]\nType=simple\nExecStart={py} {MAIN_SCRIPT}\n"
            f"WorkingDirectory={ROOT}\nRestart=always\nRestartSec=5\n\n"
            f"[Install]\nWantedBy=default.target\n"
        )
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", "pii-proxy"],
            capture_output=True,
        )
    else:
        autostart = Path.home() / ".config" / "autostart"
        autostart.mkdir(parents=True, exist_ok=True)
        (autostart / "pii-proxy.desktop").write_text(
            f"[Desktop Entry]\nType=Application\nName=PII Proxy\n"
            f"Exec={py} {MAIN_SCRIPT}\nX-GNOME-Autostart-enabled=true\n"
        )


def _service_windows() -> None:
    py = str(VENV_PY)
    subprocess.run(
        [
            "schtasks", "/create",
            "/tn", "PII Proxy",
            "/tr", f'"{py}" "{MAIN_SCRIPT}"',
            "/sc", "ONLOGON",
            "/rl", "LIMITED",
            "/f",
        ],
        check=True, capture_output=True,
    )
    subprocess.run(["schtasks", "/run", "/tn", "PII Proxy"], capture_output=True)


def step_create_shortcut(q: queue.Queue) -> None:
    _put(q, "log", "Creating desktop shortcut for PII Proxy Manager…")
    system = platform.system()
    if system == "Darwin":
        _shortcut_mac()
    elif system == "Linux":
        _shortcut_linux()
    elif system == "Windows":
        _shortcut_windows()
    _put(q, "log", "✓ Desktop shortcut created")


def _shortcut_mac() -> None:
    desktop = Path.home() / "Desktop"
    app     = desktop / "PII Proxy Manager.app"
    macos   = app / "Contents" / "MacOS"
    macos.mkdir(parents=True, exist_ok=True)
    (app / "Contents" / "Info.plist").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"\n'
        '  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        '  <key>CFBundleName</key><string>PII Proxy Manager</string>\n'
        '  <key>CFBundleExecutable</key><string>launcher</string>\n'
        '  <key>CFBundleIdentifier</key><string>com.pii-proxy.manager</string>\n'
        '  <key>CFBundleVersion</key><string>1.0</string>\n'
        '  <key>LSMinimumSystemVersion</key><string>10.13</string>\n'
        '</dict></plist>\n'
    )
    launcher = macos / "launcher"
    launcher.write_text(f'#!/bin/bash\nexec "{VENV_PY}" "{MANAGER_SCRIPT}"\n')
    launcher.chmod(0o755)


def _shortcut_linux() -> None:
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        return
    shortcut = desktop / "PII Proxy Manager.desktop"
    shortcut.write_text(
        f"[Desktop Entry]\nName=PII Proxy Manager\n"
        f"Comment=Start, stop, and configure PII Proxy\n"
        f"Exec={VENV_PY} {MANAGER_SCRIPT}\nTerminal=false\nType=Application\n"
    )
    shortcut.chmod(0o755)


def _shortcut_windows() -> None:
    desktop = Path.home() / "Desktop"
    lnk = str(desktop / "PII Proxy Manager.lnk").replace("\\", "\\\\")
    ps = (
        f"$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{lnk}'); "
        f"$s.TargetPath = '{VENV_PY}'; "
        f"$s.Arguments = '\"{MANAGER_SCRIPT}\"'; "
        f"$s.WorkingDirectory = '{ROOT}'; "
        f"$s.Description = 'PII Proxy Manager'; "
        f"$s.Save()"
    )
    subprocess.run(["powershell", "-Command", ps], capture_output=True)


# ── Reusable UI helpers ───────────────────────────────────────────────────────

def _btn(parent, text: str, cmd, primary: bool = True) -> tk.Button:
    return tk.Button(
        parent, text=text, command=cmd, font=F_BTN,
        relief="flat", cursor="hand2",
        bg=ACCENT if primary else "#e5e7eb",
        fg=WHITE if primary else FG_MAIN,
        activebackground="#1d4ed8" if primary else "#d1d5db",
        activeforeground=WHITE if primary else FG_MAIN,
        padx=20, pady=8,
    )


class _LabeledEntry(tk.Frame):
    """A labelled text entry with optional placeholder text."""

    def __init__(self, parent, label: str, placeholder: str = ""):
        super().__init__(parent, bg=BG)
        tk.Label(self, text=label, bg=BG, fg=FG_BODY,
                 font=F_BODY, anchor="w").pack(fill="x")
        self.var = tk.StringVar()
        self._entry = tk.Entry(
            self, textvariable=self.var, font=F_BODY,
            relief="solid", bd=1, fg=FG_MAIN, bg=WHITE,
            highlightthickness=1, highlightcolor=ACCENT,
            highlightbackground="#d1d5db",
        )
        self._entry.pack(fill="x", ipady=6, pady=(2, 0))
        if placeholder:
            self._attach_placeholder(placeholder)

    def _attach_placeholder(self, text: str) -> None:
        entry = self._entry
        entry.insert(0, text)
        entry.configure(fg=FG_MUTE)

        def on_in(_e):
            if entry.get() == text:
                entry.delete(0, "end")
                entry.configure(fg=FG_MAIN)

        def on_out(_e):
            if not entry.get():
                entry.insert(0, text)
                entry.configure(fg=FG_MUTE)

        entry.bind("<FocusIn>",  on_in)
        entry.bind("<FocusOut>", on_out)

    @property
    def value(self) -> str:
        return self.var.get().strip()


# ── Pages ─────────────────────────────────────────────────────────────────────

class WelcomePage(tk.Frame):
    def __init__(self, parent, app: "InstallerApp"):
        super().__init__(parent, bg=BG)
        self._app = app
        self._build()

    def _build(self) -> None:
        tk.Label(self, text="🛡  PII Proxy", font=F_TITLE,
                 bg=BG, fg=ACCENT).pack(pady=(PAD, 2))
        tk.Label(self, text="Automatic privacy protection for AI assistants",
                 font=F_BODY, bg=BG, fg=FG_MUTE).pack()

        card = tk.Frame(self, bg=WHITE, relief="solid", bd=1)
        card.pack(fill="x", pady=PAD, padx=2)
        tk.Label(
            card,
            text=(
                "PII Proxy runs silently in the background.\n\n"
                "It replaces your real name, email, phone number,\n"
                "and other personal details with believable fake values\n"
                "before anything reaches the AI — then quietly restores\n"
                "the originals in responses.\n\n"
                "No personal information ever leaves your computer."
            ),
            font=F_BODY, bg=WHITE, fg=FG_BODY,
            justify="left", padx=PAD, pady=PAD,
        ).pack(anchor="w")

        tk.Label(self, text="Setup takes about 3–5 minutes.",
                 font=F_SMALL, bg=BG, fg=FG_MUTE).pack(pady=(0, PAD))
        _btn(self, "Get Started →", self._app.show_info).pack()


class InfoPage(tk.Frame):
    def __init__(self, parent, app: "InstallerApp"):
        super().__init__(parent, bg=BG)
        self._app = app
        self._build()

    def _build(self) -> None:
        tk.Label(self, text="What should PII Proxy protect?",
                 font=F_HEAD, bg=BG, fg=FG_MAIN).pack(anchor="w", pady=(PAD, 2))
        tk.Label(self,
                 text="You can always add more from the Manager app later.",
                 font=F_BODY, bg=BG, fg=FG_MUTE).pack(anchor="w", pady=(0, PAD))

        self._names  = _LabeledEntry(self, "Your full name(s)",
                                     "Jane Smith, JS, Jane")
        self._emails = _LabeledEntry(self, "Your email address(es)",
                                     "jane@example.com, work@company.com")
        self._phones = _LabeledEntry(self, "Your phone number(s)",
                                     "+1 555-123-4567")
        for w in (self._names, self._emails, self._phones):
            w.pack(fill="x", pady=4)

        tk.Label(self, text="Separate multiple values with commas.",
                 font=F_SMALL, bg=BG, fg=FG_MUTE).pack(anchor="w", pady=(4, 0))

        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", pady=(PAD, 0))
        _btn(row, "← Back", self._app.show_welcome, primary=False).pack(side="left")
        _btn(row, "Install Now →", self._next).pack(side="right")

    def _next(self) -> None:
        def _split(raw: str) -> list:
            return [v.strip() for v in raw.split(",") if v.strip()]

        self._app.show_install(
            names  = _split(self._names.value),
            emails = _split(self._emails.value),
            phones = _split(self._phones.value),
        )


class InstallPage(tk.Frame):
    def __init__(self, parent, app: "InstallerApp",
                 names: list, emails: list, phones: list):
        super().__init__(parent, bg=BG)
        self._app    = app
        self._names  = names
        self._emails = emails
        self._phones = phones
        self._q: queue.Queue = queue.Queue()
        self._build()
        self.after(120, self._start_worker)

    def _build(self) -> None:
        tk.Label(self, text="Installing…", font=F_HEAD,
                 bg=BG, fg=FG_MAIN).pack(anchor="w", pady=(PAD, 8))

        self._bar = ttk.Progressbar(self, mode="indeterminate", length=460)
        self._bar.pack(fill="x", pady=(0, 12))
        self._bar.start(14)

        self._log = tk.Text(
            self, height=13, font=F_MONO, bg="#1e1e1e", fg="#d4d4d4",
            relief="flat", state="disabled", wrap="word",
        )
        self._log.pack(fill="both", expand=True)

    def _append(self, line: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", line + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _start_worker(self) -> None:
        threading.Thread(target=self._worker, daemon=True).start()
        self.after(200, self._poll)

    def _worker(self) -> None:
        try:
            step_create_venv(self._q)
            step_install_packages(self._q)
            step_download_model(self._q)
            step_write_pii_config(self._names, self._emails, self._phones)
            _put(self._q, "log", "✓ PII configuration file saved")
            step_setup_service(self._q)
            step_create_shortcut(self._q)
            _put(self._q, "done", None)
        except Exception as exc:
            _put(self._q, "error", str(exc))

    def _poll(self) -> None:
        while True:
            try:
                kind, data = self._q.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._append(data)
            elif kind == "done":
                self._bar.stop()
                self._bar.configure(mode="determinate", value=100)
                self._append("\n✅  Installation complete!")
                self.after(600, self._app.show_done)
                return
            elif kind == "error":
                self._bar.stop()
                self._append(f"\n❌  Error: {data}")
                messagebox.showerror(
                    "Installation failed",
                    f"Something went wrong:\n\n{data}\n\n"
                    "Check the log above for clues, or open a GitHub issue.",
                )
                return
        self.after(200, self._poll)


class DonePage(tk.Frame):
    def __init__(self, parent, app: "InstallerApp"):
        super().__init__(parent, bg=BG)
        self._app = app
        self._build()

    def _build(self) -> None:
        tk.Label(self, text="✅  You're all set!", font=F_TITLE,
                 bg=BG, fg=SUCCESS).pack(pady=(PAD, 4))
        tk.Label(self,
                 text="PII Proxy is running and will start automatically on login.",
                 font=F_BODY, bg=BG, fg=FG_BODY).pack()

        card = tk.Frame(self, bg=WHITE, relief="solid", bd=1)
        card.pack(fill="x", pady=PAD, padx=2)
        tk.Label(
            card,
            text=(
                "Next steps:\n\n"
                "1.  Restart your terminal and Claude Code\n"
                "       (so the new proxy URL takes effect)\n\n"
                "2.  Use “PII Proxy Manager” on your Desktop to\n"
                "       add more names, emails, or other details\n\n"
                "3.  That's it — your information is now protected!"
            ),
            font=F_BODY, bg=WHITE, fg=FG_BODY,
            justify="left", padx=PAD, pady=PAD,
        ).pack(anchor="w")

        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", pady=(0, PAD))
        _btn(row, "Open Manager", self._open_manager).pack(side="left")
        _btn(row, "Close", self._app.root.destroy, primary=False).pack(side="right")

    def _open_manager(self) -> None:
        subprocess.Popen(
            [str(VENV_PY), str(MANAGER_SCRIPT)],
            start_new_session=True,
        )
        self._app.root.destroy()


# ── App shell ─────────────────────────────────────────────────────────────────

class InstallerApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("PII Proxy Installer")
        self.root.geometry("520x570")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        # accent stripe
        tk.Frame(self.root, bg=ACCENT, height=4).pack(fill="x")
        self._content = tk.Frame(self.root, bg=BG)
        self._content.pack(fill="both", expand=True, padx=PAD, pady=PAD)
        self._page = None
        self.show_welcome()

    def _switch(self, cls, **kw) -> None:
        if self._page:
            self._page.destroy()
        self._page = cls(self._content, self, **kw)
        self._page.pack(fill="both", expand=True)

    def show_welcome(self) -> None:
        self._switch(WelcomePage)

    def show_info(self) -> None:
        self._switch(InfoPage)

    def show_install(self, names: list, emails: list, phones: list) -> None:
        self._switch(InstallPage, names=names, emails=emails, phones=phones)

    def show_done(self) -> None:
        self._switch(DonePage)

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    InstallerApp().run()

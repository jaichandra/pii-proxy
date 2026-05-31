#!/usr/bin/env python3
"""
PII Proxy Manager
=================
Start, stop, and configure PII Proxy without opening a terminal.

Launched by the desktop shortcut the installer creates, or directly:
    venv/bin/python installer/manager.py
"""

from __future__ import annotations

import os
import sys
import shutil
import socket
import platform
import subprocess
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from pathlib import Path


# ── paths ─────────────────────────────────────────────────────────────────────
HERE        = Path(__file__).parent.resolve()
ROOT        = HERE.parent.resolve()
VENV        = ROOT / "venv"
VENV_PY     = VENV / ("Scripts/python.exe" if platform.system() == "Windows"
                       else "bin/python")
PII_HOME    = Path.home() / ".pii-proxy"
KNOWN_PII   = PII_HOME / "known_pii.yaml"
MAIN_SCRIPT = ROOT / "pii_proxy.py"
PLIST_LABEL = "com.jai.pii-proxy"
PORT        = 8082


# ── design tokens (match installer palette) ───────────────────────────────────
BG      = "#f8f9fa"
ACCENT  = "#2563eb"
WHITE   = "#ffffff"
FG_MAIN = "#111827"
FG_BODY = "#374151"
FG_MUTE = "#6b7280"
SUCCESS = "#16a34a"
DANGER  = "#dc2626"
BORDER  = "#e5e7eb"

F_TITLE = ("Helvetica Neue", 18, "bold")
F_HEAD  = ("Helvetica Neue", 13, "bold")
F_BODY  = ("Helvetica Neue", 12)
F_SMALL = ("Helvetica Neue", 10)
F_MONO  = ("Courier", 11)
F_BTN   = ("Helvetica Neue", 11, "bold")

PAD = 20


# ── proxy status ──────────────────────────────────────────────────────────────

def is_running() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
            return True
    except OSError:
        return False


# ── service control ───────────────────────────────────────────────────────────

def start_proxy() -> None:
    system = platform.system()
    if system == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"
        uid = str(os.getuid())
        subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)],
                       capture_output=True)
    elif system == "Linux":
        if shutil.which("systemctl"):
            subprocess.run(["systemctl", "--user", "start", "pii-proxy"],
                           capture_output=True)
        else:
            subprocess.Popen([str(VENV_PY), str(MAIN_SCRIPT)],
                             start_new_session=True)
    elif system == "Windows":
        subprocess.run(["schtasks", "/run", "/tn", "PII Proxy"],
                       capture_output=True)


def stop_proxy() -> None:
    system = platform.system()
    if system == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"
        uid = str(os.getuid())
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist)],
                       capture_output=True)
    elif system == "Linux":
        if shutil.which("systemctl"):
            subprocess.run(["systemctl", "--user", "stop", "pii-proxy"],
                           capture_output=True)
        else:
            subprocess.run(["pkill", "-f", "pii_proxy.py"], capture_output=True)
    elif system == "Windows":
        subprocess.run(["schtasks", "/end", "/tn", "PII Proxy"],
                       capture_output=True)


def restart_proxy() -> None:
    stop_proxy()
    time.sleep(1.2)
    start_proxy()


# ── YAML helpers (pyyaml available in venv) ───────────────────────────────────

def _load_yaml() -> dict | None:
    """Returns parsed config, or None if pyyaml is unavailable."""
    try:
        import yaml
    except ImportError:
        return None
    if not KNOWN_PII.exists():
        return _empty_config()
    with KNOWN_PII.open() as fh:
        data = yaml.safe_load(fh) or {}
    return _normalise(data)


def _save_yaml(data: dict) -> None:
    import yaml
    PII_HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    with KNOWN_PII.open("w") as fh:
        yaml.dump(data, fh, default_flow_style=False,
                  allow_unicode=True, sort_keys=False)
    KNOWN_PII.chmod(0o600)


def _empty_config() -> dict:
    return {
        "identity": {
            "names": [], "emails": [], "phones": [], "addresses": [],
        },
        "employer": {"names": [], "domains": []},
        "ignore":   ["8082", "127.0.0.1", "localhost"],
    }


def _normalise(raw: dict) -> dict:
    cfg = _empty_config()
    if isinstance(raw.get("identity"), dict):
        ident = raw["identity"]
        for key in ("names", "emails", "phones", "addresses"):
            cfg["identity"][key] = list(ident.get(key) or [])
    if isinstance(raw.get("employer"), dict):
        emp = raw["employer"]
        cfg["employer"]["names"]   = list(emp.get("names")   or [])
        cfg["employer"]["domains"] = list(emp.get("domains") or [])
    if isinstance(raw.get("ignore"), list):
        cfg["ignore"] = [str(v) for v in raw["ignore"]]
    # pass through unknown top-level keys (e.g. family, projects)
    for k, v in raw.items():
        if k not in cfg:
            cfg[k] = v
    return cfg


# ── Reusable widget helpers ───────────────────────────────────────────────────

def _btn(parent, text: str, cmd, primary: bool = True,
         danger: bool = False, **kw) -> tk.Button:
    if danger:
        bg, abg, fg = "#fee2e2", "#fecaca", "#991b1b"
    elif primary:
        bg, abg, fg = "#dbeafe", "#bfdbfe", "#1e3a8a"
    else:
        bg, abg, fg = "#e5e7eb","#d1d5db", FG_MAIN
    return tk.Button(
        parent, text=text, command=cmd, font=F_BTN,
        relief="flat", cursor="hand2",
        bg=bg, fg=fg, activebackground=abg, activeforeground=fg,
        padx=14, pady=6, **kw,
    )


class _EditableList(tk.Frame):
    """A labelled Listbox with Add / Remove buttons."""

    def __init__(self, parent, label: str, items: list, height: int = 4):
        super().__init__(parent, bg=WHITE)

        tk.Label(self, text=label, font=F_HEAD, bg=WHITE,
                 fg=FG_MAIN, anchor="w").pack(fill="x")

        lb_frame = tk.Frame(self, bg=WHITE)
        lb_frame.pack(fill="x")

        sb = tk.Scrollbar(lb_frame, orient="vertical")
        self._lb = tk.Listbox(
            lb_frame, font=F_BODY, height=height,
            selectmode="single", relief="solid", bd=1,
            yscrollcommand=sb.set,
            activestyle="dotbox",
            selectbackground=ACCENT, selectforeground=WHITE,
            bg=WHITE, fg=FG_MAIN,
        )
        sb.config(command=self._lb.yview)
        self._lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

        for item in items:
            self._lb.insert("end", str(item))

        ctrl = tk.Frame(self, bg=WHITE)
        ctrl.pack(fill="x", pady=(4, 0))
        _btn(ctrl, "+ Add",  self._add).pack(side="left", padx=(0, 6))
        _btn(ctrl, "Remove", self._remove, primary=False).pack(side="left")

    def _add(self) -> None:
        val = simpledialog.askstring("Add value", "Enter a new value:",
                                     parent=self)
        if val and val.strip():
            self._lb.insert("end", val.strip())

    def _remove(self) -> None:
        sel = self._lb.curselection()
        if sel:
            self._lb.delete(sel[0])

    def get_items(self) -> list:
        return list(self._lb.get(0, "end"))


# ── PII Configuration editor ──────────────────────────────────────────────────

class PIIEditorWindow(tk.Toplevel):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.title("Edit My PII Configuration")
        self.geometry("560x680")
        self.configure(bg=WHITE)
        self.resizable(True, True)
        self.grab_set()

        cfg = _load_yaml()
        if cfg is None:
            self._open_raw_fallback()
            self.destroy()
            return

        self._cfg = cfg
        self._build()

    def _open_raw_fallback(self) -> None:
        """If pyyaml isn't available, open the file in the default app."""
        if not KNOWN_PII.exists():
            messagebox.showinfo(
                "Not found",
                "No PII config file found. Run the installer first.",
                parent=self,
            )
            return
        system = platform.system()
        if system == "Darwin":
            subprocess.run(["open", str(KNOWN_PII)])
        elif system == "Windows":
            os.startfile(str(KNOWN_PII))  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", str(KNOWN_PII)])

    def _build(self) -> None:
        cfg = self._cfg

        # scrollable inner frame
        canvas = tk.Canvas(self, bg=WHITE, highlightthickness=0)
        vsb    = tk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=WHITE)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind(
            "<Configure>",
            lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfig(win_id, width=e.width),
        )

        # mouse-wheel scrolling
        def _on_wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_wheel)

        p = PAD
        ident = cfg["identity"]
        emp   = cfg["employer"]

        # ── Personal ─────────────────────────────────────────────────────────
        tk.Label(inner, text="Personal Information", font=F_TITLE,
                 bg=WHITE, fg=FG_MAIN).pack(anchor="w", padx=p, pady=(p, 6))

        self._names  = self._section(inner, "Your name(s)", ident["names"])
        self._emails = self._section(inner, "Email address(es)", ident["emails"])
        self._phones = self._section(inner, "Phone number(s)", ident["phones"])
        self._addrs  = self._section(inner, "Home address(es)", ident["addresses"])

        ttk.Separator(inner, orient="horizontal").pack(fill="x", padx=p, pady=p)

        # ── Employer ──────────────────────────────────────────────────────────
        tk.Label(inner, text="Employer / Company", font=F_TITLE,
                 bg=WHITE, fg=FG_MAIN).pack(anchor="w", padx=p, pady=(0, 6))

        self._cnames  = self._section(inner, "Company name(s)",        emp["names"])
        self._domains = self._section(inner, "Work domain(s) (e.g. acme.com)", emp["domains"])

        ttk.Separator(inner, orient="horizontal").pack(fill="x", padx=p, pady=p)

        # ── Ignore list ───────────────────────────────────────────────────────
        tk.Label(inner, text="Never Anonymize — Exceptions",
                 font=F_TITLE, bg=WHITE, fg=FG_MAIN).pack(anchor="w", padx=p, pady=(0, 2))
        tk.Label(
            inner,
            text="Values here are never replaced, even if they match a detection rule.\n"
                 "Port numbers, internal IPs, and version strings belong here.",
            font=F_SMALL, bg=WHITE, fg=FG_MUTE, justify="left",
        ).pack(anchor="w", padx=p, pady=(0, 6))
        self._ignore = self._section(inner, "Exceptions", cfg["ignore"])

        # ── Footer ────────────────────────────────────────────────────────────
        foot = tk.Frame(inner, bg=WHITE)
        foot.pack(fill="x", padx=p, pady=p)
        _btn(foot, "Save & Restart Proxy", self._save).pack(side="left")
        _btn(foot, "Cancel", self.destroy, primary=False).pack(side="right")

    def _section(self, parent: tk.Frame, label: str, items: list) -> _EditableList:
        w = _EditableList(parent, label, items)
        w.pack(fill="x", padx=PAD, pady=(0, 12))
        return w

    def _save(self) -> None:
        cfg = self._cfg
        cfg["identity"]["names"]     = self._names.get_items()
        cfg["identity"]["emails"]    = self._emails.get_items()
        cfg["identity"]["phones"]    = self._phones.get_items()
        cfg["identity"]["addresses"] = self._addrs.get_items()
        cfg["employer"]["names"]     = self._cnames.get_items()
        cfg["employer"]["domains"]   = self._domains.get_items()
        cfg["ignore"]                = self._ignore.get_items()
        try:
            _save_yaml(cfg)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc), parent=self)
            return
        restart_proxy()
        messagebox.showinfo(
            "Saved", "Configuration saved.\nProxy restarted with new settings.",
            parent=self,
        )
        self.destroy()


# ── Log viewer ────────────────────────────────────────────────────────────────

class LogWindow(tk.Toplevel):
    _LOG_FILES = ["/tmp/pii-proxy.log", "/tmp/pii-proxy.err"]

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.title("PII Proxy — Recent Logs")
        self.geometry("700x450")
        self.configure(bg=BG)
        self._build()
        self._refresh()

    def _build(self) -> None:
        self._text = tk.Text(
            self, font=F_MONO, bg="#1e1e1e", fg="#d4d4d4",
            state="disabled", wrap="word",
        )
        vsb = tk.Scrollbar(self, command=self._text.yview)
        self._text.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._text.pack(fill="both", expand=True)
        _btn(self, "Refresh", self._refresh).pack(pady=6)

    def _refresh(self) -> None:
        parts = []
        for path in self._LOG_FILES:
            p = Path(path)
            if p.exists():
                parts.append(f"── {path} ──\n{p.read_text()}")
        text = "\n\n".join(parts) if parts else "(No log files found yet)"
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.insert("end", text)
        self._text.see("end")
        self._text.configure(state="disabled")


# ── Main manager window ───────────────────────────────────────────────────────

REPO_URL = "https://github.com/jaichandra/pii-proxy"


class AboutWindow(tk.Toplevel):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.title("About PII Proxy")
        self.geometry("360x280")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.grab_set()
        self._build()

    def _build(self) -> None:
        tk.Frame(self, bg=ACCENT, height=4).pack(fill="x")

        inner = tk.Frame(self, bg=BG)
        inner.pack(fill="both", expand=True, padx=PAD, pady=PAD)

        tk.Label(inner, text="🛡  PII Proxy", font=F_TITLE,
                 bg=BG, fg=ACCENT).pack(pady=(0, 4))

        tk.Label(
            inner,
            text=(
                "Automatically replaces personal information in\n"
                "AI requests with believable pseudonyms, then\n"
                "restores the originals in responses."
            ),
            font=F_BODY, bg=BG, fg=FG_BODY, justify="center",
        ).pack(pady=(0, PAD))

        ttk.Separator(inner, orient="horizontal").pack(fill="x", pady=(0, PAD))

        link = tk.Label(
            inner, text=REPO_URL, font=F_SMALL,
            bg=BG, fg=ACCENT, cursor="hand2",
        )
        link.pack()
        link.bind("<Button-1>", lambda _e: self._open_repo())

        tk.Label(inner, text="Click to open in browser",
                 font=F_SMALL, bg=BG, fg=FG_MUTE).pack(pady=(2, PAD))

        _btn(inner, "Close", self.destroy, primary=False).pack()

    def _open_repo(self) -> None:
        system = platform.system()
        if system == "Darwin":
            subprocess.run(["open", REPO_URL])
        elif system == "Windows":
            subprocess.run(["start", "", REPO_URL], shell=True)
        else:
            subprocess.run(["xdg-open", REPO_URL])


class ManagerApp:
    _REFRESH_MS = 2000

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("PII Proxy Manager")
        self.root.geometry("380x510")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        tk.Frame(self.root, bg=ACCENT, height=4).pack(fill="x")
        self._build()
        self._schedule_refresh()

    def _build(self) -> None:
        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True, padx=PAD, pady=PAD)

        # heading
        tk.Label(main, text="🛡  PII Proxy", font=F_TITLE,
                 bg=BG, fg=ACCENT).pack()

        # status card
        card = tk.Frame(main, bg=WHITE, relief="solid", bd=1)
        card.pack(fill="x", pady=(12, 0))

        row = tk.Frame(card, bg=WHITE)
        row.pack(padx=PAD, pady=(PAD, 6), fill="x")
        self._dot = tk.Label(row, text="●", font=("Helvetica Neue", 26),
                             bg=WHITE, fg=DANGER)
        self._dot.pack(side="left")
        self._status_lbl = tk.Label(row, text="Checking…", font=F_HEAD,
                                    bg=WHITE, fg=FG_MAIN)
        self._status_lbl.pack(side="left", padx=(8, 0))

        ctrl = tk.Frame(card, bg=WHITE)
        ctrl.pack(padx=PAD, pady=(0, PAD), fill="x")

        self._btn_start   = _btn(ctrl, "Start",   self._start)
        self._btn_stop    = _btn(ctrl, "Stop",    self._stop,    primary=False)
        self._btn_restart = _btn(ctrl, "Restart", self._restart, primary=False)
        for w in (self._btn_start, self._btn_stop, self._btn_restart):
            w.pack(side="left", padx=(0, 6))

        ttk.Separator(main, orient="horizontal").pack(fill="x", pady=12)

        # action buttons
        for text, cmd, pri in [
            ("Edit My PII Configuration", self._edit_pii, True),
            ("View Proxy Logs",           self._view_logs, False),
            ("Open Config Folder",        self._open_folder, False),
            ("About PII Proxy",           self._about,       False),
        ]:
            _btn(main, text, cmd, primary=pri).pack(fill="x", pady=3)

        ttk.Separator(main, orient="horizontal").pack(fill="x", pady=12)
        _btn(main, "Quit Manager", self.root.destroy, danger=True).pack()

    # ── status refresh ────────────────────────────────────────────────────────

    def _schedule_refresh(self) -> None:
        self._refresh_status()
        self.root.after(self._REFRESH_MS, self._schedule_refresh)

    def _refresh_status(self) -> None:
        running = is_running()
        if running:
            self._dot.configure(fg=SUCCESS)
            self._status_lbl.configure(text=f"Running  (port {PORT})")
            self._btn_start.configure(state="disabled")
            self._btn_stop.configure(state="normal")
            self._btn_restart.configure(state="normal")
        else:
            self._dot.configure(fg=DANGER)
            self._status_lbl.configure(text="Stopped")
            self._btn_start.configure(state="normal")
            self._btn_stop.configure(state="disabled")
            self._btn_restart.configure(state="disabled")

    # ── proxy controls ────────────────────────────────────────────────────────

    def _start(self) -> None:
        start_proxy()
        self.root.after(1800, self._refresh_status)

    def _stop(self) -> None:
        stop_proxy()
        self.root.after(1500, self._refresh_status)

    def _restart(self) -> None:
        self._btn_restart.configure(state="disabled")
        restart_proxy()
        self.root.after(2500, self._refresh_status)

    # ── actions ───────────────────────────────────────────────────────────────

    def _edit_pii(self) -> None:
        PIIEditorWindow(self.root)

    def _view_logs(self) -> None:
        LogWindow(self.root)

    def _open_folder(self) -> None:
        if not PII_HOME.exists():
            messagebox.showinfo(
                "Not found",
                f"Config folder does not exist yet:\n{PII_HOME}\n\n"
                "Run the installer first.",
            )
            return
        system = platform.system()
        if system == "Darwin":
            subprocess.run(["open", str(PII_HOME)])
        elif system == "Windows":
            subprocess.run(["explorer", str(PII_HOME)])
        else:
            subprocess.run(["xdg-open", str(PII_HOME)])

    def _about(self) -> None:
        AboutWindow(self.root)

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    ManagerApp().run()

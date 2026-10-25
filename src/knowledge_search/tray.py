"""The Windows app: Ferret in the system tray (`pythonw -m knowledge_search.tray`, no console window).

Runs the server in this process on port 8000, or the next free one when another program has it, and opens
the browser once it answers (not with --background, used when Windows starts it at sign-in). Menu: Open
Ferret · Pause indexing · Start when I sign in · Updates… (Settings → Updates; Install update X shows when
GitHub has a newer release, checked at start and daily) · Quit Ferret. A second start while Ferret runs only opens the
browser.
"""

import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

PORTS = range(8000, 8020)
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"  # the same value the installer's task writes


def url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def answers(port: int) -> bool:
    try:
        urllib.request.urlopen(url(port) + "/healthz", timeout=2)
        return True
    except OSError:
        return False


def free_port() -> int | None:
    for port in PORTS:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return None


def start_command() -> str:
    return f'"{Path(sys.executable).with_name("pythonw.exe")}" -m knowledge_search.tray --background'


def autostart(on: bool | None = None) -> bool:
    """Whether Windows starts Ferret at sign-in; `on` changes it."""
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
        if on is True:
            winreg.SetValueEx(k, "Ferret", 0, winreg.REG_SZ, start_command())
        elif on is False:
            try:
                winreg.DeleteValue(k, "Ferret")
            except FileNotFoundError:
                pass
        try:
            winreg.QueryValueEx(k, "Ferret")
            return True
        except FileNotFoundError:
            return False


def main() -> None:
    for name in ("stdout", "stderr"):  # pythonw has no console; output goes to logs/ via logging instead
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115 - for the process' life
    background = "--background" in sys.argv

    from .config import get_settings

    settings = get_settings()
    updating = Path(settings.data_dir) / "updating"  # written by updates.install: the browser tab reloads itself
    if updating.exists():
        background = True
        updating.unlink(missing_ok=True)
    port_file = Path(settings.data_dir) / "port"
    try:
        known = int(port_file.read_text())
    except (OSError, ValueError):
        known = None
    if known and answers(known):  # Ferret already runs
        if not background:
            webbrowser.open(url(known))
        return

    import pystray
    import uvicorn
    from PIL import Image

    from . import db, logs, runtime, updates
    from .api import create_app
    from .deps import state

    logs.install(settings.data_dir)
    port = free_port()
    if port is None:
        port = PORTS[0]  # the server reports the error; the tray says so below
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, access_log=False,
                                           log_config=None))
    thread = threading.Thread(target=server.run, name="server")
    thread.start()

    def paused() -> bool:
        if not server.started:
            return False
        with db.pool().connection() as conn:
            return runtime.load(conn).indexing_paused

    def toggle_pause():
        with db.pool().connection() as conn:
            runtime.save(conn, {"indexing_paused": not runtime.load(conn).indexing_paused}, None)
        icon.title = "Ferret (indexing paused)" if paused() else f"Ferret: {url(port)}"

    def check():
        if updates.check()["newer"]:
            icon.update_menu()
            icon.notify(f"Ferret {updates.state['latest']} is available: right-click the tray icon and choose Install update.",
                        "Ferret")

    def quit_app(icon, _item):
        icon.title = "Ferret is stopping…"
        server.should_exit = True
        icon.stop()

    state.quit_app = lambda: quit_app(icon, None)
    icon = pystray.Icon("Ferret", Image.open(Path(__file__).with_name("static") / "ferret.png"), "Ferret", pystray.Menu(
        pystray.MenuItem("Open Ferret", lambda: webbrowser.open(url(port)), default=True),
        pystray.MenuItem("Pause indexing and summaries", toggle_pause, checked=lambda _item: paused()),
        pystray.MenuItem("Start when I sign in", lambda: autostart(not autostart()), checked=lambda _item: autostart(),
                         visible=sys.platform == "win32"),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda _item: f"Install update {updates.state['latest']}",
                         lambda: updates.install(on_started=lambda: quit_app(icon, None)),
                         visible=lambda _item: updates.status()["can_install"] and updates.status()["newer"]),
        pystray.MenuItem("Updates…", lambda: webbrowser.open(url(port) + "/#updates")),
        pystray.MenuItem("Quit Ferret", quit_app)))

    def watch():
        """Open the browser once ready, then check for updates now and daily (not with KS_OFFLINE)."""
        while thread.is_alive() and not server.started:
            time.sleep(0.5)
        if not server.started:
            if not server.should_exit:
                icon.title = "Ferret could not start: no free port between 8000 and 8019"
                icon.notify("Ferret could not start: ports 8000 to 8019 are all in use.", "Ferret")
            return
        port_file.write_text(str(port))
        icon.title = "Ferret (indexing paused)" if paused() else f"Ferret: {url(port)}"
        if not background:
            webbrowser.open(url(port))
        while not settings.offline and thread.is_alive():
            check()
            time.sleep(24 * 3600)

    threading.Thread(target=watch, daemon=True).start()
    icon.run()
    thread.join(timeout=60)  # the database shuts down with the server
    port_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

"""The Windows app: Ferret in the system tray (`pythonw -m knowledge_search.tray`, no console window).

Runs the server in this process, opens the browser once it answers (not with --background, used when
Windows starts it at sign-in), and offers Open Ferret · Start when I sign in · Quit Ferret. A second start
while Ferret runs just opens the browser.
"""

import os
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

URL = "http://127.0.0.1:8000"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"  # the same value the installer's task writes


def running() -> bool:
    try:
        urllib.request.urlopen(URL + "/healthz", timeout=2)
        return True
    except OSError:
        return False


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
    if running():
        if not background:
            webbrowser.open(URL)
        return

    import pystray
    import uvicorn
    from PIL import Image

    from . import logs
    from .api import create_app
    from .config import get_settings

    logs.install(get_settings().data_dir)
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=8000, access_log=False,
                                           log_config=None))
    thread = threading.Thread(target=server.run, name="server")
    thread.start()

    def quit_app(icon, _item):
        icon.title = "Ferret is stopping…"
        server.should_exit = True
        icon.stop()

    icon = pystray.Icon("Ferret", Image.open(Path(__file__).with_name("static") / "ferret.png"), "Ferret", pystray.Menu(
        pystray.MenuItem("Open Ferret", lambda: webbrowser.open(URL), default=True),
        pystray.MenuItem("Start when I sign in", lambda: autostart(not autostart()), checked=lambda _item: autostart(),
                         visible=sys.platform == "win32"),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit Ferret", quit_app)))

    def watch():
        """Open the browser once ready; say so in the tray if the server could not start (port 8000 taken)."""
        while thread.is_alive() and not server.started:
            time.sleep(0.5)
        if server.started:
            icon.title = "Ferret: " + URL
            if not background:
                webbrowser.open(URL)
        elif not server.should_exit:
            icon.title = "Ferret could not start: port 8000 is in use by another program"
            icon.notify("Ferret could not start: port 8000 is in use by another program.", "Ferret")

    threading.Thread(target=watch, daemon=True).start()
    icon.run()
    thread.join(timeout=60)  # the database shuts down with the server


if __name__ == "__main__":
    main()

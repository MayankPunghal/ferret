"""Updates: the newest release on GitHub, and for the Windows app, installing it from Settings → Updates or the
tray (download the installer, run it silently; it stops Ferret, replaces it and starts it again)."""

import json
import logging
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from importlib.metadata import version
from pathlib import Path

log = logging.getLogger(__name__)
RELEASES = "https://api.github.com/repos/MayankPunghal/ferret/releases/latest"
state = {"latest": None, "url": None, "checked_at": None, "error": None, "installing": None, "progress": None}


def current() -> str:
    return version("ferret-search")


def windows_app() -> bool:
    """Installed by Ferret-Setup.exe (its Python sits next to stop.ps1), so it can update itself."""
    return sys.platform == "win32" and (Path(sys.prefix).parent / "stop.ps1").is_file()


def newer(tag: str, than: str) -> bool:
    def parts(v):
        return tuple(int(x) for x in v.lstrip("v").split(".")[:3] if x.isdigit())
    return parts(tag) > parts(than)


def check() -> dict:
    """Ask GitHub for the newest release (and its Windows installer)."""
    try:
        req = urllib.request.Request(RELEASES, headers={"User-Agent": "Ferret", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            rel = json.load(r)
        state.update(latest=rel["tag_name"].lstrip("v"), error=None,
                     url=next((a["browser_download_url"] for a in rel.get("assets", []) if a["name"].endswith(".exe")), None))
    except (OSError, ValueError, KeyError) as e:
        state["error"] = f"could not reach GitHub: {e}"
    state["checked_at"] = time.time()
    return status()


def status() -> dict:
    latest = state["latest"]
    return {"current": current(), "latest": latest, "newer": bool(latest and newer(latest, current())),
            "windows_app": windows_app(), "can_install": windows_app() and bool(state["url"]),
            "checked_at": state["checked_at"], "error": state["error"], "installing": state["installing"],
            "progress": state["progress"]}


def install(on_started=None) -> None:
    """Download the newer installer and run it; `on_started` (the tray's quit) runs once it has started."""
    def run():
        target = Path(tempfile.gettempdir()) / f"Ferret-Setup-{state['latest']}.exe"
        try:
            state.update(installing="downloading", progress=0)
            req = urllib.request.Request(state["url"], headers={"User-Agent": "Ferret"})
            with urllib.request.urlopen(req, timeout=600) as r, open(target, "wb") as f:
                total, done = int(r.headers.get("Content-Length") or 0), 0
                while chunk := r.read(1 << 16):
                    f.write(chunk)
                    done += len(chunk)
                    state["progress"] = done * 100 // total if total else None
        except OSError as e:
            state.update(installing=None, error=f"the update could not be downloaded: {e}")
            return
        state["installing"] = "installing"
        # the restarted app opens no new browser tab: the page that started the update reloads itself (tray.py)
        from .config import get_settings

        (Path(get_settings().data_dir) / "updating").write_text(state["latest"])
        log.warning("installing Ferret %s from %s", state["latest"], target)
        subprocess.Popen([str(target), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/UPDATE=1"])
        if on_started:
            on_started()

    threading.Thread(target=run, daemon=True, name="update").start()


if __name__ == "__main__":
    assert newer("v1.0.2", "1.0.1") and not newer("v1.0.1", "1.0.1") and newer("1.1.0", "1.0.9")
    print("ok")

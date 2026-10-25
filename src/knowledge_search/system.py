"""What this computer can run: its memory and graphics cards, checked once, on the first start, to choose
defaults (Settings shows what was found and can change everything). Thresholds are estimates from the model
sizes: Gemma 4 E2B needs about 3-4 GB of its own, and an integrated GPU shares the computer's memory."""

import ctypes
import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

SUMMARIES_MIN_GB = 8  # below: summaries off by default (the model would crowd out everything else)
GPU_MIN_GB = 15  # a "16 GB" computer reports ~15.6; below: the summary model stays on the CPU
VENDORS = {"0x8086": "Intel GPU", "0x1002": "AMD GPU", "0x10de": "NVIDIA GPU"}


def memory_gb() -> float:
    """Total memory, or the container's limit when it is lower."""
    if sys.platform == "win32":
        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (n, ctypes.c_ulonglong) for n in ("total", "avail", "page", "apage", "virt", "avirt", "aext")]

        s = Status(length=ctypes.sizeof(Status))
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
        total = s.total
    else:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        limit = Path("/sys/fs/cgroup/memory.max")
        if limit.is_file() and limit.read_text().strip().isdigit():
            total = min(total, int(limit.read_text()))
    return round(total / 2**30, 1)


def gpus() -> list[str]:
    """Graphics cards llama.cpp's Vulkan build could use (names on Windows, the vendor on Linux)."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name"],
                                 capture_output=True, text=True, timeout=20,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            return [n.strip() for n in out.splitlines()
                    if n.strip() and not any(w in n for w in ("Basic", "Remote", "Virtual", "Hyper-V", "Mirror"))]
        if sys.platform == "linux" and Path("/dev/dri").is_dir():  # no /dev/dri: no access (a container without it)
            found = {VENDORS.get(p.read_text().strip()) for p in Path("/sys/class/drm").glob("card*/device/vendor")}
            return sorted(n for n in found if n)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("could not list the graphics cards: %s", e)
    return []


def choose(memory: float, cards: list[str], gpu_build: bool) -> dict:
    """Settings that differ from the shipped defaults on this computer."""
    if memory < SUMMARIES_MIN_GB:
        return {"enrich_enabled": False}
    if cards and gpu_build and memory >= GPU_MIN_GB:  # measured on an Iris Xe: 10-20% faster, same quality
        return {"llm_device": "gpu"}
    return {}


def apply_once(conn) -> None:
    """On the first start: record the computer and save the defaults chosen for it. Settings already saved, or
    set by a KS_ environment variable, are left alone."""
    from . import enrich, runtime

    if conn.execute("SELECT 1 FROM app_settings WHERE key = 'system'").fetchone():
        return
    info = {"memory_gb": memory_gb(), "gpus": gpus()}
    saved = {r["key"] for r in conn.execute("SELECT key FROM app_settings")}
    chosen = {k: v for k, v in choose(info["memory_gb"], info["gpus"], enrich.gpu_supported()).items()
              if k not in saved and f"KS_{k.upper()}" not in os.environ}
    runtime.save(conn, {**chosen, "system": {**info, "chosen": chosen}}, None)
    log.warning("this computer: %s GB memory, graphics: %s; defaults chosen: %s",
                info["memory_gb"], ", ".join(info["gpus"]) or "none found", chosen or "the shipped ones")


if __name__ == "__main__":  # quick check: python -m knowledge_search.system
    assert choose(4, ["Intel GPU"], True) == {"enrich_enabled": False}
    assert choose(16, ["Intel GPU"], True) == {"llm_device": "gpu"} and choose(16, [], True) == {}
    assert choose(12, ["Intel GPU"], True) == {} and choose(32, ["NVIDIA GPU"], False) == {}
    print(memory_gb(), "GB", gpus())

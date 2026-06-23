#!/usr/bin/env python3
"""_launcher.py — Multi-folder aggregator for the FileWhipr context-menu entry.

Windows Explorer invokes this script once per selected folder when the user
right-clicks multiple folders simultaneously. This script collects all the
concurrent invocations and launches filewhipr.py exactly once with all
selected folder paths.

How it works:
  1. Each instance appends its path to a shared temp file.
  2. Instances wait briefly so sibling multi-select launches can write first.
  3. The first instance to create a lock file exclusively becomes the "trigger".
  4. The trigger waits briefly for sibling instances to finish writing.
  5. The trigger reads all paths, cleans up, and launches the main GUI.
  6. Later invocations while the lock exists launch their own GUI instead of
     joining an already-active collection.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

_TEMP = Path(os.environ.get("TEMP") or os.environ.get("TMP") or "C:\\Temp")
_LOCK_FILE = _TEMP / "filewhip_launcher.lock"
_COLLECT_DELAY = 0.15  # seconds to let selected-folder sibling processes start
_DEBOUNCE = 0.4   # seconds to wait for sibling invocations
_STALE_AGE = 30   # seconds before leftover files are treated as stale


def _script() -> Path:
    return Path(__file__).resolve().parent / "filewhipr.py"


def _pythonw() -> str:
    py = Path(sys.executable)
    pw = py.with_name("pythonw.exe")
    return str(pw) if pw.exists() else str(py)


def _my_path_file() -> Path:
    """Each instance writes to its own file to avoid concurrent-write collisions."""
    return _TEMP / f"filewhip_path_{os.getpid()}.txt"


def _purge_stale() -> None:
    now = time.time()
    for f in list(_TEMP.glob("filewhip_path_*.txt")) + [_LOCK_FILE]:
        try:
            if f.exists() and (now - f.stat().st_mtime) > _STALE_AGE:
                f.unlink(missing_ok=True)
        except OSError:
            pass


def main() -> None:
    if len(sys.argv) < 2:
        return

    incoming = str(Path(sys.argv[1]).resolve())

    _purge_stale()

    if _LOCK_FILE.exists():
        subprocess.Popen([_pythonw(), str(_script()), incoming])
        return

    # Step 1: Each instance writes to its own file — no concurrent-write collision.
    my_file = _my_path_file()
    my_file.write_text(incoming + "\n", encoding="utf-8")

    # Let sibling Explorer invocations for the same multi-select write their paths.
    time.sleep(_COLLECT_DELAY)

    # Step 2: Race to become the trigger instance.
    try:
        fd = os.open(str(_LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        return  # Another instance won; let it handle the launch.

    # Step 3: We are the trigger — wait for siblings to finish writing.
    time.sleep(_DEBOUNCE)

    # Step 4: Collect all per-PID path files.
    paths: list[str] = []
    for f in _TEMP.glob("filewhip_path_*.txt"):
        try:
            text = f.read_text(encoding="utf-8").strip()
            if text:
                paths.append(text)
        except OSError:
            pass

    if not paths:
        paths = [incoming]

    # Step 5: Clean up before launch so a crash doesn't leave stale files.
    for f in _TEMP.glob("filewhip_path_*.txt"):
        f.unlink(missing_ok=True)
    _LOCK_FILE.unlink(missing_ok=True)

    # Step 6: Launch the main GUI with all collected folder paths.
    subprocess.Popen([_pythonw(), str(_script()), *paths])


if __name__ == "__main__":
    main()

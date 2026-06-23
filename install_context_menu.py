#!/usr/bin/env python3
"""install_context_menu.py — Install / uninstall the FileWhipr Windows Explorer context-menu entry.

Run with no arguments (or --install) to register the right-click entry.
Run with --uninstall to remove it.

Entries are written to HKCU so no administrator rights are required.
Two shell extension points are registered:
  - Directory/shell                : right-click on a folder icon
  - Directory/Background/shell    : right-click inside an open folder
"""

import sys
import winreg
from pathlib import Path

MENU_LABEL = "FileWhipr"
_SHELL_KEYS = [
    r"Software\Classes\Directory\shell\FileWhipr",
    r"Software\Classes\Directory\Background\shell\FileWhipr",
]
_LEGACY_SHELL_KEYS = [
    r"Software\Classes\Directory\shell\FileWhip",
    r"Software\Classes\Directory\Background\shell\FileWhip",
]


def _script_path() -> Path:
    return Path(__file__).resolve().parent / "_launcher.py"


def _pythonw() -> str:
    """Prefer pythonw.exe so no console window flashes on launch."""
    py = Path(sys.executable)
    pw = py.with_name("pythonw.exe")
    return str(pw) if pw.exists() else str(py)


def _delete_key_tree(hive: int, path: str) -> None:
    """Delete a registry key and its direct subkeys (one level deep)."""
    try:
        with winreg.OpenKey(hive, path) as key:
            # Collect subkey names first (can't delete while iterating)
            subkeys = []
            idx = 0
            while True:
                try:
                    subkeys.append(winreg.EnumKey(key, idx))
                    idx += 1
                except OSError:
                    break
        for sub in subkeys:
            try:
                winreg.DeleteKeyEx(hive, path + "\\" + sub)
            except FileNotFoundError:
                pass
        winreg.DeleteKeyEx(hive, path)
    except FileNotFoundError:
        pass


def _ico_path() -> str:
    return str(Path(__file__).resolve().parent / "FileWhipr.ico")


def install() -> None:
    script = _script_path()
    if not script.exists():
        print(f"ERROR: launcher script not found at {script}")
        sys.exit(1)

    pythonw = _pythonw()
    # %V  = selected folder path (Directory\shell and Background\shell)
    command = f'"{pythonw}" "{script}" "%V"'
    ico = _ico_path()

    for key_path in _SHELL_KEYS:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, MENU_LABEL)
            # MUIVerb lets Windows localise the label if desired; harmless here.
            winreg.SetValueEx(key, "MUIVerb", 0, winreg.REG_SZ, MENU_LABEL)
            # Icon value tells Explorer which icon to show in the context menu.
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, ico)
            # Microsoft documents MultiSelectModel for verbs that support multiple items.
            winreg.SetValueEx(key, "MultiSelectModel", 0, winreg.REG_SZ, "Player")

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, key_path + r"\command"
        ) as cmd_key:
            winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, command)

    for key_path in _LEGACY_SHELL_KEYS:
        _delete_key_tree(winreg.HKEY_CURRENT_USER, key_path)

    print(f'Installed: "{MENU_LABEL}" in Windows Explorer right-click menu.')
    print(f"  Script : {script}")
    print(f"  Runtime: {pythonw}")
    print()
    print("Right-click any folder (or inside an open folder) to use it.")
    print('Run with --uninstall to remove the entry.')


def uninstall() -> None:
    for key_path in _SHELL_KEYS + _LEGACY_SHELL_KEYS:
        _delete_key_tree(winreg.HKEY_CURRENT_USER, key_path)

    print('Removed "FileWhipr…" from the Windows Explorer context menu.')


def main() -> None:
    if "--uninstall" in sys.argv:
        uninstall()
    else:
        install()


if __name__ == "__main__":
    main()

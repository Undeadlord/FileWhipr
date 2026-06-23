# FileWhipr

A small Windows utility that copies or moves files by extension out of a folder
(and, optionally, all its subfolders) into a destination you choose — driven
entirely from the Explorer right-click menu.

Right-click a folder, pick **FileWhipr**, choose an extension (`.pdf`, `.jpg`,
`.stl`, whatever), pick **Copy** or **Move** and a destination, and it whips
every matching file into shape for you. Select several folders at once and it treats them
as a single combined source.

Built with Python and [PySide6](https://www.qt.io/qt-for-python).

---

## Features

- **Explorer context-menu integration** — right-click a folder icon *or*
  right-click inside an open folder.
- **Multi-folder aware** — select several folders, right-click once, and they
  are aggregated into one operation.
- **Copy or Move**, with optional **recursive** descent into subfolders.
- **Live scan + transfer progress** off the UI thread, fully cancellable
  mid-operation.
- **Destination safety checks** — recursive operations will not run when the
  destination is the source folder or inside a source folder.
- **Automatic de-collision** — if a file of the same name already exists at the
  destination, the copy is renamed `name (1).ext`, `name (2).ext`, … rather than
  overwritten.
- **Configurable extension list** with a Custom entry for anything not in it.
- **Light / Dark / System theme.**
- **No admin rights required** — context-menu entries are written to `HKCU`.

---

## Requirements

- **Windows** (uses the Windows registry for the context-menu entry).
- **Python 3.10+** (the code uses `X | None` type-hint syntax).
- **PySide6** — see [`requirements.txt`](requirements.txt).

---

## Installation

1. Clone or download this repository to a folder you intend to keep — the
   context-menu entry points at the script in place, so don't delete it
   afterward:

   ```powershell
   git clone https://github.com/Undeadlord/FileWhipr.git
   cd FileWhipr
   ```

2. Install the dependency:

   ```powershell
   pip install -r requirements.txt
   ```

3. Register the right-click menu entry:

   ```powershell
   python install_context_menu.py
   ```

   You should see a confirmation that the entry was installed. Right-click any
   folder (or inside an open folder) and you'll find **FileWhipr** in the menu.

   If you are updating an existing checkout, rerun this command after pulling
   the latest files so the Explorer context-menu registration is refreshed.

### Removing it

```powershell
python install_context_menu.py --uninstall
```

This removes the registry entries. You can then delete the folder.

---

## Usage

- **Right-click a folder** → **FileWhipr**, or **right-click inside an open
  folder** → **FileWhipr**.
- Choose the **extension** to act on (or type a custom one).
- Pick **Copy** or **Move**, toggle **Include subfolders** as needed.
- Choose a **destination** and hit **Scan and Copy / Move**.

When **Include subfolders** is enabled, FileWhipr blocks destinations that are
the same as a source folder or nested inside one of the source folders. This
prevents recursive copy/move operations from pulling their own output back into
the scan.

You can also run it directly without the context menu — it will prompt you to
pick a folder:

```powershell
python filewhipr.py
```

---

## Settings

Click the **⚙** button in the window to configure:

- The list of extensions shown in the dropdown (add / remove / reorder).
- Default action (Copy or Move) and default recursive behavior.
- Whether the window closes on every outcome or only on success.
- Theme (System / Light / Dark).

Settings are stored in `filewhipr_settings.json` next to the script.

---

## Files

| File | Purpose |
|------|---------|
| `filewhipr.py` | The main GUI application. |
| `_launcher.py` | Aggregates multiple simultaneous folder selections into one launch. |
| `install_context_menu.py` | Installs / uninstalls the Explorer context-menu entry. |
| `filewhipr_settings.json` | User settings (created/updated at runtime). |

---

## License

FileWhipr is released under the [MIT License](LICENSE).

It uses **PySide6**, which is licensed under the LGPL v3. PySide6 is imported
(dynamically linked) and unmodified; see [`NOTICE`](NOTICE) for details.

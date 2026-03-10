# Development Setup

## Prerequisites

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) package manager
- LibreOffice (for testing the extension)

## Setup

Install development dependencies:

```shell
uv sync
```

This creates a `.venv/` virtual environment and installs dev dependencies
(`types-unopy`, `types-uno-script`) for type checking.

## Type Checking

The project uses [Pyright](https://github.com/microsoft/pyright) for static
type analysis. Configuration is in `pyrightconfig.json`.

```shell
pyright
```

UNO type stubs are provided by `types-unopy`. Some UNO attributes are not
covered by those stubs — missing ones are added in `typings/com/sun/star/`.
Lines where Pyright cannot resolve a known-good attribute at runtime use
`# type: ignore[attr-defined]`.

## Building the Extension

```shell
VIPEROFFICE_VERSION="0.1.0" make extension
```

Output `.oxt` file is placed in `dist/`. Build artifacts go to `build/`.

```shell
make clean   # remove build/ and dist/
```

## Installing for Testing

Open the built `.oxt` file with LibreOffice — it will prompt to install.
Restart LibreOffice after installation.

Enable via **Tools → Add-Ons → ViperOffice - enable**

To apply code changes without reinstalling, symlink the source file into
LibreOffice's user macro directory:

```shell
ln -s "$(pwd)/src/viperoffice.py" ~/.config/libreoffice/4/user/Scripts/python/viperoffice.py
```

Changes take effect after restarting LibreOffice.

## Assigning Shortcuts

You can assign keyboard shortcuts to enable, disable, or toggle the extension.
The shortcuts affect all current and new editor windows.

1. Open **Tools → Customize** and go to the **Keyboard** tab.
2. In **Category**, navigate to **Application Macros → My Macros → ViperOffice → ViperOffice**.
3. The **Function** list shows: `enable_viper_office`, `disable_viper_office`, `toggle_viper_office`.
4. Select a function, pick a key combination in **Shortcut Keys**, and click **Assign**.
5. Click **Save** then **OK**.

## Project Structure

```
src/viperoffice.py        Main extension source (single file)
extension/template/       Extension package template used by make
typings/com/sun/star/     Local UNO type stubs (supplement types-unopy)
pyrightconfig.json        Pyright configuration
pyproject.toml            Project metadata and dev dependencies
Makefile                  Build targets
```

## Editor Setup

Any editor with Pyright/LSP support will pick up types automatically once
`uv sync` has been run, since `pyrightconfig.json` points `extraPaths` at
the `.venv` site-packages.

For Neovim with `pyright` LSP, no extra configuration is needed — open any
file under `src/` and type checking is active.

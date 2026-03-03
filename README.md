# ViperOffice

ViperOffice is an extension for LibreOffice and OpenOffice that brings subset of Vi/(Neo)Vim
Vi/(Neo)Vim text editors text editing and navigation capabilities to word processing.

This is a new project under development and in it's early stages. Feature set is
incomplete and breakage can happen since there is lots of moving parts. Code isn't very refined yet.

Some of the of code is partially based on LibreOffice extensions
- [jmagers/vibreoffice](https://github.com/jmagers/vibreoffice) and [fedorov-ao/vibreoffice](https://github.com/fedorov-ao/vibreoffice)
which are written in LibreOffice Basic. They are reference and inspiration for
the project. However, this isn't a direct port.


## Installation/Usage

The easiest way to install is to download the
[latest extension file](https://raw.github.com/linuxmage/vibreoffice/master/dist/vibreoffice-1.1.4.oxt)
and open it with LibreOffice/OpenOffice. LibreOffice/OpenOffice will need to be restarted before this extension can be used.

To enable/disable ViperOffice, select Tools -> Add-Ons -> ViperOffice and enable, disable or toggle. [How to make shortcuts for these.](#shortcuts-for-enabling-and-disabling)
Enabled/disabled state affects all current and new windows. Windows gets updated when
they gains focus.

You can build the .oxt file yourself by running
```shell
# replace 0.0.0 with your desired version number
VIPEROFFICE_VERSION="0.0.0" make extension
```
This will simply build the extension file from the template files in
`extension/template`. These template files were auto-generated using
[Extension Compiler](https://wiki.openoffice.org/wiki/Extensions_Packager#Download).

## Features

- For info about commands you can refer to [Neovim docs](https://neovim.io/doc/user/) since
they are similar in functionality.

- Modes: Normal (+Operator pending), Insert, Visual.
- Statusline with mode, count, pending command.

Currently supported commands:
- Insert: `i`, `I`, `a`, `A`, `o`, `O`, Visual `v`.
- Motions: `hjkl`, `$`, `0`, `^`, `gg`, `G`, `w`, `W`, `b`, `B`, `e`, `E`, `ge`, `gE`, `()`, `H`, `L`
- Number count for most commands. Example `10j`, `3gg`, `d4gE`
- Scrolling: `C-f`, `C-b`
- Replace: `r`
- Deletion: `x`, `X`, `d`, `dd`, `D`, `c`, `cc`, `C`, `s`, `S`
- Undo/redo: `u`, `C-r` / `U`
- Copy/paste: `y`, `yy`, `Y`, `p`, `P` 
- Search: `/` (LibreOffice search bar)

- Aliases `Ins` = i, `BS` = h 

Supported Insert mode commands:
- Switch to Normal mode: `Esc`, aliases: `C-[`, `C-c`

Other shortcuts and keys are passed to LibreOffice.

## Shortcuts for enabling and disabling

You can set your own shortcuts to enable, disable or toggle enabling of this extension. Affects all current and new editor windows.

Open Tools -> Customize. In `Categories` under Application Macros -> My Macros -> ViperOffice -> ViperOffice. List has functions:
enable_viper_office, disable_viper_office, toggle_viper_office

Select function, in `Shortcut keys` select key for it and click `Assign`. Click `Save` to save settings and `OK` to finish.


## Known differences to Vi/(Neo)vim

- Line based commands work on *visual lines*, not lines separated by end-of-line -characters.
- Movement keys will wrap to the next line.
- Start and end of text which for example `G` and `gg` use, operate on *text container* like C-Home/End in LibreOffice. It's not necessarily start or end of a document. Container can be for example a text frame. Repeating command targets next text container.
- For yank/paste system clipboard is used instead of registers. `x`, `X`, `s` and `S` don't yank
  to clipboard but just delete.
- `H` and `L` motions move cursor to start and end of page instead of screen.
- Operators don't support `j` or `k`.
- `Y` does same as `y$`.

## Known issues / missing features

For implemented features:

- Insert/append commands currently don't take count.


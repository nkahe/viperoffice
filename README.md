# ViperOffice

ViperOffice is an extension for LibreOffice and OpenOffice that brings subset of Vi/(Neo)Vim
Vi/(Neo)Vim text editors text editing and navigation capabilities to word processing.

This is a new project under development and in it's early stages. There isn't enough
supported features yet to be really useful and breakage can happen. Code hasn't been
refined.

Some of the of code is partially based on LibreOffice extensions
- [jmagers/vibreoffice](https://github.com/jmagers/vibreoffice) and [fedorov-ao/vibreoffice](https://github.com/fedorov-ao/vibreoffice)
which are written in LibreOffice Basic. They are reference and inspiration for
the project. However, this isn't a direct port and many areas like word based
motions are completely different.

Goal: VibreOffice was great but goal is to have behavior more glose to Vim.

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

- Modes: Normal (+Operator pending), Insert.
- Optional count for most commands.
- Statusline with mode, count, pending command.

Currently supported Normal mode commands:
- Insert: `i`, `I`, `a`, `A`, `o`, `O`.
- Motions: `hjkl`, `$`, `0`, `^`, `gg`, `G`, `w`, `W`, `b`, `B`, `e`, `E`, `()`, `H`
- Scrolling: `C-f`, `C-b`
- Replace: `r`
- Deletion: `x`, `X`, `d`, `dd`, `D`, `c`, `cc`, `C`, `s`
- Undo/redo: `u`, `C-r`
- Copy/paste: `y`, `yy`, `p`, `P` 

- Aliases: `/` = default search bar, `Ins` = i, `U` = C-r, `BS` = h 

Supported Insert mode commands:
- Switch to Normal mode: `Esc`, aliases: `C-[`, `C-c`

Other shortcuts and keys are passed to LibreOffice so they work as expected.

## Shortcuts for enabling and disabling

You can set your own shortcuts to enable, disable or toggle enabling of this extension. Affects all current and new editor windows.

Open Tools -> Customize. In `Categories` under Application Macros -> My Macros -> ViperOffice -> ViperOffice. List has functions:
enable_viper_office, disable_viper_office, toggle_viper_office

Select function, in `Shortcut keys` select key for it and click `Assign`. Click `Save` to save settings and `OK` to finish.


## Known differences to Vi/(Neo)vim

- Line based commands work on *visual lines*, not lines separated by end-of-line -characters.
- Movement keys will wrap to the next line.
- Start and end of text which for example `G` and `gg` use, is LibreOffice's definition which is for *text container*. It's not necessarily start or end of a document. Container can be for example a text frame. Repeating command targets next text container.
- For yank/paste system clipboard is used instead of registers.
- Operators don't support `j` or `k`.

## Known issues / missing features

For implemented features:

- Insert/append commands currently don't take count.


# ViperOffice

ViperOffice is an extension for LibreOffice and OpenOffice that brings subset of Vi/(Neo)Vim
Vi/(Neo)Vim text editors text editing and navigation capabilities to word processing.

This is a new project under development and in it's early stages. Feature set is
incomplete and breakage can happen since there is lots of moving parts.

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

For how to use different commands, please read [user documentation](user_documentation.md).

## Features

- Multi-window support.
- Modes: Normal, Operator-pending, Insert, Visual.
- Vi(m) -like word, WORD, sentence and paragraph motions.
- Word, sentence and paragraph text-objects.
- Most command can take number count and use it like Vi(m).
- Passthrough of not-used shortcuts so most of LibreOffice's shortcuts are still available.
- Statusline with mode, count, pending commands.

Currently supported commands:
- Insert: `i`, `I`, `a`, `A`, `o`, `O`, Visual `v`.
- Motions: `hjkl`, `$`, `0`, `^`, `gg`, `G`, `w`, `W`, `b`, `B`, `e`, `E`, `ge`, `gE`, `H`, `L`, `()`, `{}`, `<CR>`, `+`, `-`, `_`
- To character motions: `f`, `F`, `t`, `T`, `;`, `,`
- Number count for most commands. Example `10j`, `3gg`, `d4gE`
- Scrolling: `C-f`, `C-b`, `C-d`, `C-u`
- Replace: `r`
- Deletion: `x`, `X`, `d`, `dd`, `D`, `c`, `cc`, `C`, `s`, `S`
- Registers: `"_`
- Undo/redo: `u`, `C-r` / `U`
- Copy/paste: `y`, `yy`, `Y`, `p`, `P`
- Text-objects:  `iw`, `iW`, `aw`, `aW`, `is`, `as`, `ip`, `ap`
- Search: `/` (LibreOffice search bar)

- In Visual mode: swap cursor position to other end of selection: `o`.

Aliases:
- `Ins` = i, `BS` = h, `C-c` copy in Visual mode
- Navigation keys are mapped to equivalent motions.

Mouse:
- Mouse selection in Normal mode switch to Visual mode.

Insert mode commands:
- Switch to Normal mode: `Esc` or with alias `C-[`. 

Commands are described in more detail in the [user documentation](user_documentation.md).


## Shortcuts for enabling and disabling

You can set your own shortcuts to enable, disable or toggle enabling of this extension. Affects all current and new editor windows.

Open Tools -> Customize. In `Categories` under Application Macros -> My Macros -> ViperOffice -> ViperOffice. List has functions:
enable_viper_office, disable_viper_office, toggle_viper_office

Select function, in `Shortcut keys` select key for it and click `Assign`. You can click `Save` to save settings and `OK` to finish.


## Known differences to Vi/(Neo)vim

- Line based commands work on *visual lines*, not lines separated by end-of-line -characters.
    - Movement keys will wrap to the next line.
    - Due to line wrapping, you may find your cursor move up/down a line for
      commands that would otherwise leave you in the same position (such as `dd`)
- For yank/paste system clipboard is used instead of registers. `x`, `X`, `s` and `S` don't yank but just delete.
- Paragraph motions `{}` stop at start of paragraphs in addition to possible
  first empty line between them.
- `H` and `L` motions move cursor to start and end of document page instead of screen.
- `Y` does same as `y$` similarly to Neovim.
- `C-d` and `C-u` default scrolling of 20 lines instead of half page since there's no reliable way to get position of half page. Number of scrolled lines can changed with extension's global variable `SCROLL`.
- Sentence motions use LibreOffice's definition of a sentence. Selected language can have an effect for that. For example first alphabet of sentence may need to be in upper case for it to be considered as sentence.


## Known issues / missing features

For implemented features:

- Insert/append commands currently don't take count.
- Commands not listed under features are not currently implemented.

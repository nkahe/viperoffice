# ViperOffice

ViperOffice is an extension for LibreOffice and OpenOffice that brings subset of Vi/Vim 
key bindings for navigation and editing text. It is written in Python. It currently supports only Writer.

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

Currently supported commands:
- Insert/append (`i`, `a`) and return to Normal with `Esc`
- Movement keys: `hjkl`, `0`, `$`
- Deletion `x`

Shortcuts and movements keys not used by ViperOffice are passed to LibreOffice. 

## Shortcuts for enabling and disabling

You can set your own shortcuts to enable, disable or toggle enabling of this extension. Affects all current and new editor windows.

Open Tools -> Customize. In `Categories` under Application Macros -> My Macros -> ViperOffice -> ViperOffice. List has functions:
enable_viper_office, disable_viper_office, toggle_viper_office

Select function, in `Shortcut keys` select key for it and click `Assign`. Click `Save` to save settings and `OK` to finish.


## Known differences

## Known issues

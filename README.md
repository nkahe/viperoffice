# vibreoffice

vibreoffice is an extension for Libreoffice and OpenOffice that brings some of
your favorite key bindings from vi/vim to your favorite office suite. It is
obviously not meant to be feature-complete, but hopefully will be useful to
both vi/vim neophytes and experts alike.

## Installation/Usage

The easiest way to install is to download the
[latest extension file](https://raw.github.com/linuxmage/vibreoffice/master/dist/vibreoffice-1.1.4.oxt)
and open it with LibreOffice/OpenOffice. LibreOffice/OpenOffice will need to be restarted before this extension can be used.

To enable/disable vibreoffice, simply select Tools -> Add-Ons -> vibreoffice and enable, disable or toggle. [How to make shortcuts for these.](#shortcuts-for-enabling-and-disabling)

If you really want to, you can build the .oxt file yourself by running
```shell
# replace 0.0.0 with your desired version number
VIBREOFFICE_VERSION="0.0.0" make extension
```
This will simply build the extension file from the template files in
`extension/template`. These template files were auto-generated using
[Extension Compiler](https://wiki.openoffice.org/wiki/Extensions_Packager#Download).


## Features

vibreoffice currently supports:
- Insert/append (`i`, `a`) and return to Normal with `Esc`
- Movement keys: `hjkl`
- Deletion `x`

## Shortcuts for enabling and disabling

You can set your own shortcuts to enable, disable or toggle enabling of this extension. Affects all current and new editor windows.

Open Tools -> Customize. In `Categories` under Application Macros -> My Macros -> vibreoffice -> vibreoffice. List has functions:
startVibreoffice, stopVibreoffice, toggleVibreoffice

Select function, in `Shortcut keys` select key for it and click `Assign`. Click `Save` to save settings and `OK` to finish.


## Known differences/issues



## License
vibreoffice is released under the MIT License.

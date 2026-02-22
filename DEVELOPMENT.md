# Development

Extension consists of different sections in this order:

Global state

Editor - Functions to manipulate view and model (document).

Input handling - Keyhandler class and helper functions to interpret user input and to for example call correct `editor` section functions or pass input to LibreOffice.

Infra - Initialization, attach and detach keyhandlers for all editor windows depending if extension is enabled or disabled.

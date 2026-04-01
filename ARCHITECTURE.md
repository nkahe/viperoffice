# ViperOffice architecture

Extension source code consists of following modules:

- **core** - Common functionality imported by other modules. Global state, utility-
  and UI related functions.
- **viperoffice** - Enabling and disabling extension, exposes API for it, listens events, handles controllers and attachs KeyHandler to them.
- **editor** - Handles input and calls editor actions, which manipulate view and
  text document.

Modules have different sections which are described below.

## Core module

### Global state

Global state of extension and helper functions to manage it.

### UI and input modes

Update statusline and cursor appearance and general Vi input mode changing which affects those. Used mainly made by KeyHandler.

### Utility funtions

Other general helper functions.


## Editor module

File consists of different sections listed below. Order is same as in source code.
Main class of extension is KeyHandler. 


### Import modules

Functions for importing other modules.


### Cursor and selection

Get information or make changes to cursor which includes selection and caret position.
Used by actions.


### Actions

Actions are functions which are mapped to different keys and can make changes to view
and document. They mainly get variable state as function parameters from KeyHandler
or other actions and return boolean about success of the action.

#### Navigating in document

Jump cursor to different places in document or scroll view. Commands `gg`, `G`, `H`, `L`, `C-f`, `C-b`, `C-d`, `C-u`, `/`, `f`, `F`, `t`, `T`, `,`, `;`.

#### Lines

Moving in line and line based motions. Commands `hjkl`, `$`, `0`, `^`, `S`, `X`, `<CR>`, `+`, `-`, `_`

#### Character editing

Insert, delete and replace characters. Commands `i`, `I`, `a`, `A`, `o`, `O`, `x`, `X`, `s`, `r`. 

#### Operators and clipboard

Vi operators delete, change and yank, clipboard operations, undo/redo. Commands `d`, `dd`, `D`, `c`, `cc`, `C`, `y`, `yy`, `Y`, `p`, `P`, `u`, `C-r`.

#### Word motions

Commands `w`, `W`, `b`, `B`, `e`, `E`, `ge`, `gE`,  `iw`, `iW`, `aw`, `aW`

#### Sentence motions

Commands `()`, `is`, `as`

#### Paragraph motions

Commands `{}`, `ip`, `ap`


### Input handling

Contains KeyHandler class which is backbone of extension. It interprets and processes user
input, manages (global) state and calls actions based on them.



# Architecture

## Main extension source code

Main code consist of different sections. Main class of extension is KeyHandler. Order
is same as they are in code.

### Global state

Global state of extension and all helper functions to manage it. It's used mainly by
KeyHandler. 

### Utility funtions

Other general helper functions.

### UI and input modes

Update statusline and cursor appearance and general Vi input mode changing which affects those. Used mainly made by KeyHandler.

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

Moving in line and line based motions. Commands `hjkl`, `$`, `0`, `^`, `S`, `X`.

#### Character editing

Insert, delete and replace characters. Commands `i`, `I`, `a`, `A`, `o`, `O`, `x`, `X`, `s`, `r`. 

#### Operators and clipboard

Vi operators delete, change and yank, clipboard operations, undo/redo. Commands `d`, `dd`, `D`, `c`, `cc`, `C`, `y`, `yy`, `Y`, `p`, `P`, `u`, `C-r`.

#### Word motions

Commands `w`, `W`, `b`, `B`, `e`, `E`, `ge`, `gE`.

#### Sentence motions

Commands `()`, `is`, `as`

#### Paragraph motions

Commands `{}`, `ip`, `ap`.

### Input handling

Contains KeyHandler class which is backbone of extension. It interprets and processes user
input, manages (global) state and calls actions based on them.

### Infra

Non-editing functionality: initialization, enable and disable extension, listen events,
handle controllers and attach KeyHandler.

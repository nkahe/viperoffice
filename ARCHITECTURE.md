# Architecture

## Main extension source code

Main code consist of following sections:

### Global state

Global state of extension and all helper functions to manage it.

### Utility funtions

Other general helper functions.

### UI and input modes

Updating statusline and cursor appearance and general Vi input mode changing which affects those.

### Cursor and selection

Get information or make changes to cursor which includes selection and caret position.

### Navigating in document

Jump cursor to different places in document or scroll view. Commands `gg`, `G`, `H`, `L`, `C-f`, `C-b`, `C-d`, `C-u`, `/`.

### Lines

Moving in line and line based motions. Commands `hjkl`, `$`, `0`, `^`, `S`, `X`.

### Character editing

Insert, delete and replace characters. Commands `i`, `I`, `a`, `A`, `o`, `O`, `x`, `X`, `s`, `r`. 

### Operators and clipboard

Vi operators delete, change and yank, clipboard operations, undo/redo. `d`, `dd`, `D`, `c`, `cc`, `C`, `y`, `yy`, `Y`, `p`, `P`, `u`, `C-r`.

### Word motions

Commands `w`, `W`, `b`, `B`, `e`, `E`, `ge`, `gE`.

### Sentence motions

Commands `()`, `is`, `as`

### Paragraph motions

Commands `{}`, `ip`, `ap`.

### Input handling

Interpreting user key input and routing to correct functions based on it and global state.

### Infra

Non-editing functionality: initialization, enabling and disabling extension, listening events,
handling controllers and attaching keyhandlers.

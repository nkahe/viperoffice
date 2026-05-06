# ViperOffice architecture

Extension source code consists of following modules:

- **core** - ViperOffice helper functions which manipulate global state or UNO context access and are used by different modules.
- **utils** - Utility functions which don't depend on global state and are commonly shared with other modules.
- **viperoffice** - Enabling and disabling extension, exposes API for it, listens events, handles controllers and attachs editor's KeyHandler to them.
- **editor** - Handles input and calls actions, which manipulate view and text document.
Motions used by editor:
- **sentences** - Sentence motions. Commands `()`, `is`, `as`.
- **paragraphs** - Paragraph motions. Commands `{}`, `ip`, `ap`.
- **words** - Word motions including text-objects. Commands `w`, `W`, `b`, `B`, `e`, `E`, `ge`, `gE`,  `iw`, `iW`, `aw`, `aW`
- **word_specs** - Words specs used by editor and words.py.
- **words-decentralized** - Alternative implementation of Words motions. Uses more
  independent functions. Easier to follow but bigger. Doesn't include text-objects.

Modules have different sections which are described below.

## Editor module

File consists of different sections listed below. Order is same as in source code.
Main class of extension is KeyHandler which is in same module with different actions
for convenience.

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


### Input handling

Contains KeyHandler class which is backbone of extension. It interprets and processes user
input, manages (global) state and calls actions based on them.


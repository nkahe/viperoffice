# User documentation

Content of this documentation is based on Vim documentation (Vim License), modified.
See LICENSES/VIM-LICENSE.

## Modes

ViperOffice (like Vi/Vim) operates in different modes. The current mode is shown in the statusline.

- **Normal mode** - Enter editor commands. This is the default mode when starting ViperOffice (also called command mode).
- **Visual mode** - Like Normal mode but movement commands extend a highlighted selection. Non-movement commands operate on the selection.
- **Insert mode** - Typed text is inserted into the document.
- **Operator-pending mode** - After an operator is started, ViperOffice waits for a motion to specify the text the operator will act on.

## Default LibreOffice commands

- Most shortcuts which aren't listed here, are forwarded to LibreOffice.

## Motions and operators

Motion commands can follow an operator to make the operator act on the text moved over (the text between the cursor before and after the motion). Operators are commonly used to delete or change text. The operators:

- `c` — delete and yank (copy) to clipboard and change to Insert mode.
- `d` — delete and yank (copy) to clipboard.
- `y` — yank (copy) into clipboard.


## Inserting text

The following commands can be used to insert new text into the document
and start Insert mode.

- `a` — Append text after the cursor.  If the cursor is in the first column of
  an empty line Insert starts there.
- `A` — Append text at end of line, before line ending space or end of paragraph -character.
- `i` or `<Insert>` — Insert text before the cursor.
- `I` — Insert text before the first non-blank in the line.
- `o` — Begin a new paragraph below the cursor and insert text.
- `O` — Begin a new paragraph above the cursor and insert text.


## Insert mode commands

- `<Esc>`, `CTRL-[` or  `CTRL-C` — End insert mode, go back to Normal mode.

## Starting and stopping Visual mode

- `v` — In Normal mode switch to Visual mode and vice versa.
- `<Mouse selection>` — Works like in default LibreOffice but enters to Visual mode.
- `<Left click>` — In Visual mode moves cursor and switches to Normal mode.

### Left-right motions

These commands move the cursor horizontally in the current line. Most are exclusive motions (they do not include the target character) except where noted.

- `h`, `<Left>` or `<BS>` — [count] characters left. Exclusive motion.
- `l` or `<Right>` — [count] characters right. Exclusive motion.
- `0` or `<Home>` — To the first character of the line. Exclusive motion.
- `^` - To the first non-blank character of the line. Exclusive motion. Any count is ignored.
- `$`, `<End>` — To the end of the line (inclusive). With a count N also go `N-1` lines down if possible. In Visual mode `$` places the cursor just after the last character.
- `f{char}` — Till before [count]th occurrence of `{char}` to the right; cursor lands on `{char}` (inclusive).
- `F{char}` — Till before [count]th occurrence of `{char}` to the left; cursor lands on `{char}` (inclusive).
- `t{char}` — Till before the [count]th occurrence of `{char}` to the right; cursor lands on the character just left of `{char}` (inclusive).
- `T{char}` — Till after the [count]th occurrence of `{char}` to the left; cursor lands on the character just right of `{char}` (exclusive).
- `;` — Repeat the latest `f`, `t`, `F` or `T` [count] times.
- `,` — Repeat the latest `f`, `t`, `F` or `T` in the opposite direction [count] times.

### Search

- `/` — Show default LibreOffice search bar. 

### Up-down motions

- `k` or `<Up>`   — [count] display lines upward (exclusive).
- `j` or `<Down>` — [count] display lines downward (exclusive).
- `G`  — Goto line [count], default last line, to the first non-blank character (linewise).
- `gg` — Goto line [count], default first line, to the first non-blank character (linewise).
- `H`  — Goto start of page.
- `L`  — Goto end of page.
- `-`, `<minus>` — [count] lines upward, on the first non-blank character |linewise|.
- `<CR>` or `+`  — [count] lines downward, to the first non-blank character (linewise).
- `_`, `<underscore>` — Move [count] - 1 lines downward, on the first non-blank character.
- `CTRL-U` — 20 (by default) display lines up [count] times.
- `CTRL-D` — 20 (by default) display lines down [count] times.

### Word motions

- `w` — [count] words forward (exclusive).
- `W` — [count] WORDS forward (exclusive).
- `e` — forward to the end of word [count] (inclusive). Does not stop in an empty line.
- `E` — forward to the end of WORD [count] (inclusive). Does not stop in an empty line.
- `b` — [count] words backward (exclusive).
- `B` — [count] WORDS backward (exclusive).
- `ge` — Backward to the end of word [count] (inclusive).
- `gE` — Backward to the end of WORD [count] (inclusive).

A word consists of a sequence of alphabets and characters defined by ISKEY variable.
By default this includes digits and underscores. An empty line is also considered
to be a word. A WORD consists of a sequence of non-blank characters, separated with white
space. An empty line is also considered to be a WORD.

### Text-object motions

 Sentence and paragraph motions use LibreOffice's definition of these. For sentences there can be differences depending on language. Usually definition is consecutive words where first word starts with upper case character and ends with `.`, `!` or `?` character. Paragraphs are separated by end of paragraph -characters.

- `(` — [count] sentences backward (exclusive).
- `)` — [count] sentences forward (exclusive).
- `{` — [count] paragraphs backward (exclusive). Stops at start of paragraph or first blank line.
- `}` — [count] paragraphs forward (exclusive). Stops at start of paragraph or first blank line.

### Text-object selection

Text-object commands work in Visual mode or after an operator. Commands starting with `a` select an object including surrounding whitespace; commands starting with `i` select the "inner" object without surrounding whitespace. Inner commands select less text than the corresponding `a` commands.

- `aw` — "a word": select [count] words (leading/trailing whitespace included but not counted).
- `aW —`"a WORD", select [count] WORDs. Leading or trailing white space is included,
  but not counted.
- `iw` — "inner word": select [count] words (whitespace is treated as word and
  is counted). 
- `iW` — "inner WORD", select [count] WORDs. White space between words is counted too.
- `as` — "a sentence": select [count] sentences. In Visual mode it's charwise.
- `ap` — "a paragraph": select [count] paragraphs. End of paragraph character is a paragraph boundary. In Visual mode it's linewise.
- `ip` — "inner paragraph": select [count] paragraphs. End of paragraph character is a paragraph boundary. In Visual mode it's linewise.

(When using these commands, combine them with operators, e.g. `daw` to delete a word including surrounding whitespace.)


## Scroll

- `CTRL-B` or `<PageUp>` — Scroll window [count] pages Backwards (upwards) in the document.
- `CTRL-F` or `<PageDown>` — Scroll window [count] pages Forwards (downwards) in the document.


## Delete and copy

- `"_` — Black hole register. Next delete command that would yank text to clipboard,
  just deletes it instead leaving clipboard untouched.
- `x` or `<Del>` — Delete [count] characters under and after the cursor
  (not linewise). The `<Del>` key does not take a [count].  Instead,
  it deletes the last character of the count.
- `X` — Delete [count] characters before the cursor.
- `d{motion}` — Delete text that {motion} moves to clipboard.
- `dd` — Delete [count] lines into clipboard linewise.
- `D`  — Delete the characters under the cursor until the end of the line and
         [count]-1 more lines to clipboard. synonym for "d$". (not linewise)

In Visual mode:

- `{Visual}x` or `<Del>` — Delete the highlighted text 
- `{Visual}d` — Delete highligted text to clipboard.
- `{Visual}X` — Delete the highlighted lines. 
- `{Visual}D` — Delete the highlighted lines to clipboard.


## Delete and insert

- `c{motion}` — Delete the text that `{motion}` moves into clipboard, then start Insert mode.
- `cc` — Delete [count] lines into clipboard and start Insert mode.
- `C` — Delete from the cursor position to the end of the the line and [count]-1 more
        lines into clipboard, then start Insert mode. Synonym for c$ (not linewise).
- `s` — Delete [count] characters into clipboard and start Insert mode (substitute).
        Synonym for "cl" (not linewise).
- `S` — Delete [count] lines into clipboard and start Insert mode. (Synonym for `cc`, linewise).
        Synonym for "cc" linewise.

In Visual mode:

- `{Visual}c`, `s`  — Delete the highlighted text into clipboard and start Insert mode.
- `{Visual}r{char}` — Replace all selected characters by {char}.
- `{Visual}C` — Delete the highlighted lines into clipboard and start Insert mode.
- `{Visual}S` — Delete the highlighted lines and start Insert mode.


## Replace characters

- `r{char}` — Replace the character under the cursor with `{char}`. If `{char}` is
 `<CR>` or `<TAB>`, it's replaced by end of paragraph or tab character.


## Copying and pasting text

- `y{motion}` — Yank `{motion}` text into clipboard (does not change the text).
- `yy` — Yank [count] lines into clipboard (linewise).
- `Y` — Yank end of line into clipboard. Same as `y$`. In Visual mode yank highlighted lines.
- `p` — Put the text from clipboard after the cursor [count] times.
- `P` — Put the text from clipboard before the cursor [count] times.

Note: default `Ctrl-C`, `Ctrl-X` and `Ctrl-V` also works.


## Undo and redo commands

- `u` — Undo [count] changes.
- `C-r`, `U` — Redo [count] changes.

Note: default `Ctrl-Z` and `Ctrl-Y` also work.

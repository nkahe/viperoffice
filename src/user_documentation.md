# User documentation

## Modes

ViperOffice (like Vi/Vim) operates in different modes. The current mode is shown in the statusline.

- **Normal mode**
  - Enter editor commands. This is the default mode when starting ViperOffice (also called command mode).

- **Visual mode**
  - Like Normal mode but movement commands extend a highlighted selection. Non-movement commands operate on the selection.

- **Insert mode**
  - Typed text is inserted into the buffer.

- **Operator-pending mode**
  - After an operator is started, ViperOffice waits for a motion to specify the text the operator will act on.

## Motions and operators

Motion commands can follow an operator to make the operator act on the text moved over (the text between the cursor before and after the motion). Operators are commonly used to delete or change text. The main operators:

- `c` — change
- `d` — delete and yank (copy) to clipboard
- `y` — yank into clipboard (does not change the text)

### Left-right motions

These commands move the cursor horizontally in the current line. Most are exclusive motions (they do not include the target character) except where noted.

- `h`, `<Left>`, `<BS>`
  - Move [count] characters left. Exclusive motion.

- `l`, `<Right>`
  - Move [count] characters right. Exclusive motion.

- `0` or `<Home>`
  - Move to the first character of the line. Exclusive motion.
  - `<Home>` behaves like `1|` (stays in same TEXT column when moving up/down), which differs from `0` when the line starts with a tab.

- `^`
  - Move to the first non-blank character of the line. Exclusive motion. Any count is ignored.

- `$`, `<End>`
  - Move to the end of the line (inclusive). With a count N also go `N-1` lines down if possible.
  - In Visual mode `$` places the cursor just after the last character.

- `f{char}`
  - Move to the [count]th occurrence of `{char}` to the right; cursor lands on `{char}` (inclusive).
  - `{char}` can be entered as a digraph. With Unicode encoding, composing characters may be used.
  - `:lmap` mappings apply to `{char}`; in Insert mode `CTRL-^` toggles this.

- `F{char}`
  - Move to the [count]th occurrence of `{char}` to the left; cursor lands on `{char}` (inclusive).

- `t{char}`
  - Move until before the [count]th occurrence of `{char}` to the right; cursor lands on the character just left of `{char}` (inclusive).

- `T{char}`
  - Move until after the [count]th occurrence of `{char}` to the left; cursor lands on the character just right of `{char}` (exclusive).

- `;` — Repeat the latest `f`, `t`, `F` or `T` [count] times.
- `,` — Repeat the latest `f`, `t`, `F` or `T` in the opposite direction [count] times.

### Up-down motions

- `k`, `<Up>`  — move [count] display lines upward (exclusive).
- `j`, `<Down>` — move [count] display lines downward (exclusive).
- `<CR>` - Move [count] lines downward, to the first non-blank character (linewise).
- `G` - Go to line [count], default last line, to the first non-blank character (linewise).
- `gg` - Go to line [count], default first line, to the first non-blank character (linewise).
- `H` - Go to start of page.
- `L` - Go to end of page.
- `CTRL-U` - Move cursor 20 lines up [count] times.
- `CTRL-D` - Move cursor 20 lines down [count] times.

### Word motions

- `w` — Move forward [count] words (exclusive).
- `W` — Move forward [count] WORDS (exclusive).
- `e` — Move forward to the end of word [count] (inclusive). Does not stop in an empty line.
- `E` — Move forward to the end of WORD [count] (inclusive). Does not stop in an empty line.
- `b` — Move backward [count] words (exclusive).
- `B` — Move backward [count] WORDS (exclusive).
- `ge` — Move backward to the end of word [count] (inclusive).
- `gE` — Move backward to the end of WORD [count] (inclusive).

(`word` vs `WORD`: `word` treats punctuation as separators; `WORD` treats any whitespace as separator.)

### Text-object motions

- `(` — Move backward [count] sentences (exclusive).
- `)` — Move forward [count] sentences (exclusive).
- `{` — Move backward [count] paragraphs (exclusive).
- `}` — Move forward [count] paragraphs (exclusive).

### Text-object selection

Text-object commands work in Visual mode or after an operator. Commands starting with `a` select an object including surrounding whitespace; commands starting with `i` select the "inner" object without surrounding whitespace. Inner commands select less text than the corresponding `a` commands.

- `aw` — "a word": select [count] words (leading/trailing whitespace included but not counted). In Visual linewise mode `aw` becomes charwise.
- `iw` — "inner word": select [count] words (whitespace between words is counted). In Visual linewise mode `iw` becomes charwise.
- `as` — "a sentence": select [count] sentences. In Visual mode it's charwise.
- `ap` — "a paragraph": select [count] paragraphs. A blank line (only whitespace) is a paragraph boundary. In Visual mode it's linewise.
- `ip` — "inner paragraph": select [count] paragraphs. A blank line is a paragraph boundary. In Visual mode it's linewise.

(When using these commands, combine them with operators, e.g. `daw` to delete a word including surrounding whitespace.)


## Scroll

`CTRL-B` or `PageUp` - Scroll window [count] pages Backwards (upwards) in the document.
`CTRL-F` or `PageDown` - Scroll window [count] pages Forwards (downwards) in the document.


## Insert mode

<Esc> or CTRL-[ - End insert mode, go back to Normal mode.
CTRL-C - Quit insert mode, go back to Normal mode.

## Change

"x]x	or Del		Delete [count] characters under and after the cursor
			(not linewise).
The <Del> key does not take a [count].  Instead, it
			deletes the last character of the count.

X			Delete [count] characters before the cursor.

d{motion}		Delete text that {motion} moves to clipboard.
dd			Delete [count] lines into clipboard linewise.
D			Delete the characters under the cursor until the end
			of the line and [count]-1 more lines to clipboard. synonym for "d$".
			(not linewise)
{Visual}x or Del - Delete the highlighted text 
{Visual}d - Delete highligted text to clipboard.
{Visual}X - Delete the highlighted lines. 
{Visual}D		Delete the highlighted lines to clipboard

## Delete and insert

`c{motion}` — Delete the text that `{motion}` moves into `clipboard.i`, then start Insert mode.

`cc` — Delete [count] lines into `clipboard.i` and start Insert mode.

C			Delete from the cursor position to the end of the
 the line and [count]-1 more lines into `clipboard.i`, then start Insert mode.
		start insert.  Synonym for c$ (not linewise).

`s` — Delete [count] characters into `clipboard.i` and start Insert mode (substitute).
		insert (s stands for Substitute).  Synonym for "cl" (not linewise).

`S` — Delete [count] lines into `clipboard.i` and start Insert mode. (Synonym for `cc`, linewise).
Synonym for "cc" linewise.

{Visual}["clipboard.i"]c
{Visual}["clipboard.i"]s — Delete the highlighted text into `clipboard.i` and start Insert mode.
			start insert (for {Visual} see Visual-mode).

{Visual}r{char}		Replace all selected characters by {char}.

{Visual}["clipboard.i"]C — Delete the highlighted lines into `clipboard.i` and start Insert mode.
			start insert. 

{Visual}["clipboard.i"]S — Delete the highlighted lines into `clipboard.i` and start Insert mode.
			start insert.


## Simple changes

r{char}			Replace the character under the cursor with {char}.
			If {char} is a <CR> or <NL>, a line break replaces the
			character. 

## Copying and moving text

`y{motion}` — Yank `{motion}` text into `clipboard.i` (does not change the text).

`yy` — Yank [count] lines into `clipboard.i` (linewise).

`Y` — Yank [count] lines into `clipboard.i` (synonym for `yy`, linewise).

		Mapped to "y$" by default. default-mappings

`p` — Put the text from `clipboard.i` after the cursor [count] times.

`P` — Put the text from `clipboard.i` before the cursor [count] times.

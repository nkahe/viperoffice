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
- `d` — delete
- `y` — yank into register (does not change the text)

### Left-right motions

These commands move the cursor horizontally in the current line. Most are exclusive motions (they do not include the target character) except where noted.

- `h`, `<Left>`, `CTRL-H`, `<BS>`
  - Move [count] characters left. Exclusive motion.
  - Tip: to map `<BS>` literally, use `:map CTRL-V<BS> X` (press `CTRL-V` then `<BS>`).

- `l`, `<Right>`, `<Space>`
  - Move [count] characters right. Exclusive motion.
  - See the `whichwrap` option to adjust end-of-line behavior.

- `0` or `<Home>`
  - Move to the first character of the line. Exclusive motion.
  - `<Home>` behaves like `1|` (stays in same TEXT column when moving up/down), which differs from `0` when the line starts with a tab.

- `^`
  - Move to the first non-blank character of the line. Exclusive motion. Any count is ignored.

- `$`, `<End>`
  - Move to the end of the line (inclusive). With a count N also go `N-1` lines down if possible.
  - In Visual mode `$` places the cursor just after the last character.
  - With `virtualedit` enabled, `$` may move the cursor back from past EOL to the last character.

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

- `k`, `<Up>` — move up [count] logical lines.
- `j`, `<Down>` — move down [count] logical lines.

- `gk`, `g<Up>` — move [count] display lines upward (exclusive). Differs from `k` when lines wrap and with operators because it's not linewise.
- `gj`, `g<Down>` — move [count] display lines downward (exclusive). Differs from `j` when lines wrap and with operators because it's not linewise.

- `<CR>`
  - Move [count] lines downward, to the first non-blank character (linewise).

- `G`
  - Go to line [count], default last line, to the first non-blank character (linewise).

- `gg`
  - Go to line [count], default first line, to the first non-blank character (linewise).

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

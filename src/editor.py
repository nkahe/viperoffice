from __future__ import annotations
from typing import TYPE_CHECKING, Callable
from functools import partial
import threading
import unohelper
from com.sun.star.awt import KeyModifier, XKeyHandler, Key

from core import (  # noqa: f401
    DEBUG,
    KeyEvent,
    Mode,
    _current_doc,
    _execute_dispatch,
    _get_controller,
    _get_cursor,
    _get_last_ft,
    _get_mode,
    _get_pending_keys,
    _get_position,
    _get_raw_count,
    _get_scroll,
    _get_text_cursor,
    _get_visual_caret_range,
    _goto_mode,
    _handle_exc,
    _reset_count,
    _set_last_ft,
    _set_position,
    _set_visual_anchor,
    _show_cursor,
    _state,
    _update_statusline,
)
from utils import ( # type: ignore[reportMissingImports]
    _clone_text_range,
    _is_current_paragraph_empty,
    _is_cursor_at_whitespace,
    _is_forward_selection,
    _pos_xy,
    _set_visual_selection,
    _sync_view_cursor_to_text_cursor,
    msg
)
from sentences import (
    _start_of_sentences_forward,
    _to_start_of_sentences_backwards,
    _select_sentence_text_objects,
    _expand_with_sentences_objects,
    _is_at_sentence_start
)
from paragraphs import (  # type: ignore[reportMissingImports]
    _paragraphs_forward,
    _paragraphs_backward,
    _select_paragraph_text_objects,
    _expand_with_paragraph_objects,
)
from word_specs import (  # type: ignore[reportMissingImports]
    _WORD_MOTION_B,
    _WORD_MOTION_BIG_B,
    _WORD_MOTION_BIG_E,
    _WORD_MOTION_BIG_W,
    _WORD_MOTION_E,
    _WORD_MOTION_G_BIG_E,
    _WORD_MOTION_GE,
    _WORD_MOTION_W,
)
from words import ( # type: ignore[reportMissingImports]
    _current_paragraph_text_and_offset,
    _expand_with_word_text_objects,
    _select_word_objects_forward,
    _word_char_class,
    _word_motion,
    _to_start_of_words,
    _to_start_of_WORDS,
    _to_start_of_previous_WORD,
    _to_end_of_words
)

# --------------------
# Cursor and selection
# --------------------

# Based on Commit f33d46f from fedorov-ao/vibreoffice
def _go_to_other_end(mode: Mode, cursor) -> bool:
    """Move cursor to the other end of highlighted text. Command 'o' / 'O' in visual mode.

    The current cursor position becomes the start of the highlighted text and
    the cursor is moved to the other end of the highlighted text. The highlighted
    area remains the same.
    """
    if not mode.startswith("visual"):
        return False
    s = cursor.getString()
    if not s:
        return False

    # Probe which end the caret is on by trying to extend right.
    # - Selection grows  → caret was at the RIGHT (end).
    # - Selection shrinks → caret was at the LEFT (start).
    cursor.goRight(1, True)
    caret_was_at_end = len(cursor.getString()) > len(s)

    if caret_was_at_end:
        # Undo probe to restore original selection, then rebuild right→left.
        cursor.goLeft(1, True)
        cursor.collapseToEnd()
        cursor.goLeft(len(s), True)
        for _ in range(10):   # small bounded correction for paragraph marks
            if cursor.getString() == s:
                break
            cursor.goLeft(1, True)
        new_tc = _get_text_cursor()
        if new_tc is not None:
            _set_visual_anchor(new_tc.getEnd())
    else:
        # Collapse to start, step back 1 to include the first character
        # then rebuild left→right.
        cursor.collapseToStart()
        cursor.goLeft(1, False)
        cursor.goRight(len(s), True)
        for _ in range(10):
            if cursor.getString() == s:
                break
            cursor.goRight(1, True)
        new_tc = _get_text_cursor()
        if new_tc is not None:
            _set_visual_anchor(new_tc.getStart())

    return True


def _debug_cursor_state(pop_up: bool = False):  # noqa: F811  # pyright: ignore[reportUnusedFunction]
    """Print debug info about view cursor and text cursor ranges to console."""
    cursor = _get_cursor()
    if cursor is None:
        print("ViperOffice cursor debug: No view cursor available.")
        return
    try:
        text_cursor = _get_text_cursor()
        lines = [f"Mode: {_get_mode()}  pending: {_get_pending_keys()}"]

        try:
            pos = cursor.getPosition()
            x = pos.X() if callable(pos.X) else pos.X
            y = pos.Y() if callable(pos.Y) else pos.Y
            lines.append("-- view cursor --")
            lines.append(f"position: X={x}, Y={y}")
        except Exception as e:
            _handle_exc(err=e)
            lines.append("Position: unavailable")
        try:
            lines.append(f"Collapsed: {cursor.isCollapsed()}")
            lines.append(f"At start of line: {cursor.isAtStartOfLine()}")
        except Exception as e:
            _handle_exc(err=e)
            lines.append("Range: unavailable")

        # Text cursor info
        if text_cursor is None:
            lines.append("TextCursor: unavailable")
        else:
            try:
                paragraph_text, offset = _current_paragraph_text_and_offset(text_cursor)
                char = text_cursor.getString()[:40]
                lines.append("-- text cursor LO API --")
                lines.append(f"char: {char}")
                lines.append(f"getString: {text_cursor.getString()}")
                lines.append(f"collapsed: {text_cursor.isCollapsed()}")
                lines.append(f"start of paragraph: {text_cursor.isStartOfParagraph()}")
                lines.append(f"end of paragraph: {text_cursor.isEndOfParagraph()}")
                # Cursor needs to be collapsed for this to give True.
                lines.append(f"start of sentence: {text_cursor.isStartOfSentence()}")  # type: ignore[reportAttributeAccessIssue]
                lines.append(f"start of word: {text_cursor.isStartOfWord()}")
                lines.append(f"end of word: {text_cursor.isEndOfWord()}")
                lines.append("-- ViperOffice custom functions --")
                lines.append(f"is forward selection: {_is_forward_selection(text_cursor)}")
                lines.append(f"Is at whitespace: {_is_cursor_at_whitespace(text_cursor)}")
                lines.append(f"at whitespace after sentence: {_is_cursor_at_whitespace(text_cursor, "after_sentence")}")
                lines.append(f"at whitespace before paragraph: {_is_cursor_at_whitespace(text_cursor, "before_paragraph")}")
                lines.append(f"Is at empty paragraph: {_is_current_paragraph_empty(text_cursor)}")
                lines.append(f"Paragraph length: {len(paragraph_text)}")
                lines.append(f"Paragraph offset: {offset}")
                lines.append(f"start of sentence: {_is_at_sentence_start(text_cursor)}")
                lines.append(f"Word character class: {_word_char_class(char)}")
                lines.append("")
            except Exception as e:
                _handle_exc(err=e)
                lines.append(f"TextCursor info error: {e}")
        if pop_up:
            msg("\n".join(lines), "ViperOffice cursor debug")
        else:
            print("ViperOffice cursor debug:\n" + "\n".join(lines))
    except Exception as e:
        _handle_exc(e)


# ----------------------
# Navigating in document
# ----------------------

def _scroll_window(expand:bool, count:int, forward:bool, mode:Mode, lines:int|None=None) -> bool:
    """Scroll window. Commands 'C-f' or <PageDown>, 'C-b' or <PageUp>, 'C-u', 'C-d'.
    """
    try:
        cursor = _get_cursor()
        if cursor is None:
            return False
        if lines:
            if forward:
                for _ in range(count):
                    _lines_down(lines, expand, mode, cursor)
            else:
                for _ in range(count):
                    _lines_up(lines, expand, mode, cursor)
        else:
            anchor = _state().get("visual_anchor") if expand else None
            if forward:
                for _ in range(count):
                    cursor.screenDown()
            else:
                for _ in range(count):
                    cursor.screenUp()
            if anchor is not None:
                _set_visual_selection(cursor, anchor, cursor.getStart())
        return True
    except Exception as e:
        _handle_exc(err=e)
        return False


def _to_line(expand: bool, raw_count: int, default_end: bool, mode, cursor) -> bool:
    """Go to line [count] motion. Commands 'G' and 'gg'. Linewise in
       Operation-pending mode. Args:

    expand: bool       Expand selection
    raw_count: int     Move to line [count].
    default_end: bool  To default to end of text document if no count given.
                       else default of start of text document.
    """
    try:
        if raw_count == 0 and default_end:  # Command 'G'
            target = cursor.getText().getEnd()
        else:
            target = cursor.getText().getStart()  # Command 'gg'

        anchor = _state().get("visual_anchor") if expand else None
        if anchor is not None:
            _set_visual_selection(cursor, anchor, target)
        else:
            cursor.gotoRange(target, expand)

        if raw_count > 1:
            cursor.goDown(raw_count - 1, expand)  # [count]G/gg

        if mode == "pending":
            _select_linewise(cursor)
        return True
    except Exception as e:
        _handle_exc(err=e)
        return False


def _jump_to_page(expand: bool, count, cursor, target: str = "start") -> bool:
    """Motion to start or end of a page. Commands 'H' and 'L'."""
    target = target.lower()
    try:
        if target not in ("start", "end", "next", "previous"):
            return False

        anchor = cursor.getStart() if expand else None

        match target:
            case "start":
                cursor.jumpToStartOfPage()
            case "end":
                cursor.jumpToEndOfPage()
            case "next":
                for _ in range(count):
                    cursor.jumpToNextPage()
            case "previous":
                for _ in range(count):
                    cursor.jumpToPreviousPage()

        if expand and anchor:
            new_pos = cursor.getStart()
            cursor.gotoRange(anchor, False)
            cursor.gotoRange(new_pos, True)

        return True
    except Exception as e:
        _handle_exc(err=e)
        return False


def _focus_findbar() -> bool:
    """Show default LibreOffice find bar. Command '/'. """
    try:
        _execute_dispatch("vnd.sun.star.findbar:FocusToFindbar")
        return True
    except Exception as e:
        _handle_exc(err=e)
        # dispatcher.executeDispatch(frame, ".uno:SearchDialog", "", 0, ())
        return False


def _repeat_search(count, backward: bool = False) -> bool:
    """Repeat last LibreOffice search count times. Commands 'n' and 'N'."""
    try:
        # FindbarFindNext / FindbarFindPrev repeat the last findbar search.
        cmd = "vnd.sun.star.findbar:FindPrev" if backward else "vnd.sun.star.findbar:FindNext"
        for _ in range(max(1, count)):
            _execute_dispatch(cmd)
        return True
    except Exception as e:
        _handle_exc(err=e)
        return False


def _to_character(expand:bool, count:int, cursor, command: str, char: str) -> bool:
    """Motions to move to [count]'th next / previous occurances of a character [char].
       Commands 'f', 'F', 't', 'T'.
    """
    doc = _current_doc()
    if doc is None:
        return False
    try:
        if command not in ("f", "F", "t", "T"):
            return False
        if not isinstance(char, str) or len(char) != 1:
            return False

        backward = command in ("F", "T")
        visual_forward_extra = expand and command in ("f", "t") and not backward
        steps = max(1, int(count))
        moved_any = False

        search_desc = doc.createSearchDescriptor()
        search_desc.setSearchString(char)
        search_desc.SearchCaseSensitive = True
        search_desc.SearchBackwards = backward

        def _range_same_start(range_a, range_b) -> bool:
            if range_a is None or range_b is None:
                return False
            try:
                text = range_a.getText()
                return text.compareRegionStarts(range_a, range_b) == 0
            except Exception as e:
                _handle_exc(err=e)
                return False

        def _offset_range(text, base_range, delta: int):
            if base_range is None:
                return None
            try:
                probe = text.createTextCursorByRange(base_range)
                if delta < 0 and not probe.goLeft(-delta, False):
                    return None
                if delta > 0 and not probe.goRight(delta, False):
                    return None
                return probe.getStart()
            except Exception as e:
                _handle_exc(err=e)
                return None

        for _ in range(steps):
            text_cursor = _get_text_cursor()
            if text_cursor is None:
                return moved_any

            text = text_cursor.getText()
            if text is None:
                return moved_any

            if expand:
                caret = _get_visual_caret_range(text_cursor)
                text_cursor.gotoRange(caret, False)
            else:
                text_cursor.gotoRange(text_cursor.getStart(), False)

            start_cursor = text.createTextCursorByRange(text_cursor.getStart())
            if backward:
                if not start_cursor.goLeft(1, False):
                    break
            else:
                start_offset = 2 if command == "t" else 1
                if not start_cursor.goRight(start_offset, False):
                    break

            start_range = start_cursor.getStart()
            found_range = doc.findNext(start_range, search_desc)
            if found_range is None:
                break

            target_range = found_range.getStart()
            if command == "t":
                target_range = _offset_range(text, target_range, -1)
            elif command == "T":
                target_range = _offset_range(text, target_range, 1)
            if target_range is not None and visual_forward_extra:
                extra = _offset_range(text, target_range, 1)
                if extra is not None:
                    target_range = extra

            if target_range is None:
                break

            if not _range_same_start(text_cursor.getStart(), target_range):
                moved_any = True

            probe = text.createTextCursorByRange(target_range)
            _sync_view_cursor_to_text_cursor(probe, expand, cursor, backward=backward)

        return moved_any
    except Exception as e:
        _handle_exc(err=e)
        return False


def _repeat_last_to_character(count: int, expand: bool, key: KeyEvent, cursor) -> bool:
    """ Repeat last to-character motion (commands f, F, t, T). Commands ';' and ','.
        key.char ';' : use same direction
        key.char ',' : use opposite direction
    """
    try:
        last_ft = _get_last_ft()

        if not last_ft or not last_ft["type"] or not last_ft["type"]:
            return False

        ft_type = last_ft["type"]
        ft_char = last_ft["char"]

        if not isinstance(ft_type, str) or \
            not isinstance(ft_char, str) or \
            len(ft_char) != 1:
            return False

        match key.char:
            case ",":
                search_type = ft_type.swapcase()
            case ";":
                search_type = ft_type
            case _:
                return False

        text_cursor = _get_text_cursor()
        if text_cursor is None:
            return False

        match search_type:
            case "t":
                text_cursor.goRight(1, expand)
            case "T", "F":
                text_cursor.goLeft(1, expand)
            case _:
                pass

        if len(search_type) != 1:
            return False

        return _to_character(expand, count, cursor, search_type, ft_char)

    except Exception as e:
        _handle_exc(err=e)
        return False


# ------------------
# Lines
# ------------------

def _lines_up(count:int, expand:bool, mode: Mode, cursor) -> bool:
    """Motion for [count] lines up. Command 'k' or <Up>. """
    try:
        if mode == "pending":
            _to_end_of_line(False, 1, cursor)
            # At a soft-wrap point the inter-word space sits at the start of
            # the next visual line. Step past it so the selection includes it
            # and doesn't get left behind as a leading space after deletion.
            tc = _get_text_cursor()
            if tc is not None and not tc.isEndOfParagraph():
                cursor.goRight(1, False)
                cursor.gotoStartOfLine(True)
                count += 1
        return bool(cursor.goUp(count, expand))
    except Exception as e:
        _handle_exc(err=e)
        return False


def _lines_down(count:int, expand:bool, mode: Mode, cursor) -> bool:
    """Motion for [count] lines down. Command 'j' or <Down>. """
    try:
        if mode == "pending":
            cursor.gotoStartOfLine(False)
            count += 1
        return bool(cursor.goDown(count, expand))
    except Exception as e:
        _handle_exc(err=e)
        return False


def _to_first_non_blank(expand, count, cursor, up: bool = False) -> bool:
    """Motion to first non-blank character in current line, [count] lines down
       or up if 'up' is True. Commands '^', '-', '+', <CR>.
    """
    try:
        if count > 0:
            if up:
                cursor.goUp(count, expand)
            else:
                cursor.goDown(count, expand)

        # This variable represents the original line the cursor was on before
        # any of the following changes.
        anchor = _state().get("visual_anchor") if expand else None
        start_pos = cursor.getStart() if anchor is None else None
        caret_pos = None
        if anchor is not None:
            caret_pos = cursor.getEnd() if _is_forward_selection(cursor) else cursor.getStart()

        old_line = cursor.getPosition().Y

        # Select all of the current line and put it into a string.
        cursor.gotoEndOfLine(False)
        if cursor.getPosition().Y > old_line:
            # If gotoEndOfLine moved cursor to next line then move it back.
            cursor.goLeft(1, False)
        cursor.gotoStartOfLine(True)
        line_text = cursor.getString()

        # Undo any changes made to the view cursor, then move to start of line.
        # This way any previous selection made by the user will remain.
        if anchor is not None and caret_pos is not None:
            _set_visual_selection(cursor, anchor, caret_pos)
        elif start_pos is not None:
            cursor.gotoRange(start_pos, False)
        if anchor is not None:
            cursor.gotoStartOfLine(False)
            _set_visual_selection(cursor, anchor, cursor.getStart())
        else:
            cursor.gotoStartOfLine(expand)

        # Get x position of first non-blank character of line.
        i = 0
        while i < len(line_text):
            ch = line_text[i]
            if ch != " " and ch != "\t":
                break
            i += 1

        # Move the cursor to the first non-blank character.
        if i > 0:
            if anchor is not None:
                cursor.goRight(i, False)
                _set_visual_selection(cursor, anchor, cursor.getStart())
            else:
                cursor.goRight(i, expand)

        return True
    except Exception as e:
        _handle_exc(e)
        return False


def _to_end_of_line(expand:bool, count:int, cursor) -> bool:
    """Motion to end of line and optionally [count] -1 lines down.
    Command '$' or <End>.
    """
    try:
        if count > 1:
            cursor.goDown(count - 1, expand)
        old_pos = cursor.getPosition()

        cursor.gotoEndOfLine(expand)
        new_pos = cursor.getPosition()

        _, old_y = _pos_xy(old_pos)
        _, new_y = _pos_xy(new_pos)

        if not expand:
            # LibreOffice can place cursor visually at next line start; move left
            # back to previous line end unless this was an empty-line no-op.
            if cursor.isAtStartOfLine() and old_y != new_y:
                cursor.goLeft(1, expand)

        return True
    except Exception as e:
        _handle_exc(err=e)
        return False


def _select_linewise(cursor) -> bool:
    """Expand selection to cover full lines. Command 'S' and in visual mode
       commands 'C', 'D', 'X', 'Y'."""
    text_cursor = _get_text_cursor()
    if text_cursor is None:
        return False
    try:
        # X in visual mode: expand selection to cover full visual lines.
        # gotoStartOfLine/gotoEndOfLine are view cursor methods, so use
        # the view cursor to navigate to each end of the selection first.
        sel_start = text_cursor.getStart()
        sel_end   = text_cursor.getEnd()
        cursor.gotoRange(sel_start, False)
        cursor.gotoStartOfLine(False)
        cursor.gotoRange(sel_end, True)
        cursor.gotoEndOfLine(True)
        return True
    except Exception as e:
        _handle_exc(err=e)
        return False


# ------------------
# Character editing
# ------------------

# Insert, delete, replace characters

def _append_text(cursor):
    """Command 'a'."""
    textCursor = _get_text_cursor()
    try:
        if textCursor is not None and not textCursor.isEndOfParagraph():
            cursor.goRight(1, False)
    except Exception as e:
        _handle_exc(err=e)
        pass


def _append_text_to_end_of_line(mode, cursor):
    """Command 'A'."""
    try:
        if mode.startswith("visual"):
            cursor.gotoRange(cursor.getEnd(), False)
        _to_end_of_line(False, 1, cursor)
        return
    except Exception as e:
        _handle_exc(err=e)
        pass


def _insert_before_first_non_blank(mode, cursor):
    """Command 'I'."""
    try:
        if mode.startswith("visual"):
            # Move to the line where the selection starts before going to line start.
            cursor.gotoRange(cursor.getStart(), False)
        return _to_first_non_blank(False, 0, cursor)
    except Exception as e:
        _handle_exc(err=e)
        pass


def _begin_new_paragraph(above: bool, cursor):
    """Begin to write new paragraph above or below current line.
       Normal mode commands 'o', 'O'."""
    try:
        if above:
            cursor.gotoStartOfLine(False)
        else:
            _to_end_of_line(False, 0, cursor)
            cursor.goRight(1, False)

        cursor.setString(chr(13))  # CR
        if not cursor.isAtStartOfLine():
            cursor.goLeft(1, False)
            cursor.setString(chr(13) + chr(13))
            cursor.goRight(1, False)
        return True

    except Exception as e:
        _handle_exc(err=e)
        return False


def _copy_and_delete_linewise(yank: bool, delete: bool) -> bool:
    """Copy and/or delete whole lines where selection is.
       Command 'S' and 'C', 'D', 'X', 'Y' in Visual mode.
    """
    cursor = _get_cursor()
    if cursor is None:
        return False
    _select_linewise(cursor)
    return _copy_and_delete(yank, delete)


def _delete_characters(count:int, backward: bool = False) -> bool:
    """Delete single characters. Normal mode commands 'x','X' and 's'."""
    try:
        text_cursor = _get_text_cursor()
        if text_cursor is None:
            return False
        # Collapse to start of normal-mode block cursor position
        text_cursor.gotoRange(text_cursor.getStart(), False)
        if backward:
            # Delete count chars to the left; bail if already at start of line
            if not text_cursor.goLeft(count, True):
                return False
        else:
            # Delete count chars from cursor position rightward
            if not text_cursor.goRight(count, True):
                return False
        text_cursor.setString("")
        return True
    except Exception as e:
        _handle_exc(err=e)
        return False


def _replace_characters(count:int, key:KeyEvent, cursor) -> bool:
    """Replace character(s) under cursor with {key_char}.
       With count replace [count] characters with [count] {key_char}.
       Command 'r'.
    """
    try:
        length = len(cursor.getString())

        if length > 1:
            cursor.setString(key.char * length)
        else:
            cursor.setString(key.char * count)

        return True
    except Exception as e:
        _handle_exc(err=e)
        return False


# -----------------------
# Operators and clipboard
# -----------------------

def _yank(key, mode:Mode, cursor) -> bool:
    """Yanks text {motion} moves over. Commands `y`, `yy`.
       Flashes yanked range.
    """
    _copy_and_delete(True, False)
    position = _get_position()
    if (position is not None) and \
        (not mode.startswith("visual") or key.char == "Y"):
        # Keep yanked range visually selected briefly, then restore cursor.
        # Use _set_mode instead of _goto_mode so the selection isn't
        # collapsed immediately by _show_cursor("normal")().
        def _flash_restore():
            if key.char != "h":
                cursor.gotoRange(position, False)
            _show_cursor("normal")
        threading.Timer(0.1, _flash_restore).start()
    return True


def _copy_and_delete(yank:bool, delete:bool) -> bool:
    """Copy and/or delete selection to clipboard.
       yank  : Yank selection
       delete: Delete selection
    """
    try:
        if not yank and not delete:
            return False
        text_cursor = _get_text_cursor()
        if yank:
            _execute_dispatch(".uno:Copy")
        if delete:
            if text_cursor is not None:
                text_cursor.setString("")
        return True
    except Exception as e:
        _handle_exc(err=e)
        return False


def _yank_and_delete_to_end_of_line(count: int, mode: Mode, yank: bool , delete: bool = True) -> bool:
    """ Delete the characters until the end of the line and
        [count] - 1 more lines. Normal mode commands 'C', 'D', 'Y'."""
    # Makes cursor to collapse to get correct range.
    cursor = _get_cursor()
    if cursor is None:
        return False
    _to_end_of_line(True, count, cursor)
    return _copy_and_delete(yank, delete)


def _paste(count:int, mode, after_cursor:bool):
    """Paste text from clipboard after or before cursor [count] times.
       Commands 'p' and 'P'.
    """
    text_cursor = _get_text_cursor()
    if text_cursor is None:
        return False

    try:
        if after_cursor and not text_cursor.isEndOfParagraph():
            text_cursor.goRight(1, False)

        if mode == "normal":
            controller = _get_controller()
            if controller is None:
                return False
            controller.select(text_cursor)

        for _ in range(count):
            _execute_dispatch(".uno:Paste")

    except Exception as e:
        _handle_exc(err=e)
        return False


def _undo(count=1) -> bool:
    """Undo changes. Command 'u'."""
    doc = _current_doc()
    if doc is None:
        return False
    try:
        for _ in range(count):
            doc.getUndoManager().undo()
        return True
    except Exception as e:
        _handle_exc(err=e)
        # Non-fatal when no more undo actions exist.
        return False


def _redo(count=1) -> bool:
    """Redo changes. Command 'C-r' or 'U'."""
    doc = _current_doc()
    if doc is None:
        return False
    try:
        for _ in range(count):
            doc.getUndoManager().redo()
        return True
    except Exception as e:
        _handle_exc(err=e)
        # Non-fatal when no more undo actions exist.
        return False


# -----------------
# Input handling
# -----------------

# UNO key handler
# Return values for keyPressed/keyReleased:
#   True  -> event is swallowed (LibreOffice should not process it)
#   False -> event is passed through to LibreOffice default handling
class KeyHandler(unohelper.Base, XKeyHandler):
    def __init__(self):
        self._count: int = 0
        self._pending_keys: str | None = None

    # An optional number that may precede the command to multiply or
    # iterate the command.
    @property
    def count(self) -> int:
        count = self._count
        return 1 if count == 0 else count

    # Pending commands like 'd' or 'g'. Type str | None. Note that only
    # operator commands result to operator pending mode.
    @property
    def pending_keys(self) -> str | None:
        return self._pending_keys

    def get_raw_count(self) -> int:
        return self._count

    def _reset_count(self) -> bool:
        self._count = 0
        _update_statusline()
        return True

    def _add_to_count(self, n: int) -> bool:
        try:
            digit = int(n)
        except Exception as e:
            _handle_exc(err=e)
            return False
        if digit < 0:
            return False
        if self.count > 1000:
            return False

        new_count = int(f"{self._count}{digit}")
        self._count = new_count
        _update_statusline()
        return True

    def _add_pending_key(self, new_key: str) -> bool:
        pending_keys = self._pending_keys
        if pending_keys is None:
            self._pending_keys = new_key
        else:
            self._pending_keys = pending_keys + new_key
        _update_statusline()
        return True

    def reset_pending_keys(self):
        self._pending_keys = None
        _update_statusline()
        return

    def _normal_ctrl_actions(self, expand: bool, mode: Mode) -> dict:
        count = self.count
        a_code = int(getattr(Key, "A", 512))
        b_code = int(getattr(Key, "B", 513))
        c_code = int(getattr(Key, "C", 514))
        d_code = int(getattr(Key, "D", 515))
        f_code = int(getattr(Key, "F", 517))
        r_code = int(getattr(Key, "R", 529))
        u_code = int(getattr(Key, "U", 532))
        scroll_count = _get_scroll()

        actions = {
            a_code: lambda: _goto_mode("visual"),
            b_code: lambda: _scroll_window(expand, count, False, mode, False),
            c_code: lambda: self._ctrl_c_command(mode),
            d_code: lambda: _scroll_window(expand, count, True, mode, scroll_count),
            f_code: lambda: _scroll_window(expand, count, True, mode, False),
            r_code: lambda: _redo(count),
            u_code: lambda: _scroll_window(expand, count, False, mode, scroll_count),
        }
        return actions

    # Keymap for Normal/Pending mode commands and Visual mode commands which
    # aren't included in Visual mode keymap.
    def _normal_commands_keymap(self, key, mode, cursor) -> dict:
        # Available commands after "g" command.
        count = self.count
        if "g" in (key.pending or ""):
            actions = {
                # Currently "g" has only motions.
            }
        else:
            do_yank = True if "_" not in (key.pending or "") else False
            actions = {
                "a": lambda: _append_text(cursor),
                "A": lambda: _append_text_to_end_of_line(mode, cursor),
                "c": lambda: self._c_d_commands(key, mode),
                "d": lambda: self._c_d_commands(key, mode),
                "C": lambda: _yank_and_delete_to_end_of_line(count, mode, do_yank),
                "D": lambda: _yank_and_delete_to_end_of_line(count, mode, do_yank),
                "Y": lambda: _yank_and_delete_to_end_of_line(count, mode, False, False),
                "i": lambda: True,
                "I": lambda: _insert_before_first_non_blank(mode, cursor),
                "o": lambda: _begin_new_paragraph(above = False, cursor = cursor),
                "O": lambda: _begin_new_paragraph(above = True, cursor = cursor),
                "p": lambda: _paste(count, mode, after_cursor=True),
                "P": lambda: _paste(count, mode, after_cursor=False),
                "r": lambda: self._r_command(count, key, cursor),
                "s": lambda: _delete_characters(count),
                "S": lambda: _copy_and_delete_linewise(yank = False, delete = True),
                "u": lambda: _undo(count),
                "U": lambda: _redo(count),
                "v": lambda: _goto_mode("visual"),
                "x": lambda: _delete_characters(count),
                "X": lambda: _delete_characters(count, backward = True),
                "y": lambda: self._y_command(key, mode, cursor),
                "/": _focus_findbar,
                # "n": lambda: _repeat_search(count),
                # "N": lambda: _repeat_search(count, backward=True),
            }

        return actions

    def _visual_commands_keymap(self, key, mode, cursor) -> dict:
        do_yank = True if "_" not in (key.pending or "") else False
        actions = {
            "C": lambda: _copy_and_delete_linewise(do_yank, delete = True),
            "D": lambda: _copy_and_delete_linewise(do_yank, delete = True),
            "S": lambda: _copy_and_delete_linewise(yank = False, delete = True),
            "X": lambda: _copy_and_delete_linewise(do_yank, delete = True),
            "Y": lambda: _copy_and_delete_linewise(do_yank, delete = False),
            "o": lambda: _go_to_other_end(mode, cursor),
            "O": lambda: _go_to_other_end(mode, cursor),
            "v": lambda: _goto_mode("visual"),
            "c": lambda: _copy_and_delete(do_yank, True),
            "d": lambda: _copy_and_delete(do_yank, True),
            "s": lambda: _copy_and_delete(yank = False, delete = True),
            "x": lambda: _copy_and_delete(yank = False, delete = True),
            "y": lambda: _yank(key, mode, cursor),
        }
        return actions

    # These can be used independently or with operators.
    def _motions_keymap(self, key, expand, mode: Mode, cursor) -> dict:
        count = self.count
        # Available motions after "g" command.
        if "g" in (key.pending or ""):
            motions = {
                "g": lambda: _to_line(expand, self.get_raw_count(), False, mode, cursor),
                "e": lambda: _word_motion(_WORD_MOTION_GE, expand, count, mode),
                "E": lambda: _word_motion(_WORD_MOTION_G_BIG_E, expand, count, mode)
            }
        else:
            motions = {
                "h": lambda: cursor.goLeft(count, expand),
                "l": lambda: cursor.goRight(count, expand),
                "j": lambda: _lines_down(count, expand, mode, cursor),
                "k": lambda: _lines_up(count, expand, mode, cursor),
                # "b": lambda: _word_motion(_WORD_MOTION_B, expand, count, mode),
                # "e": lambda: _word_motion(_WORD_MOTION_E, expand, count, mode),
                # "w": lambda: _word_motion(_WORD_MOTION_W, expand, count, mode),
                "w": lambda: _to_start_of_words(expand, count, mode, cursor, previous = False),
                "b": lambda: _to_start_of_words(expand, count, mode, cursor, previous = True),
                "e": lambda: _to_end_of_words(expand, count, mode, cursor),
                "W": lambda: _to_start_of_WORDS(expand, count, cursor, direction = "forward"),
                # "W": lambda: _to_start_of_next_WORD(expand, count, mode, cursor, key),
                "B": lambda: _to_start_of_previous_WORD(expand, count, mode, cursor),
                # "B": lambda: _word_motion(_WORD_MOTION_BIG_B, expand, count, mode),
                "E": lambda: _word_motion(_WORD_MOTION_BIG_E, expand, count, mode),
                # "W": lambda: _word_motion(_WORD_MOTION_BIG_W, expand, count, mode),
                "^": lambda: _to_first_non_blank(expand, 0, cursor),
                "$": lambda: _to_end_of_line(expand, count, cursor),
                "H": lambda: _jump_to_page(expand, count, cursor, "start"),
                "L": lambda: _jump_to_page(expand, count, cursor, "end"),
                "G": lambda: _to_line(expand, self.get_raw_count(), True, mode, cursor),
                ")": lambda: _start_of_sentences_forward(expand, count, cursor),
                "(": lambda: _to_start_of_sentences_backwards(expand, count, cursor),
                "}": lambda: _paragraphs_forward(expand, count, cursor),
                "{": lambda: _paragraphs_backward(expand, count, cursor),
                # For testing.
                ";": lambda: _repeat_last_to_character(count, expand, key, cursor),
                ",": lambda: _repeat_last_to_character(count, expand, key, cursor),
                "+": lambda: _to_first_non_blank(expand, count, cursor, False),
                "-": lambda: _to_first_non_blank(expand, count, cursor, True),
                "_": lambda: _to_first_non_blank(expand, count - 1, cursor, False),
            }
            # Monkey patch "0" so it can be used as motion or part of count.
            if self.get_raw_count() == 0:
                # make return True.
                motions["0"] = (lambda: cursor.gotoStartOfLine(expand) or True)
        return motions

    def _normal_text_objects_keymap(self, key, mode, cursor) -> dict:
        count = self.count
        text_objects = {
            "s": lambda: _select_sentence_text_objects(count, key, cursor),
            "p": lambda: _select_paragraph_text_objects(count, key, mode, cursor),
            "w": lambda: _select_word_objects_forward(count, key, mode, cursor),
            "W": lambda: _select_word_objects_forward(count, key, mode, cursor),
        }
        return text_objects

    def _visual_text_objects_keymap(self, key, mode, cursor) -> dict:
        count = self.count
        text_objects = {
            "s": lambda: _expand_with_sentences_objects(count, key, cursor),
            "p": lambda: _expand_with_paragraph_objects(count, key, mode, cursor),
            "w": lambda: _expand_with_word_text_objects(count, key, mode, cursor),
            "W": lambda: _expand_with_word_text_objects(count, key, mode, cursor),
        }
        return text_objects

    # ------------------------------------------
    def keyPressed(self, event):
        state = _state()
        if not state["enabled"]:
            return False

        # Don't do anything if cursor isn't working (as in annotations).
        cursor = _get_cursor()
        if _get_text_cursor() is None or cursor is None:
            return False

        mode: Mode = _get_mode()
        mods: int = _event_modifiers(event)
        code = _key_code(event)
        is_ctrl = self._is_only_ctrl(mods)
        is_escape = self._is_escape(code, is_ctrl)

        # Insert mode matching. Do as little as possible.
        if mode == "insert":
            if is_escape or (is_ctrl and code == 514):  # C-c
                return self._ctrl_c_command(mode)
            return False

        key = KeyEvent(
            char = _normalize_key_char(event),
            code = code,
            pending = self.pending_keys
        )

        count: int = self.count
        expand: bool = _get_mode() in ("visual", "pending")

        # --- Keys with non-shift/AltGr modifiers -------

        if is_ctrl:
            run_command = self._match_ctrl_commands(key, expand, mode)
            if run_command is None:
                return False
            # Allow LO to handle Ctrl-A.
            a_code = int(getattr(Key, "A", 512))
            if key.code == a_code:
                return False
            return True

        # Pass other non-shift modified shortcuts through, except characters
        # made with AltGr.
        if self._has_non_shift_modifier(event):
            if not bool(self._is_altgr_char(event, key)):
                return False

        # --- Keys without modifiers after this --------

        if key.pending == "r":
            if key.char.isprintable() or key.code in (1280, 1282):  # enter, tab
                _replace_characters(count, key, cursor)
                self._reset_count()
                _goto_mode("normal")
            self.reset_pending_keys()
            return True

        # Don't match navigation keys like "Home" or "PageUp" if non-operator command
        # is pending.
        if not (key.pending and mode != "pending"):
            matched_action = self._navigation_keys(expand, mode, cursor).get(key.code)
            if callable(matched_action):
                matched_action()
                return self._reset_count()
            elif matched_action is not None:
                key = KeyEvent(char=matched_action, code=key.code, pending=key.pending)

        # Count parsing. 1..9 always extend count. 0 extends count only after
        # count has started.
        if self._is_digit_char(key.char):
            if key.char != "0" or _get_raw_count() > 0:
                self._add_to_count(int(key.char))
                return True

        # Matches prefixes like "f", "g", "i". Must be before motions.
        added_prefix = self._match_motion_prefix(key, mode)
        if added_prefix is not None:
            return True

        moved = self._match_motions(key, mode, cursor)
        if moved is not None:
            if mode == "pending":
                if moved:
                    return self._apply_pending_operator(key, mode, cursor)
                else:
                    _reset_count()
                    _goto_mode("normal")
            return True

        if mode.startswith("visual"):
            run_command = self._match_visual_commands(key, mode, cursor)
            if run_command is not None:
                return True

        run_command = self._match_normal_commands(key, mode, cursor)
        if run_command is not None:
            return True

        # No suitable commands matched for "g" so cancel.
        if "g" in (key.pending or ""):
            self._reset_count()
            _goto_mode("normal")
            return True

        # ----- Non-character keys after this -----

        if self._is_function_key(event):
            return False

        # No suitable key matched so reset.
        self._reset_count()

        if key.pending or is_escape:
            _goto_mode("normal")
            return True

        # Deliberately let possible Operator-pending mode get canceled before
        # match Del key so it can be used for that.
        if self._is_del_key(event):
            self._del_key(key, mode)
            return True

        if self._is_insert_key(event):
            _goto_mode("insert")

        return True
    # -----------------------------------------
    # Match first letter for multi-part motions.
    def _match_motion_prefix(self, key, mode):
        motion_prefixes = 'fFtTgai'
        if key.pending and key.pending[-1] in motion_prefixes:
            return None

        if key.char in ('fFtT"'):
            self._add_pending_key(key.char)
            return True

        if key.char == "g" and (key.pending is None or mode == "pending"):
            self._add_pending_key("g")
            return True

        if key.pending == '"':
            if key.char == "_":
                self._add_pending_key(key.char)
                return True
            else:
                self._cancel_two_part_motion(mode)
                return True

        if mode not in ("pending", "visual"):
            return None

        if key.char in ("ai"):
            if key.pending is None or mode == "pending":
                return self._add_pending_key(key.char)
            else:
                return self._cancel_two_part_motion(mode)
        return None

    def _match_motions(self, key, mode: Mode, cursor):
        """Handle any matching motion."""
        expand: bool = mode in ("visual", "pending")
        count = self.count
        has_text_obj_prefix = key.pending[-1] in ("ai") if key.pending else False

        if has_text_obj_prefix:
            if mode.startswith("visual"):
                motions = self._visual_text_objects_keymap(key, mode, cursor)
            else:
                motions = self._normal_text_objects_keymap(key, mode, cursor)
        else:
            motions = self._motions_keymap(key, expand, mode, cursor)

        motion: Callable[[], bool] | None
        if key.pending:
            if key.pending[-1] in "fFtT":
                motion = partial(self._ft_commands, expand, key, mode)
            # For dd, cc, yy, S do motion lines down from current line.
            elif mode == "pending" and (key.pending[0] == key.char or key.char == "S"):
                motion = partial(_lines_down, count -1, True, mode, cursor)
            else:
                motion = motions.get(key.char)
        else:
            motion = motions.get(key.char)

        if motion is None:
            if has_text_obj_prefix:
                self._cancel_two_part_motion(mode)
                return False
            return None
        else:
            # Collapse Normal mode cursor so calculating ranges don't have to
            # take that in account.
            if mode in ("normal", "pending"):
                cursor.collapseToStart()

        moved = motion()
        self._reset_prefix()
        self._reset_count()
        return moved

    def _ft_commands(self, expand, key, mode) -> bool:
        if key.pending[-1].lower() not in ("ft"):
            return False
        if not key.char.isprintable():
            if mode == "pending":
                _goto_mode("normal")
            return False
        _set_last_ft(key.pending[-1], key.char)
        count = self.count
        cursor = _get_cursor()
        if cursor is None:
            return False
        return _to_character(expand, count, cursor, key.pending[-1], key.char)

    def _match_visual_commands(self, key, mode, cursor):
        visual_actions = self._visual_commands_keymap(key, mode, cursor)
        action = visual_actions.get(key.char)
        if action is None:
            return None
        did_action = action()
        # After doing action:
        self._reset_count()
        if key.char.lower() in ("cs"):
            _goto_mode("insert")
        elif key.char not in ("oOr"):
            _goto_mode("normal")
        return did_action

    def _match_ctrl_commands(self, key, expand, mode):
        actions = self._normal_ctrl_actions(expand, mode)
        action = actions.get(key.code)
        if action is None:
            return None
        did_action = action()
        self._reset_count()
        return did_action

    def _match_normal_commands(self, key, mode, cursor):
        normal_actions = self._normal_commands_keymap(key, mode, cursor)
        action = normal_actions.get(key.char)
        if action is None:
            return None
        if mode in ("normal", "pending"):
            cursor.collapseToStart()

        did_action = action()
        # After doing action:
        if key.char.lower() in ("aios"):
            self._reset_count()
            _goto_mode("insert")
        elif key.char in ("cdy"):
            _goto_mode("pending")
        elif key.char not in ("rv"):
            self._reset_count()
            _goto_mode("normal")
        return did_action

    def _navigation_keys(self, expand, mode: Mode, cursor):
        count = self.count
        backspace = int(Key.BACKSPACE)
        enter     = int(Key.RETURN)
        left      = int(Key.LEFT)
        right     = int(Key.RIGHT)
        up        = int(Key.UP)
        down      = int(Key.DOWN)
        home      = int(Key.HOME)
        end       = int(Key.END)
        pageup    = int(Key.PAGEUP)   # type: ignore[attr-defined]
        pagedown  = int(Key.PAGEDOWN)  # type: ignore[attr-defined]

        # Navigation keys are mapped to motions so they can take count, be used
        # with operators and for pageup/pagedown handle selection.
        return {
            backspace: "h",
            enter:     lambda: _to_first_non_blank(expand, count, cursor),
            left:      "h",
            right:     "l",
            up:        "k",
            down:      "j",
            home:      "0",
            end:       "$",
            pageup:    lambda: _scroll_window(expand, count, False, mode, False),
            pagedown:  lambda: _scroll_window(expand, count, True, mode, False),
        }

    # If for example after i/a motion non-valid key is entered.
    def _cancel_two_part_motion(self, mode):
        """In Pending mode, return to Normal. In Visual, reset pending
           keys and count but don't change mode."""
        if mode.startswith("visual"):
            self._reset_count()
            self.reset_pending_keys()
        else:
            _goto_mode("normal")
        return True

    @staticmethod
    def _apply_pending_operator(key, mode, cursor) -> bool:
        """Apply pending operator handling mode change."""
        if not key.pending:
            return False

        if "c" in key.pending or "d" in key.pending:
            _copy_and_delete("_" not in key.pending, True)
        elif "y" in key.pending:
            _yank(key, mode, cursor)
        else:
            return False

        new_mode = "insert" if key.pending[0] == "c" else "normal"
        _goto_mode(new_mode)
        return True

    def _c_d_commands(self, key, mode) -> bool:
        if mode == "normal" and key.char in ("cd"):
            self._add_pending_key(key.char)
            return True
        return _copy_and_delete("_" not in key.pending, True)

    def _ctrl_c_command(self, mode: Mode) -> bool:
        if mode == "normal":
            self.reset_pending_keys()
        elif mode.startswith("visual"):
            _copy_and_delete(True, False)
        _goto_mode("normal")
        return True

    def _del_key(self, key, mode) -> bool:
        if mode.startswith("visual"):
            _copy_and_delete(yank = True, delete = True)
        else:
             count = self.count
             if count:
                _delete_characters(count, key)
        _goto_mode("normal")
        return True

    @staticmethod
    def _has_non_shift_modifier(event):
        mods = _event_modifiers(event)
        return bool(mods & (KeyModifier.MOD1 | KeyModifier.MOD2 | KeyModifier.MOD3))

    @staticmethod
    def _is_altgr_char(event, key) -> bool:
        if not (isinstance(key.char, str) and len(key.char) == 1 and ord(key.char) >= 32):
            return False
        mods = _event_modifiers(event)
        mod2 = getattr(KeyModifier, "MOD2", 0)
        mod3 = getattr(KeyModifier, "MOD3", 0)
        # Treat AltGr as text-producing modified input. In this environment these
        # events arrive with key_code == 0 (e.g. AltGr+4 -> "$"), while normal
        # Ctrl/Alt shortcuts have concrete key codes.
        return bool(mods & mod2) and not bool(mods & mod3) and key.code == 0

    @staticmethod
    def _is_digit_char(ch):
        return isinstance(ch, str) and len(ch) == 1 and "0" <= ch <= "9"

    @staticmethod
    def _is_escape(key_code, is_ctrl):
        # Ctrl+[ is interpreted as Esc like in terminal.
        return (key_code == 1281) or (
            key_code == 1315 and is_ctrl
        )

    @staticmethod
    def _is_insert_key(event):
        try:
            return _key_code(event) == int(getattr(Key, "INSERT"))
        except Exception as e:
            _handle_exc(err=e)
            return False

    @staticmethod
    def _is_del_key(event):
        try:
            return _key_code(event) == int(getattr(Key, "DELETE"))
        except Exception as e:
            _handle_exc(err=e)
            return False

    @staticmethod
    def _is_function_key(event):
        key_code = _key_code(event)
        for i in range(1, 13):
            try:
                if key_code == int(getattr(Key, f"F{i}")):
                    return True
            except Exception as e:
                _handle_exc(err=e)
                continue
        return False

    @staticmethod
    def _is_only_ctrl(mods):
        ctrl = getattr(KeyModifier, "MOD1", 0)
        alt = getattr(KeyModifier, "MOD2", 0)
        meta = getattr(KeyModifier, "MOD3", 0)
        return bool(mods & ctrl) and not bool(mods & (alt | meta))

    def _reset_prefix(self) -> bool:
        """Remove last pending command if it's a command prefix:
           g, a/i, or f/F/t/T.
        """
        pending_keys = self._pending_keys
        if not pending_keys or pending_keys[-1] not in "aigfFtT":
            return False
        remaining_keys = pending_keys[:-1]
        if remaining_keys:
            self._pending_keys = remaining_keys
        else:
            self.reset_pending_keys()
        return True

    def _r_command(self, count, key, cursor) -> bool:
        if key.pending is None:
            self._add_pending_key("r")
            return True
        return _replace_characters(count, key, cursor)

    def _y_command(self, key, mode, cursor) -> bool:
        if mode == "normal" and key.pending is None:
            # Save cursor position so it can be restored after flashing
            # yanked region.
            _set_position()
            self._add_pending_key("y")
            return True
        return _yank(key, mode, cursor)

    def keyReleased(self, event):
        state = _state()
        if not state["enabled"]:
            return False
        if state["mode"] == "normal":
            _show_cursor("normal")
            return True
        return False

    def disposing(self, event):
        return None


# Normalize UNO key event payload into a single-character command key when possible.
# Handles runtime-specific KeyChar/KeyCode representations used by LO/UNO.
def _normalize_key_char(event) -> str:
    k = event.KeyChar
    key_code = _key_code(event)

    # NUM0..NUM9 can represent either digits or symbols depending on layout.
    # If KeyChar already carries a printable symbol (e.g. AltGr+4 -> "$"),
    # prefer it over key-code based reconstruction.
    if 256 <= key_code <= 265:
        if isinstance(k, str) and len(k) == 1 and ord(k) >= 32:
            return k
        shift_mask = getattr(KeyModifier, "SHIFT", 1)
        is_shift = bool(_event_modifiers(event) & shift_mask)
        if is_shift:
            return ")!@#$%^&*("[key_code - 256]
        return chr(ord("0") + (key_code - 256))

    if k is None:
        return ""

    if isinstance(k, str):
        return k

    # UNO key chars can arrive as non-str objects. Prefer textual form first.
    try:
        # Some runtimes expose UNO Char wrappers like "<Char instance $>".
        for attr in ("value", "Value", "char", "Char"):
            v = getattr(k, attr, None)
            if isinstance(v, str) and len(v) == 1:
                return v
        s = str(k)
        if len(s) == 1:
            return s
        prefix = "<Char instance "
        if s.startswith(prefix) and s.endswith(">"):
            inner = s[len(prefix):-1]
            if len(inner) == 1:
                return inner
    except Exception as e:
        _handle_exc(err=e)
        pass

    try:
        code = int(k)
    except Exception as e:
        _handle_exc(err=e)
        code = -1

    if 0 <= code <= 255:
        return chr(code)

    # Fallback: some environments deliver characters as key codes.
    try:
        key_code = int(event.KeyCode)
        # com.sun.star.awt.Key.A..Z are typically 512..537.
        if 512 <= key_code <= 537:
            # Respect Shift when falling back to key codes, otherwise "HJKLIX"
            # would be treated as lowercase normal-mode commands.
            shift_mask = getattr(KeyModifier, "SHIFT", 1)
            is_shift = bool(_event_modifiers(event) & shift_mask)
            base = ord("A") if is_shift else ord("a")
            return chr(base + (key_code - 512))

        # Ignore NUL keycode (0); it is often a non-printable placeholder.
        if 1 <= key_code <= 255:
            return chr(key_code)

    except Exception as e:
        _handle_exc(err=e)
        pass

    return ""


def _event_modifiers(event):
    try:
        return int(event.Modifiers)
    except Exception as e:
        _handle_exc(err=e)
        return 0


# NOTE: Not used currently.
def _is_ctrl_shift(mods):
    shift = getattr(KeyModifier, "SHIFT", 1)
    ctrl = getattr(KeyModifier, "MOD1", 0)
    alt = getattr(KeyModifier, "MOD2", 0)
    meta = getattr(KeyModifier, "MOD3", 0)
    return (
        bool(mods & ctrl) and
        bool(mods & shift) and
        not bool(mods & (meta | alt))
    )


def _key_code(event):
    try:
        return int(event.KeyCode)
    except Exception as e:
        _handle_exc(err=e)
        return -1

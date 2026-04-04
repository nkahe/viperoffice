from __future__ import annotations
from typing import TYPE_CHECKING
from com.sun.star.awt import Rectangle

if TYPE_CHECKING:
    from com.sun.star.text import XTextCursor
    from core import (  # noqa: F401
        _get_visual_caret_range,
        _get_controller,
        _handle_exc,
        _paragraph_scan_steps,
)

# For debugging

def _clone_text_range(text_cursor) -> XTextCursor | None:
    """Return a cloned TextCursor positioned at the start of text_cursor. """
    try:
        return text_cursor.getText().createTextCursorByRange(text_cursor.getStart())
    except Exception as e:
        _handle_exc(err=e)
        return None


def _describe_text_range(range) -> str:  # noqa: F811  # pyright: ignore[reportUnusedFunction]
    """Return a human-readable description of an XTextRange-like object.

    Output includes the range text (trimmed) and start/end offsets measured from
    the start of the containing paragraph (end is exclusive). Returns a short
    placeholder if the range is None or unprintable.
    """
    try:
        if range is None:
            return "None"
        text = range.getString()
        para = range.getText()
        # Compute start offset relative to paragraph start
        start_range = range.getStart()
        start_cursor = para.createTextCursorByRange(start_range)
        start_cursor.gotoStartOfParagraph(False)
        start_cursor.gotoRange(start_range, True)
        start_offset = len(start_cursor.getString())
        # Compute end offset relative to paragraph start (exclusive)
        end_range = range.getEnd()
        end_cursor = para.createTextCursorByRange(end_range)
        end_cursor.gotoStartOfParagraph(False)
        end_cursor.gotoRange(end_range, True)
        end_offset = len(end_cursor.getString())
        snippet = text.replace("\n", "\\n")
        if len(snippet) > 120:
            snippet = snippet[:117] + "..."
        return f"'{snippet}' (start_offset={start_offset}, end_excl={end_offset})"
    except Exception as e:
        _handle_exc(err=e)
        try:
            return f"<unprintable range: {range}>"
        except Exception as e:
            _handle_exc(err=e)
            return "<unprintable range>"


def _is_current_paragraph_empty(text_cursor) -> bool:
    if text_cursor is None:
        return False
    try:
        probe = _clone_text_range(text_cursor)
        if probe is None:
            return False
        probe.gotoStartOfParagraph(False)
        probe.gotoEndOfParagraph(True)
        return len(probe.getString()) == 0
    except Exception as e:
        _handle_exc(err=e)
        return False


def _range_after_paragraph_break(text_range):
    try:
        text_obj = text_range.getText()
        probe = text_obj.createTextCursorByRange(text_range)
        if probe.goRight(1, False):
            return probe.getStart()
    except Exception as e:
        _handle_exc(err=e)
        pass
    return None


def _is_cursor_at_whitespace(text_cursor, condition:str|None=None) -> bool:
    """Return True if cursor is on a whitespace character.

    condition: optional qualifier for additional check:
        None               – any whitespace at cursor position.
        "after_sentence"   – whitespace that immediately follows a sentence end
                             (., !, ?), ruling out mid-sentence whitespace.
        "before_paragraph" – whitespace at the start of a paragraph (paragraph
                             begins with whitespace characters).
    """
    if text_cursor is None:
        return False
    try:
        caret = _get_visual_caret_range(text_cursor)
        probe = text_cursor.getText().createTextCursorByRange(caret)
        if not probe.goRight(1, True):
            return False
        if probe.getString() not in (" ", "\t", "\n"):
            return False

        if condition is None:
            return True

        if _is_current_paragraph_empty(probe):
            return False

        if condition == "after_sentence":
        # Walk backwards past whitespace and closing punctuation to find sentence end.
            probe.collapseToStart()
            ch = ""
            for _ in _paragraph_scan_steps():
                if not probe.goLeft(1, True):
                    break
                ch = probe.getString()
                probe.collapseToStart()
                if ch not in (" ", "\t", "\"", "'", ")", "]"):
                    break
            return ch in (".", "!", "?")

        elif condition == "before_paragraph":
            # Check that the caret is within leading whitespace of the paragraph.
            caret = _get_visual_caret_range(text_cursor)
            para_probe = text_cursor.getText().createTextCursorByRange(caret)
            para_probe.gotoStartOfParagraph(False)
            para_probe.gotoRange(caret, True)
            leading = para_probe.getString()
            return len(leading) == 0 or all(c in (" ", "\t") for c in leading)
        else:
            return False
    except Exception as e:
        _handle_exc(err=e)
        return False

# UNO doesn't offer call to get caret position when there's selection. Usually
# state.visual_anchor is set and tracked but for situations it's not available
# this can be used.
def _is_forward_selection(cursor) -> bool:
    """Return True if caret is at right end of selection, False if at left end."""
    try:
        original_len = len(cursor.getString())
        moved = cursor.goRight(1, True)
        if moved:
            new_len = len(cursor.getString())
            cursor.goLeft(1, True)
            return new_len > original_len
        return True
    except Exception as e:
        _handle_exc(err=e)
        return True


def msg(text, title="ViperOffice"): # noqa: F811  # pyright: ignore[reportUnusedFunction]
    """Show [text] in a pop-up window."""
    try:
        controller = _get_controller()
        if controller is None:
            return
        parent = controller.getFrame().getContainerWindow()
        toolkit = parent.getToolkit()
        try:
            # Legacy UNO signature used by some versions.
            box = toolkit.createMessageBox(
                parent, Rectangle(), "infobox", 1, title, str(text),
            )
        except Exception as e:
            _handle_exc(err=e)
            # Newer UNO signature used by some versions.
            box = toolkit.createMessageBox(
                parent, 1, 1, title, str(text),
            )
        box.execute()
    except Exception as e:
        _handle_exc(err=e)
        pass



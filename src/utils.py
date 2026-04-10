from __future__ import annotations
from typing import TYPE_CHECKING, Any
from com.sun.star.awt import Rectangle

if TYPE_CHECKING:
    from com.sun.star.text import XTextCursor
    from core import (  # noqa: F401
        _get_text_cursor,
        _get_visual_anchor,
        _get_visual_caret_range,
        _get_controller,
        _handle_exc,
        _paragraph_scan_steps,
)

# -------------------
# Utility functions
# -------------------

def _clone_text_range(tc, end: bool = False) -> XTextCursor:
    """Return a cloned text cursor positioned at the start of text_cursor by
    default. end=True : use end of range."""
    range = tc.getEnd() if end else tc.getStart()
    return tc.getText().createTextCursorByRange(range)


# For debugging
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
        # Compute start offset relative to paragraph start
        start_range = range.getStart()
        start_cursor = _clone_text_range(range)
        start_cursor.gotoStartOfParagraph(False)
        start_cursor.gotoRange(start_range, True)
        start_offset = len(start_cursor.getString())
        # Compute end offset relative to paragraph start (exclusive)
        end_range = range.getEnd()
        end_cursor = _clone_text_range(range, end=True)
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


def _is_current_paragraph_empty(tc) -> bool:
    if tc is None:
        return False
    try:
        probe = _clone_text_range(tc)
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


def _is_cursor_at_whitespace(tc, condition:str|None=None) -> bool:
    """Return True if cursor is on a whitespace character.

    condition: optional qualifier for additional check:
        None               – any whitespace at cursor position.
        "after_sentence"   – whitespace that immediately follows a sentence end
                             (., !, ?), ruling out mid-sentence whitespace.
        "before_paragraph" – whitespace at the start of a paragraph (paragraph
                             begins with whitespace characters).
    """
    if tc is None:
        return False
    try:
        caret = _get_visual_caret_range(tc)
        probe = tc.getText().createTextCursorByRange(caret)
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
            caret = _get_visual_caret_range(tc)
            para_probe = tc.getText().createTextCursorByRange(caret)
            para_probe.gotoStartOfParagraph(False)
            para_probe.gotoRange(caret, True)
            leading = para_probe.getString()
            return len(leading) == 0 or all(c in (" ", "\t") for c in leading)
        else:
            return False
    except Exception as e:
        _handle_exc(err=e)
        return False


def _is_at_first_non_whitespace_after_leading_ws(tc) -> bool:
    """Return True if cursor is at first non-whitespace after leading paragraph whitespace.
    """
    if tc is None:
        return False
    try:
        if tc.isStartOfParagraph():
            return False
        para_probe = _clone_text_range(tc)
        para_probe.gotoStartOfParagraph(False)
        para_probe.gotoRange(tc.getStart(), True)
        return all(c in (" ", "\t") for c in para_probe.getString())
    except Exception as e:
        _handle_exc(err=e)
        return False


# UNO doesn't offer call to get caret position when there's selection. Usually
# state.visual_anchor is set and tracked but for situations it's not available
# this can be used.
def _is_forward_selection(tc) -> bool:
    """Return True if caret is at right end of selection, False if at left end."""
    try:
        if not tc:
            return False
        original_len = len(tc.getString())
        moved = tc.goRight(1, True)
        if moved:
            new_len = len(tc.getString())
            tc.goLeft(1, True)
            return new_len > original_len
        return True
    except Exception as e:
        _handle_exc(err=e)
        return True

def msg(text, title="ViperOffice"): # noqa: F811  # pyright: ignore[reportUnusedFunction]
    """Show [text] in a pop-up window for debug."""
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


def _pos_xy(pos: object) -> tuple[Any, Any]:
    """Extract (X, Y) coordinates from a UNO position object, handling both
    attribute and method forms."""
    if pos is None:
        return (None, None)
    x = getattr(pos, "X", None)
    y = getattr(pos, "Y", None)
    if callable(x):
        x = x()
    if callable(y):
        y = y()
    return (x, y)


def _same_pos(a, b):
    return _pos_xy(a) == _pos_xy(b)


def _sync_view_cursor(text_cursor, expand: bool, view_cursor, backward: bool = False):
    """Sync the visible cursor to a text cursor position.

    When `expand` is False, the view cursor is collapsed to the text cursor's
    start. When `expand` is True, the selection edge comes from either the start
    or end of `text_cursor` depending on `backward`, and existing visual-anchor
    state is preserved when available.
    """
    if expand and backward:
        edge = text_cursor.getStart()
    elif expand:
        edge = text_cursor.getEnd()
    else:
        edge = text_cursor.getStart()
    anchor = _get_visual_anchor() if expand else None
    if anchor is not None:
        # Visual mode: use _set_visual_selection so direction changes work correctly.
        _set_visual_selection(view_cursor, anchor, edge)
    else:
        view_cursor.gotoRange(edge, expand)


def _set_visual_selection(cursor, anchor, new_caret, force_backward: bool = False):
    """Rebuild visual selection between fixed anchor and new caret position.

    gotoRange(pos, True) on a view cursor only moves the RIGHT end, so we always
    expand rightward: collapse to the leftmost of (anchor, new_caret) first, then
    expand right to the other. This handles direction changes (crossing the anchor).
    """
    try:
        text = anchor.getText()
        # Expand anchor → new_caret to cover both positions, then check which
        # endpoint is the anchor. If anchor is the left end, new_caret is to the
        # right (forward); otherwise new_caret is to the left (backward).
        span = text.createTextCursorByRange(anchor.getStart())
        span.gotoRange(new_caret, True)
        left_check = text.createTextCursorByRange(span.getStart())
        left_check.gotoRange(anchor.getStart(), True)
        anchor_is_left = len(left_check.getString()) == 0

        if anchor_is_left:
            _ensure_visual_caret(cursor, True)
            new_length = _range_length_between(anchor, new_caret)
            try:
                current_right = cursor.getEnd()
            except Exception as e:
                _handle_exc(err=e)
                current_right = None
            prev_length = _range_length_between(anchor, current_right) if current_right is not None else 0
            # Only use incremental goRight when extending an existing forward selection
            # (prev_length > 0). When prev_length == 0 the cursor's right end equals
            # the anchor, meaning the current selection was backward (caret left of
            # anchor); goRight from the caret would overshoot, so rebuild instead.
            if prev_length > 0 and current_right is not None and _range_ends_before(current_right, new_caret):
                delta = new_length - prev_length
                if delta > 0 and _try_go_right(cursor, delta):
                    return
            # Rebuild selection for first expansion, direction change, or when
            # incremental path fails.
            cursor.gotoRange(anchor, False)
            cursor.gotoRange(new_caret, True)
            return

        if _range_starts_before(new_caret, anchor):
            _ensure_visual_caret(cursor, False)
            try:
                current_left = cursor.getStart()
            except Exception as e:
                _handle_exc(err=e)
                current_left = None
            prev_length = _range_length_between(current_left, anchor) if current_left is not None else 0
            new_length = _range_length_between(new_caret, anchor)
            if not force_backward and current_left is not None and \
                _range_starts_before(new_caret, current_left):
                delta = prev_length - new_length
                if delta > 0 and _try_go_left(cursor, delta):
                    return

            cursor.gotoRange(anchor, False)
            distance = _range_length_between(new_caret, anchor)
            if _try_go_left(cursor, distance):
                return

        # Fallback: collapse directly to the requested caret range.
        cursor.gotoRange(new_caret, True)
    except Exception as e:
        _handle_exc(err=e)
        pass


def _range_length_between(left_range, right_range) -> int:
    if left_range is None or right_range is None:
        return 0
    try:
        if _range_starts_before(right_range, left_range):
            left_range, right_range = right_range, left_range
        text = left_range.getText()
        span = text.createTextCursorByRange(left_range)
        span.gotoRange(right_range, True)
        return len(span.getString())
    except Exception as e:
        _handle_exc(err=e)
        return 0


def _range_starts_before(range_a, range_b) -> bool:
    if range_a is None or range_b is None:
        return False
    try:
        text = range_a.getText()
        # compareRegionStarts returns 1 when range_a starts before range_b.
        return text.compareRegionStarts(range_a, range_b) == 1
    except Exception as e:
        _handle_exc(err=e)
        return False


def _range_ends_before(range_a, range_b) -> bool:
    if range_a is None or range_b is None:
        return False
    try:
        text = range_a.getText()
        return text.compareRegionEnds(range_a, range_b) == 1
    except Exception as e:
        _handle_exc(err=e)
        return False


def _try_go_left(cursor, distance: int) -> bool:
    """Move text cursor left by distance characters with selection,
    temporarily hiding it to avoid visual flicker.

    Returns True if the cursor moved successfully.
    """
    if cursor is None or distance <= 0:
        return False
    moved = False
    hide_cursor = False
    try:
        visible_before = None
        try:
            visible_before = cursor.isVisible()
        except Exception as e:
            _handle_exc(err=e)
            visible_before = None
        if visible_before:
            cursor.setVisible(False)
            hide_cursor = True
        moved = cursor.goLeft(distance, True)
    except Exception as e:
        _handle_exc(err=e)
        moved = False
    finally:
        if hide_cursor:
            cursor.setVisible(True)
    return moved


def _try_go_right(cursor, distance: int) -> bool:
    """Move text cursor right by distance characters with selection,
    temporarily hiding it to avoid visual flicker.

    Returns True if the cursor moved successfully.
    """
    if cursor is None or distance <= 0:
        return False
    moved = False
    hide_cursor = False
    try:
        visible_before = None
        try:
            visible_before = cursor.isVisible()
        except Exception as e:
            _handle_exc(err=e)
            visible_before = None
        if visible_before:
            cursor.setVisible(False)
            hide_cursor = True
        moved = cursor.goRight(distance, True)
    except Exception as e:
        _handle_exc(err=e)
        moved = False
    finally:
        if hide_cursor:
            cursor.setVisible(True)
    return moved


def _ensure_visual_caret(cursor, at_end: bool) -> None:
    """Ensure view cursor caret is on the requested selection end without
    changing selection.
    """
    if cursor is None:
        return
    try:
        if at_end:
            cursor.gotoRange(cursor.getEnd(), True)
        else:
            cursor.gotoRange(cursor.getStart(), True)
    except Exception as e:
        _handle_exc(err=e)
        pass


from __future__ import annotations

from core import (  # noqa: F401
    KeyEvent,
    Mode,
    _get_text_cursor,
    _get_visual_caret_range,
    _handle_exc,
    _paragraph_scan_steps,
    _set_visual_anchor,
)

from utils import (   # type: ignore[reportMissingImports]
    _is_current_paragraph_empty,
    _is_forward_selection,
    _range_after_paragraph_break,
    _sync_view_cursor_to_text_cursor,
    _to_next_non_empty_paragraph,
)

# Paragraph motions

def _to_previous_non_empty_paragraph(text_cursor, expand: bool) -> bool:
    moved = False
    for _ in _paragraph_scan_steps():
        if not text_cursor.gotoPreviousParagraph(expand):
            break
        moved = True
        if not _is_current_paragraph_empty(text_cursor):
            break
    return moved


def _normalize_paragraph_text_object_start(text_cursor, cursor, started_empty: bool) -> bool:
    """Normalize paragraph text-object selection start.

    Moves the cursors to the start of the current paragraph or empty-line block
    so 'ip'/'ap' selections expand from a stable anchor.
    """
    if text_cursor is None or cursor is None:
        return False
    try:
        if started_empty:
            if not _move_to_empty_block_start(text_cursor):
                return False
            cursor.gotoRange(text_cursor.getStart(), False)
            return True

        if not text_cursor.isStartOfParagraph():
            text_cursor.gotoStartOfParagraph(False)
        cursor.gotoRange(text_cursor.getStart(), False)
        return True
    except Exception as e:
        _handle_exc(err=e)
        return False


def _move_to_empty_block_start(text_cursor) -> bool:
    if not _is_current_paragraph_empty(text_cursor):
        return False
    text_cursor.gotoStartOfParagraph(False)
    for _ in _paragraph_scan_steps():
        if not text_cursor.gotoPreviousParagraph(False):
            text_cursor.gotoStartOfParagraph(False)
            return True
        if not _is_current_paragraph_empty(text_cursor):
            text_cursor.gotoNextParagraph(False)
            text_cursor.gotoStartOfParagraph(False)
            return True
        text_cursor.gotoStartOfParagraph(False)
    return False


def _consume_empty_block_forward(text_cursor):
    end_range = None
    for _ in _paragraph_scan_steps():
        text_cursor.gotoEndOfParagraph(False)
        end_range = text_cursor.getEnd()
        if not text_cursor.gotoNextParagraph(False):
            text_cursor.gotoStartOfParagraph(False)
            return end_range, False
        if not _is_current_paragraph_empty(text_cursor):
            text_cursor.gotoStartOfParagraph(False)
            return end_range, True
    return end_range, False


def _extend_selection_after_empty_block(text_cursor, cursor):
    if text_cursor.gotoPreviousParagraph(False):
        text_cursor.gotoEndOfParagraph(False)
        end_after = _range_after_paragraph_break(text_cursor.getEnd())
        if end_after is not None:
            cursor.gotoRange(end_after, True)


def _extend_selection_to_paragraph_end(text_cursor, cursor):
    text_cursor.gotoEndOfParagraph(False)
    cursor.gotoRange(text_cursor.getEnd(), True)


def _extend_selection_over_trailing_empty_block(text_cursor, cursor):
    end_range, has_next = _consume_empty_block_forward(text_cursor)
    if end_range is not None:
        end_after = _range_after_paragraph_break(end_range) if has_next else end_range
        cursor.gotoRange(end_after or end_range, True)


def _extend_selection_over_leading_empty_block(text_cursor, cursor):
    if not text_cursor.gotoPreviousParagraph(False):
        return
    if not _is_current_paragraph_empty(text_cursor):
        text_cursor.gotoNextParagraph(False)
        return
    _move_to_empty_block_start(text_cursor)
    cursor.gotoRange(text_cursor.getStart(), True)
    text_cursor.gotoNextParagraph(False)


def _post_adjust_paragraph_text_object(text_cursor, cursor, is_around, started_empty):
    if started_empty:
        if not _is_current_paragraph_empty(text_cursor):
            _extend_selection_after_empty_block(text_cursor, cursor)
        if not is_around:
            return True
        if not _is_current_paragraph_empty(text_cursor):
            _extend_selection_to_paragraph_end(text_cursor, cursor)
        return True

    if not is_around:
        return True

    if _is_current_paragraph_empty(text_cursor):
        _extend_selection_over_trailing_empty_block(text_cursor, cursor)
    return True


def _select_ap_units_forward_visual(count: int, cursor, started_empty: bool) -> bool:
    steps = max(1, int(count))
    moved = False
    for i in range(steps):
        if not _paragraphs_forward(True, 1, cursor):
            break
        moved = True
        text_cursor = _get_text_cursor()
        if text_cursor is None:
            return False
        if i == 0 and started_empty:
            _post_adjust_paragraph_text_object(text_cursor, cursor, True, True)
        elif _is_current_paragraph_empty(text_cursor):
            _extend_selection_over_trailing_empty_block(text_cursor, cursor)
    return moved


def _select_ap_units_backward_visual(count: int, cursor) -> bool:
    steps = max(1, int(count))
    moved = False
    for _ in range(steps):
        if not _paragraphs_backward(True, 1, cursor):
            break
        moved = True
        text_cursor = _get_text_cursor()
        if text_cursor is None:
            return False
        _extend_selection_over_leading_empty_block(text_cursor, cursor)
    return moved


def _paragraphs_forward(expand: bool, count: int, cursor) -> bool:
    """Motion to for [count] paragraphs forward. Command '}'.

    From a non-empty paragraph, moves to the start of the next paragraph
    (which may itself be an empty separator line). From an empty separator
    line, jumps past all consecutive empty lines to the first non-empty
    paragraph start.
    """
    text_cursor = _get_text_cursor()
    if text_cursor is None:
        return False
    try:
        if expand:
            caret = _get_visual_caret_range(text_cursor)
            text_cursor.gotoRange(caret, False)
        steps = max(1, int(count))
        moved_any = False
        for _ in range(steps):
            if _is_current_paragraph_empty(text_cursor):
                moved = _to_next_non_empty_paragraph(text_cursor, expand)
            else:
                moved = bool(text_cursor.gotoNextParagraph(expand))
            if not moved:
                # Last paragraph with no following empty line: move to end of it.
                if not text_cursor.isEndOfParagraph():
                    text_cursor.gotoEndOfParagraph(expand)
                    _sync_view_cursor_to_text_cursor(text_cursor, expand, cursor)
                    moved_any = True
                break
            moved_any = True
        if moved_any:
            _sync_view_cursor_to_text_cursor(text_cursor, expand, cursor)
        return moved_any
    except Exception as e:
        _handle_exc(err=e)
        return False


def _paragraphs_backward(expand: bool, count: int, cursor) -> bool:
    """Motion for [count] paragraphs backward. Command '{'.

    From inside a paragraph, moves to the start of the current paragraph.
    From the start of a non-empty paragraph, moves to the start of the
    previous paragraph (which may be an empty separator line). From an
    empty separator line, skips backward past all consecutive empty lines
    to the start of the previous non-empty paragraph.
    """
    text_cursor = _get_text_cursor()
    if text_cursor is None:
        return False
    try:
        if expand:
            caret = _get_visual_caret_range(text_cursor)
            text_cursor.gotoRange(caret, False)
        steps = max(1, int(count))
        moved_any = False
        for _ in range(steps):
            if not text_cursor.isStartOfParagraph():
                text_cursor.gotoStartOfParagraph(expand)
                moved = True
            elif _is_current_paragraph_empty(text_cursor):
                moved = _to_previous_non_empty_paragraph(text_cursor, expand)
            else:
                moved = bool(text_cursor.gotoPreviousParagraph(expand))
            if not moved:
                break
            moved_any = True
        if moved_any:
            _sync_view_cursor_to_text_cursor(text_cursor, expand, cursor, backward=True)
        return moved_any
    except Exception as e:
        _handle_exc(err=e)
        return False


def _select_paragraph_text_objects(count: int, key: KeyEvent, mode: Mode, cursor):
    """
    Select "ip"/"ap" paragraph text-objects forward from the start of current object.

    ip: inner paragraph is either a text paragraph or a contiguous empty-line block.
    ap: a paragraph is text paragraph plus trailing empty lines (forward) or
        leading empty lines (backward in visual mode).
    """
    text_cursor = _get_text_cursor()
    if text_cursor is None:
        return False
    is_around = key.pending is not None and key.pending[-1] == "a"

    started_empty = _is_current_paragraph_empty(text_cursor)
    if not _normalize_paragraph_text_object_start(text_cursor, cursor, started_empty):
        return False

    if mode.startswith("visual"):
        text_cursor = _get_text_cursor()
        if text_cursor:
            _set_visual_anchor(text_cursor.getStart())

    moved = _paragraphs_forward(True, count, cursor)
    if not moved:
        return False

    text_cursor = _get_text_cursor()
    if text_cursor is None:
        return False
    return _post_adjust_paragraph_text_object(text_cursor, cursor, is_around, started_empty)


def _expand_with_paragraph_objects(count: int, key: KeyEvent, mode: Mode, cursor) -> bool:
    """Extend an existing visual selection by ip/ap paragraph text objects.

    Called when cursor already has a selection Determines direction from the
    saved anchor vs caret position, then delegates to the appropriate
    forward/backward helper.
    """
    text_cursor = _get_text_cursor()
    if text_cursor is None:
        return False
    is_around = key.pending is not None and key.pending[-1] == "a"
    caret = _get_visual_caret_range(text_cursor)

    select_forward = _is_forward_selection(cursor)

    if len(cursor.getString()) == 0:
        _select_paragraph_text_objects(count, key, mode, cursor)
        return True

    text_cursor.gotoRange(caret, False)
    started_empty = _is_current_paragraph_empty(text_cursor)
    if started_empty:
        _move_to_empty_block_start(text_cursor)
        cursor.gotoRange(text_cursor.getStart(), True)

    if not select_forward:
        if is_around:
            moved = _select_ap_units_backward_visual(count, cursor)
        else:
            moved = _paragraphs_backward(True, count, cursor)
    elif is_around:
        return _select_ap_units_forward_visual(count, cursor, started_empty)
    else:
        moved = _paragraphs_forward(True, count, cursor)

    if not moved:
        return False
    if not select_forward:
        return True

    text_cursor = _get_text_cursor()
    if text_cursor is None:
        return False
    return _post_adjust_paragraph_text_object(text_cursor, cursor, is_around, started_empty)


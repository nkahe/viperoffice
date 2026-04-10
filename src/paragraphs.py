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
    _sync_view_cursor,
)

# -------------------
# Paragraph motions
# -------------------

def _to_previous_non_empty_paragraph(tc, expand: bool, cross_empty: bool = False) -> bool:

    """Move text cursor backward one or more paragraphs.

    When cross_empty is False (default) empty paragraphs are skipped until a
    non-empty one is found. When cross_empty is True the cursor stops at the
    first previous paragraph regardless of whether it is empty.
    Uses _paragraph_scan_steps to guard against runaway loops.
    """
    moved = False
    for _ in _paragraph_scan_steps():
        if not tc.gotoPreviousParagraph(expand):
            break
        moved = True
        if cross_empty or not _is_current_paragraph_empty(tc):
            break
    return moved


def _to_next_non_empty_paragraph(tc, expand: bool, cross_empty: bool = False) -> bool:
    """Move text_cursor forward one or more paragraphs.

    When cross_empty is False (default) empty paragraphs are skipped until a
    non-empty one is found. When cross_empty is True the cursor stops at the
    first next paragraph regardless of whether it is empty.
    Uses _paragraph_scan_steps to guard against runaway loops.
    """
    moved = False
    for _ in _paragraph_scan_steps():
        if not tc.gotoNextParagraph(expand):
            break
        moved = True
        if cross_empty or not _is_current_paragraph_empty(tc):
            break
    return moved


def _normalize_paragraph_text_object_start(tc, cursor, started_empty: bool) -> bool:
    """Normalize paragraph text-object selection start.

    Moves the cursors to the start of the current paragraph or empty-line block
    so 'ip'/'ap' selections expand from a stable anchor.
    """
    if tc is None or cursor is None:
        return False
    try:
        if started_empty:
            if not _move_to_empty_block_start(tc):
                return False
            cursor.gotoRange(tc.getStart(), False)
            return True

        if not tc.isStartOfParagraph():
            tc.gotoStartOfParagraph(False)
        cursor.gotoRange(tc.getStart(), False)
        return True
    except Exception as e:
        _handle_exc(err=e)
        return False


def _move_to_empty_block_start(tc) -> bool:
    if not _is_current_paragraph_empty(tc):
        return False
    tc.gotoStartOfParagraph(False)
    for _ in _paragraph_scan_steps():
        if not tc.gotoPreviousParagraph(False):
            tc.gotoStartOfParagraph(False)
            return True
        if not _is_current_paragraph_empty(tc):
            tc.gotoNextParagraph(False)
            tc.gotoStartOfParagraph(False)
            return True
        tc.gotoStartOfParagraph(False)
    return False


def _consume_empty_block_forward(tc):
    end_range = None
    for _ in _paragraph_scan_steps():
        tc.gotoEndOfParagraph(False)
        end_range = tc.getEnd()
        if not tc.gotoNextParagraph(False):
            tc.gotoStartOfParagraph(False)
            return end_range, False
        if not _is_current_paragraph_empty(tc):
            tc.gotoStartOfParagraph(False)
            return end_range, True
    return end_range, False


def _extend_selection_after_empty_block(tc, cursor):
    if tc.gotoPreviousParagraph(False):
        tc.gotoEndOfParagraph(False)
        end_after = _range_after_paragraph_break(tc.getEnd())
        if end_after is not None:
            cursor.gotoRange(end_after, True)


def _extend_selection_to_paragraph_end(tc, cursor):
    tc.gotoEndOfParagraph(False)
    cursor.gotoRange(tc.getEnd(), True)


def _extend_selection_over_trailing_empty_block(tc, cursor):
    end_range, has_next = _consume_empty_block_forward(tc)
    if end_range is not None:
        end_after = _range_after_paragraph_break(end_range) if has_next else end_range
        cursor.gotoRange(end_after or end_range, True)


def _extend_selection_over_leading_empty_block(tc, cursor):
    if not tc.gotoPreviousParagraph(False):
        return
    if not _is_current_paragraph_empty(tc):
        tc.gotoNextParagraph(False)
        return
    _move_to_empty_block_start(tc)
    cursor.gotoRange(tc.getStart(), True)
    tc.gotoNextParagraph(False)


def _post_adjust_paragraph_text_object(tc, cursor, is_around, started_empty):
    if started_empty:
        if not _is_current_paragraph_empty(tc):
            _extend_selection_after_empty_block(tc, cursor)
        if not is_around:
            return True
        if not _is_current_paragraph_empty(tc):
            _extend_selection_to_paragraph_end(tc, cursor)
        return True

    if not is_around:
        return True

    if _is_current_paragraph_empty(tc):
        _extend_selection_over_trailing_empty_block(tc, cursor)
    return True


def _select_ap_units_forward_visual(count: int, cursor, started_empty: bool) -> bool:
    steps = max(1, int(count))
    moved = False
    for i in range(steps):
        if not _paragraphs_forward(True, 1, cursor):
            break
        moved = True
        tc = _get_text_cursor()
        if tc is None:
            return False
        if i == 0 and started_empty:
            _post_adjust_paragraph_text_object(tc, cursor, True, True)
        elif _is_current_paragraph_empty(tc):
            _extend_selection_over_trailing_empty_block(tc, cursor)
    return moved


def _select_ap_units_backward_visual(count: int, cursor) -> bool:
    steps = max(1, int(count))
    moved = False
    for _ in range(steps):
        if not _paragraphs_backward(True, 1, cursor):
            break
        moved = True
        tc = _get_text_cursor()
        if tc is None:
            return False
        _extend_selection_over_leading_empty_block(tc, cursor)
    return moved


def _paragraphs_forward(expand: bool, count: int, cursor) -> bool:
    """Motion to for [count] paragraphs forward. Command '}'.

    From a non-empty paragraph, moves to the start of the next paragraph
    (which may itself be an empty separator line). From an empty separator
    line, jumps past all consecutive empty lines to the first non-empty
    paragraph start.
    """
    tc = _get_text_cursor()
    if tc is None:
        return False
    try:
        if expand:
            caret = _get_visual_caret_range(tc)
            tc.gotoRange(caret, False)
        steps = max(1, int(count))
        moved_any = False
        for _ in range(steps):
            if _is_current_paragraph_empty(tc):
                moved = _to_next_non_empty_paragraph(tc, expand)
            else:
                moved = bool(tc.gotoNextParagraph(expand))
            if not moved:
                # Last paragraph with no following empty line: move to end of it.
                if not tc.isEndOfParagraph():
                    tc.gotoEndOfParagraph(expand)
                    _sync_view_cursor(tc, expand, cursor)
                    moved_any = True
                break
            moved_any = True
        if moved_any:
            _sync_view_cursor(tc, expand, cursor)
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
    tc = _get_text_cursor()
    if tc is None:
        return False
    try:
        if expand:
            caret = _get_visual_caret_range(tc)
            tc.gotoRange(caret, False)
        steps = max(1, int(count))
        moved_any = False
        for _ in range(steps):
            if not tc.isStartOfParagraph():
                tc.gotoStartOfParagraph(expand)
                moved = True
            elif _is_current_paragraph_empty(tc):
                moved = _to_previous_non_empty_paragraph(tc, expand)
            else:
                moved = bool(tc.gotoPreviousParagraph(expand))
            if not moved:
                break
            moved_any = True
        if moved_any:
            _sync_view_cursor(tc, expand, cursor, backward=True)
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
    tc = _get_text_cursor()
    if tc is None:
        return False
    is_around = key.pending is not None and key.pending[-1] == "a"

    started_empty = _is_current_paragraph_empty(tc)
    if not _normalize_paragraph_text_object_start(tc, cursor, started_empty):
        return False

    if mode.startswith("visual"):
        tc = _get_text_cursor()
        if tc:
            _set_visual_anchor(tc.getStart())

    moved = _paragraphs_forward(True, count, cursor)
    if not moved:
        return False

    tc = _get_text_cursor()
    if tc is None:
        return False
    return _post_adjust_paragraph_text_object(tc, cursor, is_around, started_empty)


def _expand_with_paragraph_objects(count: int, key: KeyEvent, mode: Mode, cursor) -> bool:
    """Extend an existing visual selection by ip/ap paragraph text objects.

    Called when cursor already has a selection Determines direction from the
    saved anchor vs caret position, then delegates to the appropriate
    forward/backward helper.
    """
    tc = _get_text_cursor()
    if tc is None:
        return False
    is_around = key.pending is not None and key.pending[-1] == "a"
    caret = _get_visual_caret_range(tc)

    select_forward = _is_forward_selection(cursor)

    if len(cursor.getString()) == 0:
        _select_paragraph_text_objects(count, key, mode, cursor)
        return True

    tc.gotoRange(caret, False)
    started_empty = _is_current_paragraph_empty(tc)
    if started_empty:
        _move_to_empty_block_start(tc)
        cursor.gotoRange(tc.getStart(), True)

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

    tc = _get_text_cursor()
    if tc is None:
        return False
    return _post_adjust_paragraph_text_object(tc, cursor, is_around, started_empty)


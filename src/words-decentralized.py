from __future__ import annotations  # flake8: noqa

from core import (
    Mode,
    _execute_dispatch,
    _get_text_cursor,
    _get_visual_anchor,
    _handle_exc,
    _paragraph_scan_steps,
    _set_visual_anchor,
    _get_visual_caret_range
)

from paragraphs import (
    _to_next_non_empty_paragraph,
)

from utils import (   # type: ignore[reportMissingImports]
    _clone_text_range,
    _is_at_first_non_whitespace_after_leading_ws,
    _is_cursor_at_whitespace,
    _is_forward_selection,
    _sync_view_cursor,
    _is_current_paragraph_empty,
    _set_visual_selection,
)

from words import (
    _current_paragraph_text_and_offset
)

# ------------------------
# Word motions (non-WORDs)
# ------------------------

def _to_start_of_words(expand: bool, count: int, mode: Mode, cursor, previous: bool) -> bool:
    """To start of previous or next [count] words. Commands 'w' and 'b'."""
    tc = _get_text_cursor()
    if tc is None:
        return False
    try:
        if mode == "pending":
            _set_visual_anchor(tc.getStart())
        anchor = _get_visual_anchor() if expand else None
        # Dispatch commands move view cursor directly but if text cursor has been
        # moved it needs to be synced for last.
        sync_cursor = False

        if previous:
            for _ in range(count):
                sync_cursor, new_anchor = _to_start_of_previous_word(
                    expand, mode, cursor, tc
                )
                if new_anchor is not None:
                    anchor = new_anchor
                if not sync_cursor:
                    # Dispatcher moved the view cursor; refresh tc so the next
                    # iteration sees the updated position.
                    tc = _get_text_cursor()

        else:
            for _ in range(count):
                if not _next_word_edge_case(expand, tc):
                    _execute_dispatch(".uno:GoToNextWord")
                    tc = _get_text_cursor()
                    sync_cursor = False
                else:
                    sync_cursor = True

        # When the edge case moved `tc` directly, sync the view cursor since
        # the dispatcher was not used (it only updates the view cursor).
        if sync_cursor:
            backward_selection = not _is_forward_selection(tc)
            _sync_view_cursor(tc, expand, cursor, backward_selection)
        elif expand and anchor:
            tc = _get_text_cursor()
            if tc:
                _set_visual_selection(cursor, anchor, tc.getStart())
        return True

    except Exception as e:
        _handle_exc(err=e)
        return False


def _to_start_of_previous_word(expand: bool, mode: Mode, cursor, tc):
    """Returns (sync_cursor, new_anchor). new_anchor is set when pending mode
    repositions the anchor after moving 1 left across an empty paragraph.
    """
    sync_cursor = False
    new_anchor = None

    if _is_current_paragraph_empty(tc):
        # Do operator command for paragraph next to empty line is without
        # including the empty line.
        probe = _clone_text_range(tc)
        probe.goLeft(1, False)
        if not _is_current_paragraph_empty(probe):
            if mode == "pending":
                cursor.collapseToStart()
                cursor.goLeft(1, False)
                new_anchor = _get_text_cursor()
                _set_visual_anchor(new_anchor)
            else:
                # Move to paragraph next to empty line.
                cursor.goLeft(1, expand)
            _execute_dispatch(".uno:GoToPrevWord")
        else:
            tc.goLeft(1, expand)
            sync_cursor = True

    else:
        if not _previous_word_edge_case(expand, tc):
            _execute_dispatch(".uno:GoToPrevWord")
        else:
            sync_cursor = True

    return sync_cursor, new_anchor


def _next_word_edge_case(expand, tc):
    """Handle some edge cases for 'w' and 'W' motions. Return True if handled edge case.
    """
    if _is_current_paragraph_empty(tc) or tc.isEndOfParagraph():
        tc.gotoNextParagraph(expand)
    else:
        return False
    return True


def _previous_word_edge_case(expand, tc):
    """Handle some common edge cases for 'b' and 'B' motions. Return True
    if handled edge case.
    """
    if tc.isStartOfParagraph():
        tc.goLeft(1, expand)
    elif _is_cursor_at_whitespace(tc, condition="before_paragraph") or \
    _is_at_first_non_whitespace_after_leading_ws(tc):
        # Moves cursor to previous line.
        tc.gotoStartOfParagraph(expand)
        tc.goLeft(1, expand)
    else:
        return False
    return True

# This is comment.
def _to_end_of_words(expand: bool, count: int, mode: Mode, cursor, backward: bool = False) -> bool:
    """Motion forward to end of [count] words. Command 'e'."""
    tc = _get_text_cursor()
    if not tc:
        return False
    try:
        if mode == "pending":
            _set_visual_anchor(cursor.getStart())
        moved_any = False
        if backward:
            for _ in range(count):
                if expand:
                    _update_cursor(tc)
                if not _to_end_of_previous_word(expand, cursor, tc):
                    break
                moved_any = True
        else:
            for _ in range(count):
                if not _to_next_word_end(expand, cursor, tc):
                    break
                moved_any = True

        if not moved_any:
            return False

        backward_selection = not _is_forward_selection(tc)
        _sync_view_cursor(tc, expand, cursor, backward_selection)
        return True

    except Exception as e:
        _handle_exc(err=e)
        return False


# Text cursor's isEndOfWord() doesn't match with Vi/Vim or with dispatch
# command used with to start of words motion so this parser is used instead.
# Correct placement for cursor is calculated as offset from start of paragraph.
def _to_next_word_end(expand: bool, cursor, tc) -> bool:
    """Motion forward to next end of word. For command 'e'."""
    if expand:
        _update_cursor(tc)
    paragraph_text, offset = _current_paragraph_text_and_offset(tc)
    view_offset = None
    if not expand:
        try:
            view_tc = _clone_text_range(cursor)
            _, view_offset = _current_paragraph_text_and_offset(view_tc)
        except Exception as e:
            _handle_exc(err=e)

    if expand and 0 < offset < len(paragraph_text):
        # In Visual mode, avoid skipping a single punctuation unit after a word.
        if _word_unit_class(paragraph_text[offset]) == "punct" \
                and _word_unit_class(paragraph_text[offset - 1]) == "alnum":
            offset -= 1

    if not expand and 0 < offset < len(paragraph_text):
        # Block caret sits on the previous char, but the text cursor
        # can be positioned between word and punctuation.
        if _word_unit_class(paragraph_text[offset]) == "punct" \
                and _word_unit_class(paragraph_text[offset - 1]) == "alnum" \
                and view_offset == offset - 1:
            offset -= 1

    allow_last = True
    if expand and paragraph_text and offset >= len(paragraph_text):
        # In Visual mode, allow selecting a trailing word unit (e.g. ".")
        # before jumping to the next paragraph.
        offset = len(paragraph_text) - 1
        allow_last = False

    next_offset = _scan_forward_word_unit_end(paragraph_text, offset, allow_last)

    # Skip empty lines.
    if next_offset is None:
        if not _to_next_non_empty_paragraph(tc, False, False):
            return False
        paragraph_text, offset = _current_paragraph_text_and_offset(tc)
        next_offset = _scan_forward_word_unit_end(paragraph_text, offset)
        if next_offset is None:
            return False

    if not expand and next_offset == offset:
        next_offset = min(len(paragraph_text), next_offset + 1)

    # Set cursor to new position.
    tc.gotoStartOfParagraph(False)
    move = next_offset + 1 if expand else next_offset
    if move > 0:
        tc.goRight(move, False)
        return True
    return False


def _word_unit_class(ch: str) -> str:
    if ch.isspace():
        return "blank"
    if ch.isalnum():
        return "alnum"
    return "punct"


# Scan to the end index of the next non-blank word unit. Used by 'e' motion.
def _scan_forward_word_unit_end(paragraph_text: str, offset: int, allow_last: bool = True) \
                                -> int | None:
    length = len(paragraph_text)
    if offset >= length:
        return None

    i = offset
    # If on a word unit already, advance one to ensure repeated 'e' progresses,
    # unless we are already at the last character in the paragraph and allowed.
    if _word_unit_class(paragraph_text[i]) != "blank":
        if i == length - 1:
            return i if allow_last else None
        i += 1
    while i < length and _word_unit_class(paragraph_text[i]) == "blank":
        i += 1
    if i >= length:
        return None

    cls = _word_unit_class(paragraph_text[i])
    while i + 1 < length and _word_unit_class(paragraph_text[i + 1]) == cls:
        i += 1
    return i


def _go_to_previous_paragraph_end(tc) -> bool:
    """Move to the end of the previous paragraph."""
    if not tc.gotoPreviousParagraph(False):
        return False
    tc.gotoEndOfParagraph(False)
    return True


def _to_end_of_previous_word(expand: bool, cursor, tc) -> bool:
    """Motion backward to previous end of a word. Empty line is considered a word.
    For command 'ge'."""
    if _is_current_paragraph_empty(tc):
        return _go_to_previous_paragraph_end(tc)

    paragraph_text, offset = _current_paragraph_text_and_offset(tc)
    prev_end = _scan_backward_word_unit_end(paragraph_text, offset)
    if prev_end is None:
        return _go_to_previous_paragraph_end(tc)

    tc.gotoStartOfParagraph(False)
    if prev_end > 0:
        tc.goRight(prev_end, False)
    if expand:
        tc.goRight(1, False)
    return True


# ---------------
# WORD motions
# ---------------

# A WORD consists of a sequence of non-blank characters, separated with white
# space. An empty line is also considered to be a WORD.
def _to_start_of_WORDs(expand: bool, count: int, cursor, direction: str) -> bool:
    """Motion to start of [count] WORDs forward or backward. Commands 'W' and 'B'.
    """
    tc = _get_text_cursor()
    if tc is None:
        return False
    try:
        if direction == "forward":
            for _ in range(count):
                if expand:
                    _update_cursor(tc)
                can_move = _to_end_of_WORD(tc, expand, before_end=False)
                if not can_move:
                    break
                can_move = _to_end_of_whitespace(tc)
                if not can_move:
                    break

        elif direction == "backward":
            for _ in range(count):
                if expand:
                    _update_cursor(tc)
                can_move = _to_start_of_whitespace(tc)
                if not can_move:
                    break
                can_move = _to_start_of_WORD(tc)
                if not can_move:
                    break
        else:
            return False

        backward_selection = not _is_forward_selection(tc)
        _sync_view_cursor(tc, expand, cursor, backward_selection)
        return True

    except Exception as e:
        _handle_exc(err=e)
        return False


def _to_start_of_WORD(tc) -> bool:
    for _ in _paragraph_scan_steps():
        probe = _clone_text_range(tc)
        if not probe.goLeft(1, True):
            return False
        if probe.getString().isspace():
            break
        if not tc.goLeft(1, False):
            return False
    return True


def _to_end_of_WORD(tc, expand, before_end: bool = False) -> bool:
    """before_end : Stop cursor 1 char before word end in Normal mode.
    Returns if cursor can move or not. For command 'W' and 'E'.
    """
    for _ in _paragraph_scan_steps():
        probe = _clone_text_range(tc)
        if not probe.goRight(1, True):
            return False
        if probe.getString().isspace():
            if before_end and not expand:
                tc.goLeft(1, False)
            break
        if not tc.goRight(1, False):
            return False
    return True


def _to_start_of_whitespace(tc) -> bool:
    """Move to start of whitespace in current paragraph. Returns if cursor can
    move or not. For command 'B' and 'gE'.
    """
    for _ in _paragraph_scan_steps():
        probe = _clone_text_range(tc)
        if not probe.goLeft(1, True):
            return False
        if not probe.getString().isspace():
            break
        if not tc.goLeft(1, False):
            return False
        if _is_current_paragraph_empty(tc):
            break
    return True


def _to_end_of_whitespace(tc) -> bool:
    """Move to end of whitespace in current paragraph. Returns if cursor can
    move or not. For command 'W' and 'E'.
    """
    for _ in _paragraph_scan_steps():
        probe = _clone_text_range(tc)
        if not probe.goRight(1, True):
            return False
        if not probe.getString().isspace():
            break
        if not tc.goRight(1, False):
            return False
        if _is_current_paragraph_empty(tc):
            break
    return True


def _update_cursor(tc):
    caret = _get_visual_caret_range(tc)
    if caret is not None:
        tc.gotoRange(caret, False)


def _to_end_of_WORDs_forward(expand: bool, count: int, cursor) -> bool:
    """Motion to end of [count] WORDs forward. Command 'E'."""
    tc = _get_text_cursor()
    if tc is None:
        return False
    try:
        for _ in range(count):
            if expand:
                _update_cursor(tc)

            # When Normal mode block cursor is at end of word or paragraph,
            # it needs a little push.
            if not expand:
                probe = _clone_text_range(tc)
                probe.goRight(2, True)
                string = probe.getString()
                if len(string) >= 1 and string[-1].isspace():
                    tc.goRight(1, False)

            # On edge of paragraph give cursor another push.
            if tc.isEndOfParagraph():
                tc.goRight(1, False)
                if not expand:
                    tc.goRight(1, False)

            # Skip empty lines.
            caret = _get_visual_caret_range(tc)
            caret_tc = _clone_text_range(caret)
            if _is_current_paragraph_empty(caret_tc):
                _to_next_non_empty_paragraph(tc, False, cross_empty=False)

            can_move = _to_end_of_whitespace(tc)
            if not can_move:
                break
            else:
                can_move = _to_end_of_WORD(tc, expand, before_end=True)
                if not can_move:
                    break

        probe = _clone_text_range(tc)
        backward_selection = not _is_forward_selection(probe)
        _sync_view_cursor(tc, expand, cursor, backward_selection)
        return True

    except Exception as e:
        _handle_exc(err=e)
        return False


def _scan_backward_word_unit_end(paragraph_text: str, offset: int) -> int | None:
    """Return the end index of the previous word unit before offset."""
    length = len(paragraph_text)
    if length == 0 or offset <= 0:
        return None

    i = min(offset - 1, length - 1)

    # If the caret is inside a word unit, first walk back to the boundary.
    if not paragraph_text[i].isspace():
        while i >= 0 and not paragraph_text[i].isspace():
            i -= 1

    # Then skip whitespace and land on the last character of the previous word.
    while i >= 0 and paragraph_text[i].isspace():
        i -= 1

    return i if i >= 0 else None


def _to_end_of_WORDs_backward(expand: bool, count: int, cursor) -> bool:
    """Motion to end of [count] WORDs backward. Command 'gE'."""
    tc = _get_text_cursor()
    if tc is None:
        return False

    try:
        steps = max(1, int(count))
        moved_any = False

        for _ in range(steps):
            if expand:
                _update_cursor(tc)

            if _is_current_paragraph_empty(tc):
                if not _go_to_previous_paragraph_end(tc):
                    break
                moved_any = True
                continue

            paragraph_text, offset = _current_paragraph_text_and_offset(tc)
            prev_end = _scan_backward_word_unit_end(paragraph_text, offset)
            if prev_end is None:
                if not _go_to_previous_paragraph_end(tc):
                    break
                moved_any = True
                continue

            tc.gotoStartOfParagraph(False)
            if prev_end > 0:
                tc.goRight(prev_end, False)
            if expand:
                tc.goRight(1, False)
            moved_any = True

        if not moved_any:
            return False

        probe = _clone_text_range(tc)
        backward_selection = not _is_forward_selection(probe)
        _sync_view_cursor(tc, expand, cursor, backward_selection)
        return True

    except Exception as e:
        _handle_exc(err=e)
        return False



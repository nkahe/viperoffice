from __future__ import annotations  # flake8: noqa
from typing import Any

from core import (
    KeyEvent,
    Mode,
    ISWORD,
    _dbg,
    _get_controller,
    _get_cursor,
    _get_dispatcher,
    _get_frame,
    _get_text_cursor,
    _get_visual_anchor,
    _handle_exc,
    _set_visual_anchor,
    _get_visual_caret_range
)

from utils import (   # type: ignore[reportMissingImports]
    _clone_text_range,
    _is_at_first_non_whitespace_after_leading_ws,
    _is_forward_selection,
    _is_cursor_at_whitespace,
    _range_after_paragraph_break,
    _set_visual_selection,
    _sync_view_cursor_to_text_cursor,
    _is_current_paragraph_empty
)

from paragraphs import (
    _to_previous_non_empty_paragraph,
    _to_next_non_empty_paragraph,
)

from word_specs import (  # type: ignore[reportMissingImports]
    _WORD_OBJECT_UNIT_FORWARD,
    _WORD_OBJECT_UNIT_BACKWARD,
    _WORD_OBJECT_UNIT_BIG_FORWARD,
    _WORD_OBJECT_UNIT_BIG_BACKWARD
)

FORWARD = "forward"
BACKWARD = "backward"
START = "start"
END = "end"

# ------------------
# Word motions
# ------------------

def _to_start_of_word(expand: bool, count: int, mode: Mode, cursor, previous: bool ) -> bool:
    """To start of previous or next words. 'w' and 'b'."""
    dispatcher = _get_dispatcher()
    frame = _get_frame()
    tc = _get_text_cursor()
    if dispatcher is None or frame is None or tc is None:
        return False
    try:
        if mode == "pending":
            _set_visual_anchor(tc.getStart())
        anchor = _get_visual_anchor() if expand else None

        used_edge_case = False
        for _ in range(count):
            if previous:
                if not _previous_word_edge_case(expand, tc):
                    dispatcher.executeDispatch(frame, ".uno:GoToPrevWord", "", 0, ())
                else:
                    used_edge_case = True
            else:
                if not _next_word_edge_case(expand, tc):
                    dispatcher.executeDispatch(frame, ".uno:GoToNextWord", "", 0, ())
                else:
                    used_edge_case = True

        # When the edge case moved `tc` directly, sync the view cursor since
        # the dispatcher was not used (it only updates the view cursor).
        if used_edge_case:
            backward_selection = not _is_forward_selection(tc)
            _sync_view_cursor_to_text_cursor(tc, expand, cursor, backward_selection)
        elif expand and anchor is not None:
            tc = _get_text_cursor()
            if tc is not None:
                _set_visual_selection(cursor, anchor, tc.getStart())
        return True

    except Exception as e:
        _handle_exc(err=e)
        return False


def _next_word_edge_case(expand, tc):
    if _is_current_paragraph_empty(tc) or tc.isEndOfParagraph():
        tc.gotoNextParagraph(expand)
        return True
    else:
        return False


def _previous_word_edge_case(expand, tc):
    if _is_current_paragraph_empty(tc) or tc.isStartOfParagraph():
        tc.goLeft(1, expand)
        return True
    elif tc.isStartOfParagraph():
        tc.gotoPreviousParagraph(expand)
        tc.goLeft(1, expand)
        return True
    elif _is_cursor_at_whitespace(tc, condition="before_paragraph") or \
    _is_at_first_non_whitespace_after_leading_ws(tc):
        # Moves cursor to previous line.
        tc.gotoStartOfParagraph(expand)
        tc.goLeft(1, expand)
        return True
    else:
        return False


def _to_start_of_next_WORD(expand: bool, count: int, mode: Mode, cursor, key) -> bool:
    """Command 'W'."""
    tc = _get_text_cursor()
    if tc is None:
        return False
    try:
        for _ in range(count):
            # Count empty lines as WORDS.
            if tc.isEndOfParagraph() or _is_current_paragraph_empty(tc):
                tc.goRight(1, expand)
            else:
                tc.gotoNextWord(expand)   # type: ignore[reportMissingImports]

        backward_selection = not _is_forward_selection(tc)
        _sync_view_cursor_to_text_cursor(tc, expand, cursor, backward_selection)
        return True

    except Exception as e:
        _handle_exc(err=e)
        return False


def _to_start_of_previous_WORD(expand: bool, count: int, mode: Mode, cursor) -> bool:
    """Command 'B'."""
    tc = _get_text_cursor()
    if tc is None:
        return False
    try:
        for _ in range(count):
            # Count empty lines as WORDS.
            if not _previous_word_edge_case(expand, tc):
                tc.gotoPreviousWord(expand)   # type: ignore[reportMissingImports]

        backward_selection = not _is_forward_selection(tc)
        _sync_view_cursor_to_text_cursor(tc, expand, cursor, backward_selection)
        return True

    except Exception as e:
        _handle_exc(err=e)
        return False


# Based on Commit b56b17e from jmagers/vibreoffice
def _to_end_of_next_word(expand: bool, count: int, mode: Mode, cursor) -> bool:
    """Motion to end of current or next [count] words. Command 'e'."""
    tc = _get_text_cursor()
    if not tc:
        return False
    try:
        if mode == "pending":
            _set_visual_anchor(tc.getStart())
        anchor = _get_visual_anchor() if expand else None

        for _ in range(count):
            # Move cursor to right by two in case cursor is already at vim's
            # definition of endOfWord.
            tc.goRight(2, expand)

            cursor.gotoRange(tc.getEnd(), False)
            cursor.goLeft(1, True)
            if cursor.getString() == ".":
                tc.goRight(1, expand)

            # gotoEndOfWord gets stuck sometimes so manually moving the cursor
            # right is necessary in these cases.
            while not tc.gotoEndOfWord(expand):   # type: ignore[reportMissingImports]
                if not tc.goRight(1, expand):
                    break

            if tc.isEndOfWord():
                # LibreOffice defines a "." directly following a word to be the
                # endOfWord and vim does not. So in this case we need to move the
                # the cursor to the left.
                cursor.gotoRange(tc.getEnd(), False)
                cursor.goLeft(1, True)
                if cursor.getString() == ".":
                    tc.goLeft(1, expand)

        # gotoEndOfWord moves the cursor one character further than vim
        # does so move it back one if end of word is reached and not
        # expanding selection. Skip adjustment if at end of document.
        _dbg(f"e: before final adj tc='{tc.getString()}' collapsed={tc.isCollapsed()}")
        if not expand:
            if tc.goRight(1, False):
                tc.goLeft(2, expand)
        _dbg(f"e: final tc='{tc.getString()}' collapsed={tc.isCollapsed()}")

        if expand and anchor is not None:
            _set_visual_selection(cursor, anchor, tc.getStart())

        backward_selection = not _is_forward_selection(tc)
        _sync_view_cursor_to_text_cursor(tc, expand, cursor, backward_selection)
        return True

    except Exception as e:
        _handle_exc(err=e)
        return False



def _is_keyword_char(ch: str) -> bool:
    if ch.isalpha():
        return True
    if ISWORD.get("digits") and ch.isdigit():
        return True
    return ch in str(ISWORD.get("chars", ""))


def _word_char_class(ch, big_word: bool = False):
    if ch == " " or ch == "\t" or ch == "\n":
        return "blank"
    if big_word:
        return "other"
    if _is_keyword_char(ch):
        return "keyword"
    return "other"


def _word_motion(spec, expand: bool, count: int, mode: Mode) -> bool:
    """Run a word-motion command (e.g. 'w') and optionally apply an operator.

    Args:
        spec: Word motion specification (direction/target/big_word/etc.).
        expand: If True, keeps selection expanded while moving.
        count: Number of word motions to perform (minimum 1).
        mode: Current Vi input mode.

    Returns:
        True if cursor moved at least once, otherwise False.
    """
    try:
        if not _validate_word_motion_spec(spec):
            return False
        if expand:
            if mode == "pending":
                # Operator: query collapsed so start/end ranges are accurate.
                result = _query_word_motion(spec, count, expand=False)
                if not result.get("moved", False):
                    return False
                return _apply_motion_result(result, expand)
            # Visual mode: step-by-step so selection updates incrementally.
            steps = max(1, int(count))
            moved_any = False
            for _ in range(steps):
                result = _query_word_motion(spec, 1, expand=True)
                if not result.get("moved", False):
                    break
                if not _apply_motion_result(result, expand):
                    break
                moved_any = True
            return moved_any

        result = _query_word_motion(spec, count, expand=False)
        if not result.get("moved", False):
            return False

        return _apply_motion_result(result, expand)
    except Exception as e:
        _handle_exc(err=e)
        return False


def _query_word_motion(spec, count:int, expand:bool=False):
    text_cursor = _get_text_cursor()
    cursor = _get_cursor()
    if text_cursor is None or cursor is None or not _validate_word_motion_spec(spec):
        return _normalize_motion_range({"moved": False})

    steps = max(1, int(count))
    start_range = _clone_text_range(text_cursor)
    moved_any = False

    for _ in range(steps):
        if expand:
            # Visual mode: scan from the caret (active/moving) end of the selection.
            # _get_visual_caret_range uses the saved anchor to determine which end
            # is fixed and which is the caret, regardless of selection direction.
            caret = _get_visual_caret_range(text_cursor)
            text_cursor.gotoRange(caret, False)
            if spec.get("direction") == BACKWARD:
                # The caret range's right edge is C+1 chars from para start.
                # Backward scans use offset = C (i = offset-1), so step left 1.
                text_cursor.goLeft(1, False)
        if not _word_motion_once(text_cursor, expand, spec):
            break
        moved_any = True

    # end_range = _clone_text_range(text_cursor)
    end_range = text_cursor.getEnd() if expand else _clone_text_range(text_cursor)

    result = {
        "moved": moved_any,
        "start_range": start_range,
        "end_range": end_range,
        "inclusive": bool(spec.get("inclusive", False)),
    }
    return _normalize_motion_range(result)


def _validate_word_motion_spec(spec) -> bool:
    if not isinstance(spec, dict):
        return False
    required = ("direction", "target", "big_word", "cross_empty", "inclusive")
    for key in required:
        if key not in spec:
            return False
    if spec["direction"] not in (FORWARD, BACKWARD):
        return False
    if spec["target"] not in (START, END):
        return False
    return True


def _normalize_motion_range(result, for_operator:bool = False):
    if not isinstance(result, dict):
        return {"moved": False}
    normalized = dict(result)
    normalized["operator_inclusive"] = bool(for_operator and normalized.get("inclusive", False))
    normalized["operator_exclusive"] = bool(for_operator and not normalized.get("inclusive", False))
    return normalized


def _word_motion_once(text_cursor, expand: bool, spec) -> bool:
    """Execute one step for a configured word motion.

    Args:
        text_cursor: Model text cursor used for paragraph and offset operations.
        expand: If True, keeps selection expanded while moving.
        spec: Motion config (direction/target/big_word/empty-line policy).

    Returns:
        True if cursor advanced, otherwise False.
    """
    direction = spec.get("direction", FORWARD)
    if direction == FORWARD:
        return _word_motion_once_forward(text_cursor, expand, spec)
    if direction == BACKWARD:
        return _word_motion_once_backward(text_cursor, expand, spec)
    return False


def _word_motion_once_forward(text_cursor, expand: bool, spec) -> bool:
    cross_empty = bool(spec.get("cross_empty", True))
    paragraph_text, offset = _current_paragraph_text_and_offset(text_cursor)
    length = len(paragraph_text)

    if length == 0:
        return _to_next_non_empty_paragraph(text_cursor, expand, cross_empty)

    if offset >= length:
        if _to_next_non_empty_paragraph(text_cursor, expand, cross_empty):
            return True
        # No next paragraph (EOF): move to end of current paragraph.
        if not text_cursor.isEndOfParagraph():
            text_cursor.gotoEndOfParagraph(expand)
            return True
        return False

    next_offset = _scan_forward_word_target(paragraph_text, offset, spec)
    if next_offset is None or next_offset >= length:
        if _to_next_non_empty_paragraph(text_cursor, expand, cross_empty):
            return True
        # No next paragraph (EOF): move to end of current paragraph.
        if not text_cursor.isEndOfParagraph():
            text_cursor.gotoEndOfParagraph(expand)
            return True
        return False

    text_cursor.gotoStartOfParagraph(False)
    move = next_offset
    # In expand mode, include the character at next_offset in the selection:
    # - e/E (target=END): include the last char of the word.
    # - w/W (target=START): include the first char of the next word.
    if expand and spec.get("target") in (END, START):
        move = next_offset + 1
    if move > 0:
        text_cursor.goRight(move, expand)
    return True


def _word_motion_once_backward(text_cursor, expand: bool, spec) -> bool:
    cross_empty = bool(spec.get("cross_empty", True))
    paragraph_text, offset = _current_paragraph_text_and_offset(text_cursor)
    length = len(paragraph_text)

    if length == 0 or offset <= 0:
        if not _to_previous_non_empty_paragraph(text_cursor, expand, cross_empty):
            return False
        paragraph_text, offset = _current_paragraph_text_and_offset(text_cursor)
        length = len(paragraph_text)

        # Empty paragraph is a valid word-step stop when crossing is allowed.
        if length == 0:
            return True
        offset = length

    prev_offset = _scan_backward_word_target(paragraph_text, offset, spec)
    if prev_offset is not None and 0 <= prev_offset < length:
        text_cursor.gotoStartOfParagraph(False)
        if prev_offset > 0:
            text_cursor.goRight(prev_offset, expand)
        return True

    if not _to_previous_non_empty_paragraph(text_cursor, expand, cross_empty):
        return False
    paragraph_text, offset = _current_paragraph_text_and_offset(text_cursor)
    length = len(paragraph_text)
    if length == 0:
        return True
    prev_offset = _scan_backward_word_target(paragraph_text, length, spec)
    if prev_offset is None:
        return False
    text_cursor.gotoStartOfParagraph(False)
    if prev_offset > 0:
        text_cursor.goRight(prev_offset, expand)
    return True


def _scan_forward_word_target(paragraph_text, offset, spec):
    length = len(paragraph_text)
    if offset >= length:
        return None

    classify = _word_char_class
    big_word = bool(spec.get("big_word", False))
    target = spec.get("target", START)
    unit_mode = bool(spec.get("unit_mode", False))
    i = offset

    if unit_mode and target == END:
        bounds = _word_unit_bounds_core(paragraph_text, i, False, big_word)
        if bounds is None:
            return None
        _, end_excl = bounds
        return end_excl - 1

    if target == START:
        cls = classify(paragraph_text[i], big_word)
        if cls == "blank":
            while i < length and classify(paragraph_text[i], big_word) == "blank":
                i += 1
            return i
        while i < length and classify(paragraph_text[i], big_word) == cls:
            i += 1
        while i < length and classify(paragraph_text[i], big_word) == "blank":
            i += 1
        return i

    if target == END:
        # For "e": if on non-blank, advance once so repeated `e` progresses.
        if classify(paragraph_text[i], big_word) != "blank":
            i += 1
        # Then skip blanks and land on last char of the next word.
        while i < length and classify(paragraph_text[i], big_word) == "blank":
            i += 1
        if i >= length:
            return None
        cls = classify(paragraph_text[i], big_word)
        while i + 1 < length and classify(paragraph_text[i + 1], big_word) == cls:
            i += 1
        return i

    return None


def _scan_backward_word_target(paragraph_text, offset, spec):
    length = len(paragraph_text)
    if length == 0 or offset <= 0:
        return None

    classify = _word_char_class
    big_word = bool(spec.get("big_word", False))
    target = spec.get("target", START)
    unit_mode = bool(spec.get("unit_mode", False))
    i = min(offset - 1, length - 1)

    if unit_mode and target == START:
        bounds = _word_unit_bounds_backward(paragraph_text, offset, big_word)
        if bounds is None:
            return None
        start, _ = bounds
        return start

    if target == END:
        # ge/gE: skip current word chars backward, then skip blanks,
        # landing on the last char of the previous word.
        cls = classify(paragraph_text[i], big_word)
        if cls != "blank":
            while i >= 0 and classify(paragraph_text[i], big_word) == cls:
                i -= 1
        while i >= 0 and classify(paragraph_text[i], big_word) == "blank":
            i -= 1
        return i if i >= 0 else None

    # Skip trailing blanks when scanning backward.
    while i >= 0 and classify(paragraph_text[i], big_word) == "blank":
        i -= 1
    if i < 0:
        return None

    if target == START:
        cls = classify(paragraph_text[i], big_word)
        while i - 1 >= 0 and classify(paragraph_text[i - 1], big_word) == cls:
            i -= 1
        return i

    return None


# Offset means cursor position relative to start of paragraph.
def _current_paragraph_text_and_offset(text_cursor):
    text_obj = text_cursor.getText()
    para = text_obj.createTextCursorByRange(text_cursor.getStart())
    para.gotoStartOfParagraph(False)
    para.gotoEndOfParagraph(True)
    paragraph_text = para.getString()

    offset_cursor = text_obj.createTextCursorByRange(text_cursor.getStart())
    offset_cursor.gotoStartOfParagraph(True)
    offset = len(offset_cursor.getString())
    return paragraph_text, offset


def _apply_motion_result(result, expand:bool) -> bool:
    if not isinstance(result, dict) or not result.get("moved", False):
        return False
    end_range = result.get("end_range")
    cursor = _get_cursor()
    if cursor is None or end_range is None:
        return False
    try:
        if not expand:
            moved = cursor.gotoRange(end_range, False)
            return True if moved else False

        anchor = _get_visual_anchor()
        if anchor is not None:
            # Visual mode: use _set_visual_selection so direction changes
            # (crossing the anchor) work correctly in both directions.
            _set_visual_selection(cursor, anchor, end_range)
            return True

        # Pending mode: collapse to start_range (P) first so the selection
        # starts at the block cursor char, not at P+1 (the anchor side).
        start = result.get("start_range")
        if start is not None:
            cursor.gotoRange(start, False)
        cursor.gotoRange(end_range, True)
        if result.get("inclusive"):
            # Pending mode with an inclusive motion (e.g. e/E/ge/gE):
            # _query_word_motion ran with expand=False so the +1 inclusive offset
            # in _word_motion_once_forward never fired. Extend one more char to
            # include the last character of the word.
            cursor.goRight(1, True)
    except Exception as e:
        _handle_exc(err=e)
        return False
    return True


# ------------------
# Word text-objects
# ------------------

def _select_word_objects_forward(count: int, key: KeyEvent, mode: Mode, cursor) -> bool:
    """Select [count] words forward starting from beginning of current word unit.
    Commands 'iw', 'iW, 'aw', 'aW', in Operator-pending mode or Visual without
    extended selection.
    """
    text_cursor = _get_text_cursor()
    if text_cursor is None:
        return False

    big_word = True if key.char == "W" else False
    is_around = True if key.pending and key.pending[-1] == "a" else False

    steps = max(1, int(count))
    if is_around:
        probe = _clone_text_range(text_cursor)
        start_range, end_range = _around_word_ranges_forward(probe, steps, big_word)
    else:
        start_range = _word_object_start_range(text_cursor, big_word)
        if start_range is None:
            return False
        end_range = _advance_word_object_caret(text_cursor, start_range, steps, \
                                               FORWARD, big_word)
    if end_range is None:
        return False
    if mode.startswith("visual"):
        _set_visual_anchor(start_range)
        _set_visual_selection(cursor, start_range, end_range)
        return True
    cursor.gotoRange(start_range, False)
    cursor.gotoRange(end_range, True)
    return True


def _word_object_start_range(text_cursor, big_word: bool = False):
    """Return range at the start of the current word/whitespace unit."""
    try:
        paragraph_text, offset = _current_paragraph_text_and_offset(text_cursor)
        if len(paragraph_text) == 0:
            return _range_at_paragraph_offset(text_cursor, 0)
        start_offset, _ = _word_unit_bounds(paragraph_text, offset, big_word)
        return _range_at_paragraph_offset(text_cursor, start_offset)
    except Exception as e:
        _handle_exc(err=e)
        return None


def _range_at_paragraph_offset(paragraph_cursor, offset: int):
    try:
        probe = _clone_text_range(paragraph_cursor)
        if not probe:
            return None
        probe.gotoStartOfParagraph(False)
        if offset > 0:
            probe.goRight(offset, False)
        return probe.getStart()
    except Exception as e:
        _handle_exc(err=e)
        return None


def _advance_word_object_caret(text_cursor, caret_range, steps: int, direction: str, \
                               big_word: bool = False):
    """Advance caret by word-object units and return the new caret range."""
    if caret_range is None or steps <= 0:
        return None
    try:
        text_obj = text_cursor.getText()
        probe = text_obj.createTextCursorByRange(caret_range)
        if direction == FORWARD:
            spec = _WORD_OBJECT_UNIT_BIG_FORWARD if big_word else _WORD_OBJECT_UNIT_FORWARD
        else:
            spec = _WORD_OBJECT_UNIT_BIG_BACKWARD if big_word else _WORD_OBJECT_UNIT_BACKWARD
        for _ in range(steps):
            caret = probe.getEnd() if direction == FORWARD else probe.getStart()
            probe.gotoRange(caret, False)
            expand = direction == FORWARD
            if not _word_motion_once(probe, expand, spec):
                return None
        return probe.getEnd() if direction == FORWARD else probe.getStart()
    except Exception as e:
        _handle_exc(err=e)
        return None


def _expand_with_word_text_objects(count, key, mode, cursor) -> bool:
    """Expand selection with [count] word text-objects 'iw' in Visual mode to
    direction of selection. Command 'iw' in Visual mode with extended selection.
    """
    text_cursor = _get_text_cursor()
    if text_cursor is None:
        return False

    is_around = True if key.pending and key.pending[-1] == "a" else False

    # Start from beginning of current word if haven't expanded selection.
    if len(cursor.getString()) == 0:
        big_word = True if key.char == "W" else False
        start_range = _word_object_start_range(text_cursor, big_word)
        if start_range is None:
            return False
        cursor.gotoRange(start_range, False)
        return _select_word_objects_forward(count, key, mode, cursor)

    big_word = True if key.char == "W" else False

    is_forward = _is_forward_selection(cursor)
    anchor = cursor.getStart() if is_forward else cursor.getEnd()
    _set_visual_anchor(anchor)
    caret = cursor.getEnd() if is_forward else cursor.getStart()
    select_backwards = not is_forward

    if caret is None or anchor is None:
        return False

    try:
        steps = max(1, int(count))
        direction = BACKWARD if select_backwards else FORWARD
        if is_around:
            if direction == BACKWARD:
                new_caret = _around_word_caret_backward(text_cursor, caret, steps, \
                                                        big_word)
            else:
                probe = text_cursor.getText().createTextCursorByRange(caret)
                _, end_range = _around_word_ranges_forward(probe, steps, big_word)
                new_caret = end_range
        else:
            new_caret = _advance_word_object_caret(text_cursor, caret, steps, \
                                                   direction, big_word)
        if new_caret is None:
            return False

        if is_around and direction == BACKWARD:
            _set_visual_selection(cursor, anchor, new_caret, force_backward=True)
        else:
            _set_visual_selection(cursor, anchor, new_caret)
        return True
    except Exception as e:
        _handle_exc(err=e)
        return False


def _around_word_caret_backward(text_cursor, caret_range, steps: int, big_word: bool = False):
    """Return new caret range by moving backward by 'aw/W' units."""
    if caret_range is None or steps <= 0:
        return None
    try:
        text_obj = text_cursor.getText()
        probe = text_obj.createTextCursorByRange(caret_range)

        for i in range(steps):
            paragraph_text, offset = _current_paragraph_text_and_offset(probe)
            if offset == 0:
                if not probe.gotoPreviousParagraph(False):
                    return None
                probe.gotoEndOfParagraph(False)
                paragraph_text, offset = _current_paragraph_text_and_offset(probe)
            scan_offset = max(0, offset - 1)
            bounds = _around_word_unit_bounds(paragraph_text, scan_offset, BACKWARD, big_word)

            if bounds is None:
                if not probe.gotoPreviousParagraph(False):
                    return None
                probe.gotoEndOfParagraph(False)
                continue

            start_offset, _ = bounds
            probe.gotoStartOfParagraph(False)
            if start_offset > 0:
                probe.goRight(start_offset, False)

            if i < steps - 1:
                if start_offset > 0:
                    probe.goLeft(1, False)
                elif not probe.gotoPreviousParagraph(False):
                    return probe.getStart()
                else:
                    probe.gotoEndOfParagraph(False)

        return probe.getStart()
    except Exception as e:
        _handle_exc(err=e)
        return None


def _around_word_unit_bounds(paragraph_text: str, offset: int, direction: str, \
                             big_word: bool = False) -> tuple[int, int] | None:
    """Return (start, end_excl) for one 'aw/aW' unit at offset."""
    length = len(paragraph_text)
    if length == 0:
        # Treat an empty paragraph as a single word-like unit so that
        # commands like "aw" and counts (e.g., "2aw") select an empty
        # paragraph instead of collapsing the cursor.
        return (0, 1)
    i = min(max(0, offset), length - 1)
    cls = _word_char_class(paragraph_text[i], big_word)
    start, end_excl = _word_unit_bounds(paragraph_text, i, big_word)

    if direction == FORWARD:
        if cls == "blank":
            if end_excl < length:
                next_start, next_end = _word_unit_bounds(paragraph_text, end_excl, big_word)
                if _word_char_class(paragraph_text[next_start], big_word) != "blank":
                    end_excl = next_end
            return (start, end_excl)

        if end_excl < length:
            next_start, next_end = _word_unit_bounds(paragraph_text, end_excl, big_word)
            if _word_char_class(paragraph_text[next_start], big_word) == "blank":
                return (start, next_end)

    if direction == BACKWARD and cls == "blank":
        prev_bounds = _word_unit_bounds_backward(paragraph_text, start, big_word)
        if prev_bounds is None:
            return (start, end_excl)
        prev_start, _ = prev_bounds
        return (prev_start, end_excl)

    prev_bounds = _word_unit_bounds_backward(paragraph_text, start, big_word)
    if prev_bounds is not None:
        prev_start, _ = prev_bounds
        if _word_char_class(paragraph_text[prev_start], big_word) == "blank":
            start = prev_start
    return (start, end_excl)


def _word_unit_bounds(paragraph_text: str, offset: int, big_word: bool = False) -> tuple[int, int]:
    """Return the (start, end) indices of the word-like unit containing offset.
    Preserves the original behaviour and signature.
    """
    res = _word_unit_bounds_core(paragraph_text, offset, backward=False, big_word=big_word)
    # Core never returns None for backward=False, but keep a fallback just in case
    return res if res is not None else (0, 0)


def _word_unit_bounds_backward(paragraph_text: str, offset: int, big_word: bool = False) -> \
                               tuple[int, int] | None:
    """Return the (start, end) indices of the word-like unit immediately
    preceding offset (searching backward). May return None when offset <= 0.
    """
    return _word_unit_bounds_core(paragraph_text, offset, backward=True, big_word=big_word)


def _word_unit_bounds_core(paragraph_text: str, offset: int, backward: bool, \
                           big_word: bool = False) -> tuple[int, int] | None:
    """Core logic for finding word unit in paragraph_text.

    If backward is False, returns the (start, end) indices (start inclusive,
    end exclusive) of the unit containing `offset`.
    If backward is True, returns the (start, end) indices of the unit
    immediately preceding `offset`, or None when offset <= 0.
    For empty paragraph_text, (0, 0) is returned.
    """
    length = len(paragraph_text)
    if length == 0:
        return 0, 0

    classify = _word_char_class
    if backward:
        if offset <= 0:
            return None
        i = min(offset - 1, length - 1)
    else:
        i = min(max(0, offset), length - 1)

    cls = classify(paragraph_text[i], big_word)
    start = i
    end = i

    for _ in range(length):
        if start <= 0:
            break
        if classify(paragraph_text[start - 1], big_word) != cls:
            break
        start -= 1

    for _ in range(length):
        if end + 1 >= length:
            break
        if classify(paragraph_text[end + 1], big_word) != cls:
            break
        end += 1

    return start, end + 1


def _around_word_ranges_forward(text_cursor, steps: int, big_word: bool = False) -> \
                                tuple[Any | None, Any | None]:
    """Compute forward ranges for [count] 'aw/aW' text objects from text_cursor."""
    try:
        if steps <= 0:
            return None, None
        start_range = None
        end_range = None

        for i in range(steps):
            paragraph_text, offset = _current_paragraph_text_and_offset(text_cursor)
            if len(paragraph_text) == 0:
                if start_range is None:
                    start_range = _range_at_paragraph_offset(text_cursor, 0)
                try:
                    text_cursor.gotoEndOfParagraph(False)
                    end_after = _range_after_paragraph_break(text_cursor.getEnd())
                except Exception as e:
                    _handle_exc(err=e)
                    end_after = None
                end_range = end_after or text_cursor.getEnd()
                if end_range is None or start_range is None:
                    return None, None
                text_cursor.gotoRange(end_range, False)
                continue

            if offset >= len(paragraph_text):
                if not text_cursor.gotoNextParagraph(False):
                    return None, None
                text_cursor.gotoStartOfParagraph(False)
                paragraph_text, offset = _current_paragraph_text_and_offset(text_cursor)

            bounds = _around_word_unit_bounds(paragraph_text, offset, FORWARD, big_word)
            if bounds is None:
                return None, None
            start_offset, end_excl = bounds
            if start_range is None:
                start_range = _range_at_paragraph_offset(text_cursor, start_offset)
            end_range = _range_at_paragraph_offset(text_cursor, end_excl)

            if end_range is None or start_range is None:
                return None, None
            # Move text_cursor to the position corresponding to end_range so the
            # next iteration starts from the correct place. Avoid using goRight
            # with an offset that may be greater than the paragraph length (which
            # would raise on empty paragraphs).
            try:
                text_cursor.gotoRange(end_range, False)
            except Exception as e:
                _handle_exc(err=e)
                # Fallback to paragraph-relative movement but cap the offset.
                text_cursor.gotoStartOfParagraph(False)
                para_len = len(paragraph_text)
                move = end_excl if end_excl <= para_len else para_len
                if move > 0:
                    text_cursor.goRight(move, False)

            if i < steps - 1 and end_excl >= len(paragraph_text):
                if not text_cursor.gotoNextParagraph(False):
                    return None, None
                text_cursor.gotoStartOfParagraph(False)

        return start_range, end_range
    except Exception as e:
        _handle_exc(err=e)
        return None, None

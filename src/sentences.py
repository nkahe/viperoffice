from __future__ import annotations  # flake9: noqa

from core import (
    KeyEvent,
    _get_cursor,
    _get_text_cursor,
    _handle_exc,
    _paragraph_scan_steps,
    _set_visual_anchor,
)

from utils import (   # type: ignore[reportMissingImports]
    _clone_text_range,
    _get_visual_caret_range,
    _is_current_paragraph_empty,
    _is_cursor_at_whitespace,
    _is_at_first_non_whitespace_after_leading_ws,
    _is_forward_selection,
    _range_after_paragraph_break,
    _same_pos,
    _set_visual_selection,
    _sync_view_cursor,
)

from paragraphs import (
    _to_previous_non_empty_paragraph,
    _to_next_non_empty_paragraph
)

# ------------------
# Sentence motions
# ------------------

def _to_whitespace_start(text_cursor, expand: bool, cursor) -> bool:
    try:
        if not _to_sentence_whitespace_start(text_cursor):
            return False
        _sync_view_cursor(text_cursor, expand, cursor, backward=True)
        return True
    except Exception as e:
        _handle_exc(err=e)
        return False


def _to_sentence_whitespace_start(text_cursor) -> bool:
    """Move text cursor to the start of the current whitespace unit."""
    if text_cursor is None:
        return False
    try:
        probe = _clone_text_range(text_cursor)
        for _ in _paragraph_scan_steps():
            if probe.isStartOfParagraph():
                break
            if not probe.goLeft(1, True):
                break
            if probe.getString() not in (" ", "\t", "\n"):
                probe.collapseToEnd()
                break
            probe.collapseToStart()
        text_cursor.gotoRange(probe.getStart(), False)
        return True
    except Exception as e:
        _handle_exc(err=e)
        return False


def _to_end_of_sentence(tc) -> bool:
    """Move text cursor to end of current sentence for 'is'."""
    if tc is None:
        return False
    try:
        probe = _clone_text_range(tc)
        if not probe.gotoNextSentence(False):
            probe.gotoEndOfParagraph(False)
        if not _move_probe_to_sentence_end(probe):
            return False
        tc.gotoRange(probe.getStart(), False)
        tc.goRight(1, False)
        return True
    except Exception as e:
        _handle_exc(e)
        return False


def _move_probe_to_sentence_end(probe) -> bool:
    """Move probe to the sentence-ending punctuation. Returns False if not found."""
    ch = ""
    for _ in _paragraph_scan_steps():
        if not probe.goLeft(1, True):
            break
        ch = probe.getString()
        probe.collapseToStart()
        if ch not in (" ", "\t", "\n"):
            break
    return ch in (".", "!", "?")


def _inner_sentences_forward(tc, count: int) -> bool:
    """Advance over [count] inner sentences forward for 'is'. Whitespace
    before paragraph or between sentences is also inner sentence.
    """
    if tc is None:
        return False
    try:
        if not tc.isCollapsed():
            caret = _get_visual_caret_range(tc)
            tc.gotoRange(caret, False)
        steps = max(1, int(count))
        moved_any = False
        for _ in range(steps):
            if _is_current_paragraph_empty(tc):
                moved = _advance_empty_paragraph_unit_forward(tc)
            elif _is_cursor_at_whitespace(tc, "after_sentence"):
                moved = tc.gotoNextWord(False)
            else:
                moved = _to_end_of_sentence(tc)
            if not moved:
                break
            moved_any = True
        return moved_any
    except Exception as e:
        _handle_exc(e)
        return False


def _advance_empty_paragraph_unit_forward(tc) -> bool:
    """Advance cursor over a single empty paragraph unit."""
    if tc is None or not _is_current_paragraph_empty(tc):
        return False
    try:
        if not tc.isCollapsed():
            caret = _get_visual_caret_range(tc)
            tc.gotoRange(caret, False)
        tc.gotoStartOfParagraph(False)
        if tc.gotoNextParagraph(False):
            return True
        if tc.goDown(1, False):
            return True
        end_after = _range_after_paragraph_break(tc.getEnd())
        if end_after is not None:
            tc.gotoRange(end_after, False)
            return True
        tc.gotoEndOfParagraph(False)
        return True
    except Exception as e:
        _handle_exc(err=e)
        return False


def _inner_sentences_backward(text_cursor, count: int) -> bool:
    """Advance backward over [count] inner sentence units for 'is'."""
    if text_cursor is None:
        return False
    try:
        steps = max(1, int(count))
        moved_any = False
        for _ in range(steps):
            if not text_cursor.goLeft(1, False):
                break
            if not _normalize_sentence_unit_start(text_cursor):
                break
            moved_any = True
        return moved_any
    except Exception as e:
        _handle_exc(err=e)
        return False


def _normalize_sentence_unit_start(tc) -> bool:
    """Normalize to the start of current sentence unit for 'is'."""
    if tc is None:
        return False
    try:
        if _is_current_paragraph_empty(tc):
            tc.gotoStartOfParagraph(False)
            return True
        if _is_cursor_at_whitespace(tc, "after_sentence") or \
                _is_cursor_at_whitespace(tc, "before_paragraph"):
            return _to_sentence_whitespace_start(tc)
        if not _is_at_sentence_start(tc):
            tc.gotoStartOfSentence(False)
        return True
    except Exception as e:
        _handle_exc(err=e)
        return False


def _to_start_of_next_sentence(tc, expand: bool, cursor) -> bool:
    """To next start of a sentence. Commands ')' and 'as' text-objects.

    Handles edge cases: empty paragraphs (jumps to next non-empty), leading paragraph
    whitespace, and backends that stall on paragraph-end markers.
    """
    moved = None
    # From an empty line, jump directly to the next non-empty paragraph.
    if _is_current_paragraph_empty(tc):
        moved = _to_next_non_empty_paragraph(tc, expand)

    # From leading whitespace of a paragraph, gotoNextSentence would skip the
    # first sentence entirely. Jump to the next word instead, which lands at
    # the start of that sentence.
    if _is_cursor_at_whitespace(tc, "before_paragraph"):
        tc.gotoNextWord(expand)
        moved = True

    if moved is not None:
        _sync_view_cursor(tc, expand, cursor)
        return moved

    tc.gotoNextSentence(expand)

    # If checks below won't work if fresh text_cursor isn't get.
    _sync_view_cursor(tc, expand, cursor)
    tc = _get_text_cursor()
    if tc is None:
        return False

    # Some backends land on the paragraph end marker first, skip that stop.
    if tc.isEndOfParagraph() and not _is_current_paragraph_empty(tc):
        tc.gotoNextParagraph(expand)
        _sync_view_cursor(tc, expand, cursor)

    if _is_cursor_at_whitespace(tc, "before_paragraph"):
        tc.gotoNextWord(expand)  # type: ignore[reportAttributeAccessIssue]
        _sync_view_cursor(tc, expand, cursor)
    return True


def _start_of_sentences_forward(expand: bool, count: int, cursor) -> bool:
    """To start of [count] sentences forward. Commands ')', 'as'."""
    tc = _get_text_cursor()
    if tc is None:
        return False
    try:
        if expand:
            # Collapse to the caret end so forward scan starts from the right place.
            caret = _get_visual_caret_range(tc)
            tc.gotoRange(caret, False)
        steps = max(1, int(count))
        moved_any = False

        for _ in range(steps):
            if not _to_start_of_next_sentence(tc, expand, cursor):
                break
            moved_any = True
        return moved_any
    except Exception as e:
        _handle_exc(e)
        return False


# If paragraph starts with whitespace or is empty.isStartOfSentence() can gives
# True which this fixes.
def _is_at_sentence_start(tc) -> bool:
    try:
        if tc is None or _is_cursor_at_whitespace(tc):
            return False
        probe = _clone_text_range(tc)
        return True if probe.isStartOfSentence() else False
    except Exception as e:
        _handle_exc(e)
        return False


def _to_previous_sentence_start(tc, expand:bool, cursor) -> bool:
    """Move cursor to the start of the previous sentence. Commands '(', 'is'.

    Handles edge cases: leading paragraph whitespace (collapses to paragraph start to
    trigger boundary crossing), first non-whitespace after leading whitespace (detected
    via heuristic and collapsed to paragraph start), and mid-sentence positions.
    """
    old_pos = cursor.getPosition()

    # Normalize paragraph starting position for crossing to previous paragraph.
    if _is_cursor_at_whitespace(tc, "before_paragraph") or \
    _is_at_first_non_whitespace_after_leading_ws(tc):
        tc.gotoStartOfParagraph(False)
        _sync_view_cursor(tc, expand, cursor, backward=True)
        # Fall through — cursor is now at isStartOfParagraph(), handled below.

    # From inside a sentence, first motion should go to current sentence start.
    # Skip this for empty paragraphs so we can cross to previous sentence.
    elif not _is_at_sentence_start(tc) and \
    not _is_current_paragraph_empty(tc):
        tc.gotoStartOfSentence(expand)
        _sync_view_cursor(tc, expand, cursor, backward=True)
        return True

    # Paragraph-boundary behavior matching logic.
    if tc.isStartOfParagraph():
        if _is_current_paragraph_empty(tc):
            if not _to_previous_non_empty_paragraph(tc, expand, cross_empty=False):
                _sync_view_cursor(tc, expand, cursor, backward=True)
                return True
        else:
            if tc.gotoPreviousParagraph(expand):
                # Vi/Vim like behavior where we stop at first empty line.
                if _is_current_paragraph_empty(tc):
                    _sync_view_cursor(tc, expand, cursor, backward=True)
                    return True

        tc.gotoEndOfParagraph(expand)
        if not tc.isStartOfParagraph():
            tc.goLeft(1, expand)
        tc.gotoStartOfSentence(expand)
        _sync_view_cursor(tc, expand, cursor, backward=True)
        return True

    tc.gotoPreviousSentence(expand)
    _sync_view_cursor(tc, expand, cursor, backward=True)

    if _same_pos(old_pos, cursor.getPosition()):
        if tc.goLeft(1, expand):
            tc.gotoPreviousSentence(expand)
        _sync_view_cursor(tc, expand, cursor, backward=True)
    return True


def _to_start_of_sentences_backwards(expand: bool, count, cursor) -> bool:
    """Repeats the motion [count] times. Commands '(', 'is'."""
    text_cursor = _get_text_cursor()
    if text_cursor is None:
        return False
    try:
        if expand:
            # In visual mode, scan from the caret (active) end, not the anchor.
            caret = _get_visual_caret_range(text_cursor)
            text_cursor.gotoRange(caret, False)
        steps = max(1, int(count))
        moved_any = False
        for _ in range(steps):
            moved = _to_previous_sentence_start(text_cursor, expand, cursor)
            _sync_view_cursor(text_cursor, expand, cursor, backward=True)
            if not moved:
                break
            moved_any = True
        return moved_any
    except Exception as e:
        _handle_exc(err=e)
        return False


def _select_sentence_text_objects(count: int, key: KeyEvent, cursor):
    """Select 'as' or 'is' sentence text-objects forward from current object.
       Operator-pending mode or Visual mode without extended selection.
    """
    tc = _get_text_cursor()
    if tc is None:
        return False
    is_around = key.pending is not None and key.pending[-1] == "a"

    if _is_cursor_at_whitespace(tc, "after_sentence"):
        _to_whitespace_start(tc, False, cursor)
        # After moving back without selection, re-anchor at sentence start
        # so _sentences_forward expands from there, not from the original
        # mid-sentence position.
        new_tc = _get_text_cursor()
        if new_tc is not None:
            _set_visual_anchor(new_tc.getStart())
        # If leading whitespace + text sentences are selected which is same
        # as two inner sentences.
        if is_around:
            count *= 2
            is_around = False

    elif _is_cursor_at_whitespace(tc, "before_paragraph"):
        _start_of_sentences_forward(False, 1, cursor)

    elif _is_current_paragraph_empty(tc):
        if is_around:
            # Empty paragraph counts as one unit; around includes trailing whitespace unit.
            count = count * 2
            is_around = False

    elif not _is_at_sentence_start(tc):
        _to_start_of_sentences_backwards(False, 1, cursor)

    tc = _get_text_cursor()
    if tc is None:
        return False

    _set_visual_anchor(tc.getStart())

    if is_around:
        return _start_of_sentences_forward(True, count, cursor)

    probe = _clone_text_range(tc)

    if probe is None:
        return False

    start_range = probe.getStart()
    if not _inner_sentences_forward(probe, count):
        return False
    end_range = probe.getStart()
    _set_visual_anchor(start_range)
    _set_visual_selection(cursor, start_range, end_range)
    return True


def _expand_with_sentences_objects(count: int, key: KeyEvent, cursor) -> bool:
    """Select 'as' or 'is' sentence text-objects in Visual mode with extended
    selection to direction of selection."""
    select_forward = _is_forward_selection(cursor)
    is_around = key.pending is not None and key.pending[-1] == "a"

    if len(cursor.getString()) == 0:
        return _select_sentence_text_objects(count, key, cursor)

    if is_around:
        if select_forward:
            moved = _start_of_sentences_forward(True, count, cursor)
        else:
            moved = _to_start_of_sentences_backwards(True, count, cursor)
            # Include whitespace before the newly selected sentence start,
            # so "as" grabs the spacing between sentences when going backward.
            new_tc = _get_text_cursor()
            new_cursor = _get_cursor()
            if new_tc is not None and new_cursor is not None:
                if moved and not new_tc.isStartOfParagraph():
                    _to_whitespace_start(new_tc, True, new_cursor)
        return moved

    tc = _get_text_cursor()
    if tc is None:
        return False

    anchor = cursor.getStart() if select_forward else cursor.getEnd()
    _set_visual_anchor(anchor)
    caret = cursor.getEnd() if select_forward else cursor.getStart()
    if caret is None:
        return False

    probe = tc.getText().createTextCursorByRange(caret)

    if select_forward:
        moved = _inner_sentences_forward(probe, count)
    else:
        moved = _inner_sentences_backward(probe, count)

    if not moved:
        return False

    new_caret = probe.getStart()
    _set_visual_selection(cursor, anchor, new_caret)
    return True

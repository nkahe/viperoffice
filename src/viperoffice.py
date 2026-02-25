import builtins
import datetime
import unohelper
from functools import lru_cache
from typing import Any, Final

from com.sun.star.awt import XKeyHandler
from com.sun.star.awt import KeyModifier
from com.sun.star.awt import Key
from com.sun.star.awt import Rectangle
from com.sun.star.document import XEventListener


# ------------
# Global state
# ------------

# Provided by LibreOffice's Python macro runtime.
if "XSCRIPTCONTEXT" not in globals():
    XSCRIPTCONTEXT: Any = None

DEBUG = False

# Keywords are used in searching and recognizing with many commands like "w".
# For more info see Vim's help for 'iskeyword'.
ISKEYWORD: Final[str] = "@,48-57,_,192-255"

# Retry limit when detaching key handlers to avoid stale-UNO handler buildup.
MAX_HANDLER_REMOVE_ATTEMPTS = 6

def _state():
    key = "_vipereoffice_state"
    state = getattr(builtins, key, None)
    if state is None:
        state = {
            # Has the extension been started. Will only be set to true.
            "started": False,
            "enabled": False,
            # Current vi input mode. Can be "NORMAL" or "INSERT".
            "mode": "NORMAL",
            # An optional number that may precede the command to multiply
            # or iterate the command.
            "count": 0,
            "operator_pending": None,
            "key_handler": None,
            # Python UNO may leave stale key-handler registrations attached even
            # after removeKeyHandler(); token guards ensure only the latest
            # generation can execute commands.
            "active_handler_token": 0,
            "view_event_listener": None,
            "global_event_broadcaster": None,
            # For debug
            "enable_calls": 0,
            "disable_calls": 0,
            "toggle_calls": 0,
            # One-shot guards for duplicate transition callbacks from stale
            # handlers (Python UNO lifecycle quirk).
            "swallow_once_insert_press": False,
        }
        setattr(builtins, key, state)
    return state


def _set_count(n: int):
    try:
        value = int(n)
    except Exception:
        return False
    if value < 0:
        value = 0
    if value > 999:
        value = 999
    _state()["count"] = value
    _update_statusline()
    return True


def _reset_count():
    _state()["count"] = 0
    _update_statusline()


def _add_to_count(n: int):
    try:
        digit = int(n)
    except Exception:
        return False
    if digit < 0:
        return False
    state = _state()
    if state["count"] <= 1000:
        new_count = int(f"{state['count']}{digit}")
        _update_statusline()
        return _set_count(new_count)
    return False


def _get_count() -> int:
    count = _state().get("count", 0)
    if count == 0:
        return 1
    return count


def _get_raw_count() -> int:
    return _state().get("count", 0)


def _is_count_set() -> bool:
    count = _state().get("count", 0)
    return bool(count != 0)


def _get_operator() -> None | str:
    return _state().get("operator_pending", 0)


# ------------
# Editor
# ------------

# Editor - Functions to manipulate view and model (document).

def _dbg(msg):
    if not DEBUG:
        return
    try:
        ts = datetime.datetime.now().strftime("%m-%d %H:%M:%S.%f")
        with open("/tmp/vibreoffice-debug.log", "a", encoding="utf-8") as f:
            f.write(f"{ts} {msg}\n")
    except Exception:
        pass

def _current_doc():
    try:
        return XSCRIPTCONTEXT.getDocument()
    except Exception:
        return None


def _current_controller():
    doc = _current_doc()
    if doc is None:
        return None
    try:
        return doc.getCurrentController()
    except Exception:
        return None


def _get_dispatcher():
    try:
        ctx = XSCRIPTCONTEXT.getComponentContext()
        smgr = ctx.getServiceManager()
        dispatcher = smgr.createInstanceWithContext("com.sun.star.frame.DispatchHelper", ctx)
        return dispatcher
    except Exception:
        return None


def _get_frame():
    try:
        controller = _current_controller()
        if controller is None:
            return None
        frame = controller.getFrame()
        return frame
    except Exception:
        return None


def _get_cursor():
    controller = _current_controller()
    if controller is None:
        return None
    try:
        return controller.getViewCursor()
    except Exception:
        return None


def _get_text_cursor():
    cursor = _get_cursor()
    if cursor is None:
        return None
    try:
        return cursor.getText().createTextCursorByRange(cursor)
    except Exception:
        return None


def _update_statusline(controller=None):
    if controller is None:
        controller = _current_controller()
    if controller is None:
        return
    try:
        state = _state()
        mode_name = state["mode"]
        text = mode_name
        if _get_raw_count() != 0:
            count_text = _get_count()
            text += f"  {count_text}"
        controller.StatusIndicator.start(text, 0)
    except Exception:
        # Non-fatal for status update.
        pass

def _set_mode(mode_name):
    _state()["mode"] = mode_name
    _update_statusline()


def _show_normal_cursor():
    textCursor = _get_text_cursor()
    controller = _current_controller()
    if textCursor is None or controller is None:
        return
    try:
        textCursor.gotoRange(textCursor.getStart(), False)
        moved = textCursor.goRight(1, False)
        if moved:
            textCursor.goLeft(1, True)
        controller.select(textCursor)
    except Exception:
        pass


def _show_insert_cursor():
    textCursor = _get_text_cursor()
    controller = _current_controller()
    if textCursor is None or controller is None:
        return
    try:
        textCursor.gotoRange(textCursor.getStart(), False)
        controller.select(textCursor)
    except Exception:
        pass


def _goto_mode(mode_name):
    _set_mode(mode_name)
    if mode_name == "NORMAL":
        _show_normal_cursor()
    elif mode_name == "INSERT":
        _show_insert_cursor()


# Commands 'h', 'j', 'k', 'l'.
def _move_charwise(cmd, count=1):
    cursor = _get_cursor()
    if cursor is None:
        return False
    try:
        if cmd == "h":
            return bool(cursor.goLeft(count, False))
        if cmd == "l":
            return bool(cursor.goRight(count, False))
        if cmd == "j":
            return bool(cursor.goDown(count, False))
        if cmd == "k":
            return bool(cursor.goUp(count, False))
    except Exception:
        return False
    return False


# Commands '0' and '^',
def _goto_start_of_line(first_non_blank=False):
    cursor = _get_cursor()
    if cursor is None:
        return False

    try:
        if not first_non_blank:
            return bool(cursor.gotoStartOfLine(False))

        _goto_end_of_line()

        cursor.gotoStartOfLine(True)
        line_text = cursor.getString()
        cursor.gotoStartOfLine(False)

        i = 0
        while i < len(line_text):
            ch = line_text[i]
            if ch != " " and ch != "\t":
                break
            i += 1
        if i > 0:
            cursor.goRight(i, False)
        return True

    except Exception as e:
        return False
    return False


# Motion '$'.
def _goto_end_of_line(count=1):
    cursor = _get_cursor()
    if cursor is None:
        return False

    try:
        if count > 1:
            _move_charwise("j", count - 1)
        old_pos = cursor.getPosition()
        cursor.gotoEndOfLine(False)
        new_pos = cursor.getPosition()

        old_y = getattr(old_pos, "Y", None)
        if callable(old_y):
            old_y = old_y()
        new_y = getattr(new_pos, "Y", None)
        if callable(new_y):
            new_y = new_y()

        # LibreOffice can place cursor at next line start; move left back
        # to previous line end unless this was an empty-line no-op.
        if cursor.isAtStartOfLine() and old_y != new_y:
            cursor.goLeft(1, False)
        return True

    except Exception as e:
        return False
    return False


# 'G': Go to line [count] motion. 0 = last line.
def _goto_line(expand: bool, raw_count: int) -> bool:
    cursor = _get_cursor()
    if cursor is None:
        return False
    try:
        if raw_count == 0:
            cursor.gotoEnd(False)
            return True
        line_number = max(1, int(raw_count))
        cursor.gotoStart(expand)
        if line_number > 1:
            cursor.goDown(line_number - 1, expand)
        return True
    except Exception:
        return False


# ISKEYWORD is constant, so compile checker once and reuse for word motions.
@lru_cache(maxsize=1)
def _compile_iskeyword_checker():
    spec = ISKEYWORD
    ranges = []
    singles = set()
    include_alpha = False

    try:
        tokens = [token.strip() for token in str(spec).split(",") if token.strip()]
        for token in tokens:
            if token == "@":
                include_alpha = True
                continue
            if "-" in token and token.count("-") == 1:
                start_s, end_s = token.split("-", 1)
                start_i = int(start_s)
                end_i = int(end_s)
                if start_i > end_i:
                    start_i, end_i = end_i, start_i
                ranges.append((start_i, end_i))
                continue
            if len(token) == 1:
                singles.add(token)
    except Exception:
        include_alpha = True
        ranges = [(48, 57), (192, 255)]
        singles = {"_"}

    def _is_keyword_char(ch):
        if ch in singles:
            return True
        if include_alpha and ch.isalpha():
            return True
        o = ord(ch)
        for start_i, end_i in ranges:
            if start_i <= o <= end_i:
                return True
        return False

    return _is_keyword_char


def _word_char_class(ch, is_keyword_char, big_word: bool = False):
    if ch == " " or ch == "\t" or ch == "\n":
        return "blank"
    if big_word:
        return "other"
    if is_keyword_char(ch):
        return "keyword"
    return "other"


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


# Word motion specs.
WORD_DIRECTION_FORWARD = "forward"
WORD_DIRECTION_BACKWARD = "backward"
WORD_TARGET_START = "start"
WORD_TARGET_END = "end"

_WORD_MOTION_W = {
    "direction": WORD_DIRECTION_FORWARD,
    "target": WORD_TARGET_START,
    "big_word": False,
    "cross_empty": True,
    "inclusive": False,
}
_WORD_MOTION_B = {
    "direction": WORD_DIRECTION_BACKWARD,
    "target": WORD_TARGET_START,
    "big_word": False,
    "cross_empty": True,
    "inclusive": False,
}
_WORD_MOTION_BIG_B = {
    "direction": WORD_DIRECTION_BACKWARD,
    "target": WORD_TARGET_START,
    "big_word": True,
    "cross_empty": True,
    "inclusive": False,
}
_WORD_MOTION_E = {
    "direction": WORD_DIRECTION_FORWARD,
    "target": WORD_TARGET_END,
    "big_word": False,
    "cross_empty": False,
    "inclusive": True,
}
_WORD_MOTION_BIG_E = {
    "direction": WORD_DIRECTION_FORWARD,
    "target": WORD_TARGET_END,
    "big_word": True,
    "cross_empty": False,
    "inclusive": True,
}
_WORD_MOTION_GE = {
    "direction": WORD_DIRECTION_BACKWARD,
    "target": WORD_TARGET_END,
    "big_word": False,
    "cross_empty": True,
    "inclusive": True,
}
_WORD_MOTION_G_BIG_E = {
    "direction": WORD_DIRECTION_BACKWARD,
    "target": WORD_TARGET_END,
    "big_word": True,
    "cross_empty": True,
    "inclusive": True,
}
_WORD_MOTION_BIG_W = {
    "direction": WORD_DIRECTION_FORWARD,
    "target": WORD_TARGET_START,
    "big_word": True,
    "cross_empty": True,
    "inclusive": False,
}


def _validate_word_motion_spec(spec) -> bool:
    if not isinstance(spec, dict):
        return False
    required = ("direction", "target", "big_word", "cross_empty", "inclusive")
    for key in required:
        if key not in spec:
            return False
    if spec["direction"] not in (WORD_DIRECTION_FORWARD, WORD_DIRECTION_BACKWARD):
        return False
    if spec["target"] not in (WORD_TARGET_START, WORD_TARGET_END):
        return False
    return True


def _cursor_xy(cursor):
    if cursor is None:
        return (None, None)
    try:
        return _pos_xy(cursor.getPosition())
    except Exception:
        return (None, None)


def _normalize_motion_range(result, for_operator: bool = False):
    if not isinstance(result, dict):
        return {"moved": False}
    normalized = dict(result)
    normalized["operator_inclusive"] = bool(for_operator and normalized.get("inclusive", False))
    normalized["operator_exclusive"] = bool(for_operator and not normalized.get("inclusive", False))
    return normalized


def _clone_text_range(text_cursor):
    try:
        text_obj = text_cursor.getText()
        return text_obj.createTextCursorByRange(text_cursor.getStart()).getStart()
    except Exception:
        return None


def _range_xy(rng):
    return _pos_xy(rng)


def _query_word_motion(spec, count: int, expand: bool = False):
    text_cursor = _get_text_cursor()
    cursor = _get_cursor()
    if text_cursor is None or cursor is None or not _validate_word_motion_spec(spec):
        return _normalize_motion_range({"moved": False})

    steps = max(1, int(count))
    is_keyword_char = _compile_iskeyword_checker()
    start_range = _clone_text_range(text_cursor)
    moved_any = False
    steps_done = 0

    for _ in range(steps):
        if not expand:
            # Normal mode motions must operate on a collapsed caret.
            text_cursor.gotoRange(text_cursor.getStart(), False)
        if not _word_motion_once(
            text_cursor,
            expand,
            is_keyword_char,
            spec,
        ):
            break
        moved_any = True
        steps_done += 1

    end_range = _clone_text_range(text_cursor)

    result = {
        "moved": moved_any,
        "steps_done": steps_done,
        "start_pos": _range_xy(start_range),
        "end_pos": _range_xy(end_range),
        "start_range": start_range,
        "end_range": end_range,
        "direction": spec.get("direction"),
        "inclusive": bool(spec.get("inclusive", False)),
    }
    return _normalize_motion_range(result)


def _apply_motion_result(result, expand: bool, operator: str | None) -> bool:
    if not isinstance(result, dict) or not result.get("moved", False):
        return False
    end_range = result.get("end_range")
    cursor = _get_cursor()
    if cursor is None or end_range is None:
        return False
    try:
        if operator is None:
            cursor.gotoRange(end_range, expand)
        return True
    except Exception:
        return False


def _goto_next_paragraph_with_policy(text_cursor, expand: bool, cross_empty: bool) -> bool:
    if not text_cursor.gotoNextParagraph(expand):
        return False
    if not cross_empty:
        while _is_current_paragraph_empty(text_cursor):
            if not text_cursor.gotoNextParagraph(expand):
                break
    return True


def _goto_previous_paragraph_with_policy(text_cursor, expand: bool, cross_empty: bool) -> bool:
    if not text_cursor.gotoPreviousParagraph(expand):
        return False
    if not cross_empty:
        while _is_current_paragraph_empty(text_cursor):
            if not text_cursor.gotoPreviousParagraph(expand):
                break
    return True


def _scan_forward_word_target(paragraph_text, offset, is_keyword_char, spec):
    length = len(paragraph_text)
    if offset >= length:
        return None

    classify = _word_char_class
    big_word = bool(spec.get("big_word", False))
    target = spec.get("target", WORD_TARGET_START)
    i = offset

    if target == WORD_TARGET_START:
        cls = classify(paragraph_text[i], is_keyword_char, big_word)
        if cls == "blank":
            while i < length and classify(paragraph_text[i], is_keyword_char, big_word) == "blank":
                i += 1
            return i
        while i < length and classify(paragraph_text[i], is_keyword_char, big_word) == cls:
            i += 1
        while i < length and classify(paragraph_text[i], is_keyword_char, big_word) == "blank":
            i += 1
        return i

    if target == WORD_TARGET_END:
        # For "e": if on non-blank, advance once so repeated `e` progresses.
        if classify(paragraph_text[i], is_keyword_char, big_word) != "blank":
            i += 1
        # Then skip blanks and land on last char of the next word.
        while i < length and classify(paragraph_text[i], is_keyword_char, big_word) == "blank":
            i += 1
        if i >= length:
            return None
        cls = classify(paragraph_text[i], is_keyword_char, big_word)
        while i + 1 < length and classify(paragraph_text[i + 1], is_keyword_char, big_word) == cls:
            i += 1
        return i

    return None


def _scan_backward_word_target(paragraph_text, offset, is_keyword_char, spec):
    length = len(paragraph_text)
    if length == 0 or offset <= 0:
        return None

    classify = _word_char_class
    big_word = bool(spec.get("big_word", False))
    target = spec.get("target", WORD_TARGET_START)
    i = min(offset - 1, length - 1)

    # Skip trailing blanks when scanning backward.
    while i >= 0 and classify(paragraph_text[i], is_keyword_char, big_word) == "blank":
        i -= 1
    if i < 0:
        return None

    if target == WORD_TARGET_END:
        return i

    if target == WORD_TARGET_START:
        cls = classify(paragraph_text[i], is_keyword_char, big_word)
        while i - 1 >= 0 and classify(paragraph_text[i - 1], is_keyword_char, big_word) == cls:
            i -= 1
        return i

    return None


def _word_motion_once_forward(text_cursor, expand: bool, is_keyword_char, spec) -> bool:
    cross_empty = bool(spec.get("cross_empty", True))
    paragraph_text, offset = _current_paragraph_text_and_offset(text_cursor)
    length = len(paragraph_text)

    if length == 0:
        return _goto_next_paragraph_with_policy(text_cursor, expand, cross_empty)

    if offset >= length:
        return _goto_next_paragraph_with_policy(text_cursor, expand, cross_empty)

    next_offset = _scan_forward_word_target(paragraph_text, offset, is_keyword_char, spec)

    if next_offset is not None and next_offset < length:
        text_cursor.gotoStartOfParagraph(False)
        if next_offset > 0:
            text_cursor.goRight(next_offset, expand)
        return True

    return _goto_next_paragraph_with_policy(text_cursor, expand, cross_empty)


def _word_motion_once_backward(text_cursor, expand: bool, is_keyword_char, spec) -> bool:
    cross_empty = bool(spec.get("cross_empty", True))
    paragraph_text, offset = _current_paragraph_text_and_offset(text_cursor)
    length = len(paragraph_text)

    if length == 0 or offset <= 0:
        if not _goto_previous_paragraph_with_policy(text_cursor, expand, cross_empty):
            return False
        paragraph_text, offset = _current_paragraph_text_and_offset(text_cursor)
        length = len(paragraph_text)

        # Empty paragraph is a valid word-step stop when crossing is allowed.
        if length == 0:
            return True
        offset = length

    prev_offset = _scan_backward_word_target(paragraph_text, offset, is_keyword_char, spec)
    if prev_offset is not None and 0 <= prev_offset < length:
        text_cursor.gotoStartOfParagraph(False)
        if prev_offset > 0:
            text_cursor.goRight(prev_offset, expand)
        return True

    if not _goto_previous_paragraph_with_policy(text_cursor, expand, cross_empty):
        return False
    paragraph_text, offset = _current_paragraph_text_and_offset(text_cursor)
    length = len(paragraph_text)
    if length == 0:
        return True
    prev_offset = _scan_backward_word_target(paragraph_text, length, is_keyword_char, spec)
    if prev_offset is None:
        return False
    text_cursor.gotoStartOfParagraph(False)
    if prev_offset > 0:
        text_cursor.goRight(prev_offset, expand)
    return True


def _word_motion_once(text_cursor, expand: bool, is_keyword_char, spec) -> bool:
    """Execute one step for a configured word motion.

    Args:
        text_cursor: Model text cursor used for paragraph and offset operations.
        cursor: View cursor that must be synchronized after movement.
        expand: If True, keeps selection expanded while moving.
        is_keyword_char: Predicate that classifies chars as keyword chars.
        spec: Motion config (direction/target/big_word/empty-line policy).

    Returns:
        True if cursor advanced, otherwise False.
    """
    direction = spec.get("direction", WORD_DIRECTION_FORWARD)
    if direction == WORD_DIRECTION_FORWARD:
        return _word_motion_once_forward(text_cursor, expand, is_keyword_char, spec)
    if direction == WORD_DIRECTION_BACKWARD:
        return _word_motion_once_backward(text_cursor, expand, is_keyword_char, spec)
    return False


def _run_word_motion_command(
    spec,
    expand: bool,
    count: int = 1,
    operator: str | None = None,
) -> bool:
    """Run a word-motion command (e.g. `w`) and optionally apply an operator.

    Args:
        spec: Word motion specification (direction/target/big_word/etc.).
        expand: If True, keeps selection expanded while moving.
        count: Number of word motions to perform (minimum 1).
        operator: Pending operator, or None for plain cursor motion.

    Returns:
        True if cursor moved at least once, otherwise False.
    """
    try:
        if not _validate_word_motion_spec(spec):
            return False
        if expand:
            # Keep selection behavior by applying one step at a time.
            steps = max(1, int(count))
            moved_any = False
            for _ in range(steps):
                result = _query_word_motion(spec, 1, expand=True)
                if not result.get("moved", False):
                    break
                if not _apply_motion_result(result, expand, operator):
                    break
                moved_any = True
            return moved_any

        result = _query_word_motion(spec, count, expand=False)
        if not result.get("moved", False):
            return False

        return _apply_motion_result(result, expand, operator)
    except Exception:
        return False


def _pos_xy(pos):
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


def _is_current_paragraph_empty(text_cursor):
    if text_cursor is None:
        return False
    try:
        probe = text_cursor.getText().createTextCursorByRange(text_cursor)
        probe.gotoStartOfParagraph(False)
        probe.gotoEndOfParagraph(True)
        return len(probe.getString()) == 0
    except Exception:
        return False


def _sync_view_cursor_to_text_cursor(view_cursor, text_cursor, expand: bool):
    edge = text_cursor.getEnd() if expand else text_cursor.getStart()
    view_cursor.gotoRange(edge, False)


def _goto_next_non_empty_paragraph(text_cursor, expand: bool):
    moved = False
    while True:
        if not text_cursor.gotoNextParagraph(expand):
            break
        moved = True
        if not _is_current_paragraph_empty(text_cursor):
            break
    return moved


def _goto_next_sentence(text_cursor, cursor, expand: bool):
    # Implements one ")" motion with paragraph-edge handling.
    old_pos = cursor.getPosition()

    # From an empty line, jump directly to the next non-empty paragraph.
    if _is_current_paragraph_empty(text_cursor):
        moved = _goto_next_non_empty_paragraph(text_cursor, expand)
        if moved:
            _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
        return moved

    text_cursor.gotoNextSentence(expand)
    _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)

    # Some backends land on the paragraph end marker first; skip that stop.
    if text_cursor.isEndOfParagraph() and not _is_current_paragraph_empty(text_cursor):
        text_cursor.gotoNextParagraph(expand)
        _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
        return True

    if _same_pos(old_pos, cursor.getPosition()):
        if text_cursor.isEndOfParagraph():
            if _is_current_paragraph_empty(text_cursor):
                _goto_next_non_empty_paragraph(text_cursor, expand)
            else:
                text_cursor.gotoNextParagraph(expand)
        else:
            text_cursor.goRight(1, expand)
            text_cursor.gotoNextSentence(expand)
        _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
    return True


# Repeats ")" motion by count times.
def _goto_sentences_forward(expand: bool, count = 1):
    text_cursor = _get_text_cursor()
    cursor = _get_cursor()
    if text_cursor is None or cursor is None:
        return False
    try:
        steps = max(1, int(count))
        moved_any = False
        for _ in range(steps):
            if not _goto_next_sentence(text_cursor, cursor, expand):
                break
            moved_any = True
        return moved_any
    except Exception:
        return False


def _is_at_sentence_start_heuristic(text_cursor):
    if text_cursor is None:
        return False
    try:
        probe = text_cursor.getText().createTextCursorByRange(text_cursor.getStart())
        if probe.isStartOfParagraph():
            return True

        ch = ""
        while True:
            if not probe.goLeft(1, True):
                break
            ch = probe.getString()
            probe.collapseToStart()
            if ch not in (" ", "\t", "\"", "'", ")", "]"):
                break
        return ch in (".", "!", "?")
    except Exception:
        return False


def _goto_previous_sentence(text_cursor, cursor, expand: bool):
    # Implements one "(" motion with sentence-start/paragraph-edge handling.
    old_pos = cursor.getPosition()

    # From inside a sentence, first "(" should go to current sentence start.
    if not _is_at_sentence_start_heuristic(text_cursor):
        text_cursor.gotoStartOfSentence(expand)
        _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
        return True

    # Paragraph-boundary behavior matching logic.
    if text_cursor.isStartOfParagraph():
        if _is_current_paragraph_empty(text_cursor):
            moved = text_cursor.gotoPreviousParagraph(expand)
            if not moved:
                return False
            while _is_current_paragraph_empty(text_cursor):
                if not text_cursor.gotoPreviousParagraph(expand):
                    _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
                    return True
            text_cursor.gotoEndOfParagraph(expand)
            if not text_cursor.isStartOfParagraph():
                text_cursor.goLeft(1, expand)
            text_cursor.gotoStartOfSentence(expand)
            _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
            return True
        else:
            if text_cursor.gotoPreviousParagraph(expand):
                if _is_current_paragraph_empty(text_cursor):
                    _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
                    return True
                text_cursor.gotoEndOfParagraph(expand)
                if not text_cursor.isStartOfParagraph():
                    text_cursor.goLeft(1, expand)
                text_cursor.gotoStartOfSentence(expand)
                _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
                return True

    text_cursor.gotoPreviousSentence(expand)
    _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
    if _same_pos(old_pos, cursor.getPosition()):
        if text_cursor.goLeft(1, expand):
            text_cursor.gotoPreviousSentence(expand)
        _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
    return True


# Repeats "(" motion by count times.
def _goto_sentences_backwards(expand: bool, count=2):
    # Repeats "(" motion by count times.
    text_cursor = _get_text_cursor()
    cursor = _get_cursor()
    if text_cursor is None or cursor is None:
        return False
    try:
        steps = max(1, int(count))
        moved_any = False
        for _ in range(steps):
            if not _goto_previous_sentence(text_cursor, cursor, expand):
                break
            moved_any = True
        return moved_any
    except Exception:
        return False


# For commands 'x','X' and 's'.
def _delete_characters(count=1, reverse=False, substitute=False):
    textCursor = _get_text_cursor()
    if textCursor is None:
        return False
    try:
        textCursor.gotoRange(textCursor.getStart(), False)
        if reverse is True:
            textCursor.collapseToStart()
            # At start of line
            if not textCursor.goLeft(count, True):
                return False
        # At end of line
        elif not textCursor.goRight(count, True):
            return False
        textCursor.setString("")
        if substitute:
            state = _state()
            _switch_to_insert(state, "i")
        return True
    except Exception:
        return False


# Commmand 'Esc',
def _leave_insert_to_normal():
    cursor = _get_cursor()
    if cursor is not None:
        try:
            if not cursor.isAtStartOfLine():
                _move_charwise("h")
        except Exception:
            pass
    _goto_mode("NORMAL")


# Commands 'a', 'I', 'A', 'o' and 'O'.
def _switch_to_insert(state, cmd: str):
    # Some stale handlers may still receive this same insert-transition key
    # callback. Swallow one stale duplicate so the transition key does not get
    # inserted as text.
    state["swallow_once_insert_press"] = True
    try:
        if cmd == "a" or cmd == "A":
            textCursor = _get_text_cursor()
            if cmd == "A":
                _goto_end_of_line()
            elif textCursor is not None and not textCursor.isEndOfParagraph():
                _move_charwise("l")

        elif cmd == "I":
            _goto_start_of_line(True)

        elif cmd == "o":
            cursor = _get_cursor()
            if cursor is not None:
                _goto_end_of_line()
                _move_charwise("l")
                cursor.setString(chr(13))  # CR
                if not cursor.isAtStartOfLine():
                    cursor.setString(chr(13) + chr(13))
                    _move_charwise("l")

        elif cmd == "O":
            _goto_start_of_line()
            cursor = _get_cursor()
            if cursor is not None:
                cursor.setString(chr(13))
                if not cursor.isAtStartOfLine():
                    _move_charwise("h")
                    cursor.setString(chr(13) + chr(13))
                    _move_charwise("l")

    except Exception:
        pass
    _goto_mode("INSERT")


# Commands 'u', 'C-r'.
def _undo_changes(count=1, redo=False) -> bool:
    doc = _current_doc()
    if doc is None:
        return False
    try:
        if redo:
            for _ in range(count):
                doc.getUndoManager().redo()
        else:
            for _ in range(count):
                doc.getUndoManager().undo()
        return True
    except Exception:
        # Non-fatal when no more undo actions exist.
        return False


def _scroll_window(expand: bool, forward, halfpage=False) -> bool:
    """Scroll window by one page. Commands 'C-f' (forward) and 'C-b' (backward).
    """
    try:
        cursor = _get_cursor()
        if cursor is None:
            return False
        if forward:
            return cursor.screenDown()
        else:
            return cursor.screenUp()
    except Exception:
        return False


def _jump_to_page(expand: bool, target: str, operator: str | None):
    try:
        cursor = _get_cursor()
        if cursor is None:
            return False
        if target is "start":
            return bool(cursor.jumpToStartOfPage())
        else:
            return False
    except Exception:
        return False

# --------------
# Input handling
# --------------

"""Build NORMAL-mode command dispatch map for character actions."""
def _normal_actions(state, key_char, count: int, raw_count: int, operator: str | None):
    actions = {
        "i": lambda: _switch_to_insert(state, "i"),
        "I": lambda: _switch_to_insert(state, "I"),
        "a": lambda: _switch_to_insert(state, "a"),
        "A": lambda: _switch_to_insert(state, "A"),
        "o": lambda: _switch_to_insert(state, "o"),
        "O": lambda: _switch_to_insert(state, "O"),
        "G": lambda: _goto_line(False, raw_count),
        "h": lambda: _move_charwise("h", count),
        "j": lambda: _move_charwise("j", count),
        "k": lambda: _move_charwise("k", count),
        "l": lambda: _move_charwise("l", count),
        "H": lambda: _jump_to_page(False, "start", operator),
        "w": lambda: _run_word_motion_command(_WORD_MOTION_W, False, count, operator),
        "W": lambda: _run_word_motion_command(_WORD_MOTION_BIG_W, False, count, operator),
        "e": lambda: _run_word_motion_command(_WORD_MOTION_E, False, count, operator),
        "E": lambda: _run_word_motion_command(_WORD_MOTION_BIG_E, False, count, operator),
        "b": lambda: _run_word_motion_command(_WORD_MOTION_B, False, count, operator),
        "B": lambda: _run_word_motion_command(_WORD_MOTION_BIG_B, False, count, operator),
        ")": lambda: _goto_sentences_forward(False, count),
        "(": lambda: _goto_sentences_backwards(False, count),
        "u": lambda: _undo_changes(count),
        "U": lambda: _undo_changes(count, True),
        "s": lambda: _delete_characters(count, False, True),
        "x": lambda: _delete_characters(count, False),
        "X": lambda: _delete_characters(count, True),
        "^": lambda: _goto_start_of_line(True),
        "$": lambda: _goto_end_of_line(count),
        "/": _focus_findbar,
    }
    if key_char == "0" and raw_count == 0:
        actions["0"] = lambda: _goto_start_of_line()
    return actions


def _normal_ctrl_actions(count):
    r_code = int(getattr(Key, "R", 529))
    # d_code = int(getattr(Key, "D", 514))
    # u_code = int(getattr(Key, "U", 530))
    f_code = int(getattr(Key, "F", 517))
    b_code = int(getattr(Key, "B", 512))

    actions = {
        r_code: lambda: _undo_changes(count, False),
        f_code: lambda: _scroll_window(False, True),
        b_code: lambda: _scroll_window(False, False)
    }

    return actions


# UNO key handler
# Return values for keyPressed/keyReleased:
#   True  -> event is consumed by ViperOffice (LibreOffice should not process it)
#   False -> event is passed through to LibreOffice default handling
class KeyHandler(unohelper.Base, XKeyHandler):
    def __init__(self, token):
        self._token = token

    def _is_active_instance(self):
        return self._token == _state().get("active_handler_token")

    def _consume_active_event(self, action=None):
        if action is not None:
            action()
            _reset_count()
        return True

    def keyPressed(self, event):
        state = _state()
        if not state["enabled"]:
            return False

        # Don't do anything if textCursor isn't working (as in annotations).
        textCursor = _get_text_cursor()
        count = _get_count()
        raw_count = _get_raw_count()
        operator = _get_operator()

        if textCursor is None:
            return False

        if not self._is_active_instance():
            # Stale handlers can still be called by LO after lifecycle changes.
            # Swallow one duplicate transition callback if needed.
            if state["mode"] == "INSERT" and state["swallow_once_insert_press"]:
                state["swallow_once_insert_press"] = False
                return True
            if state["mode"] == "NORMAL" and (
                _is_navigation_key(event) or _is_function_key(event)
            ):
                return False
            return bool(state["mode"] == "NORMAL")

        key_code = _key_code(event)
        key_char = _normalize_key_char(event)
        mods = _event_modifiers(event)
        is_only_ctrl = _is_only_ctrl(mods)
        is_escape = _is_escape(key_code, is_only_ctrl)

        # _msgbox(f"mods: {mods}, key_char: {key_char} key_code: {key_code}")

        if state["mode"] == "INSERT":
            if is_escape or (is_only_ctrl and key_code == 514):  # C-c
                return self._consume_active_event(_leave_insert_to_normal)
            return False

        # ----- Non-Insert mode after this -----

        is_altgr_char = _is_altgr_char_event(event, key_char, key_code)

        # _msgbox(f"mods: {mods}\nkey_char: {key_char}\nkey_code: {key_code} \n"
        #     f"is_only_ctrl: {is_only_ctrl}")

        if is_only_ctrl:
            actions = _normal_ctrl_actions(count)
            action = actions.get(key_code)

            if action is not None:
                return self._consume_active_event(action)
            else:
                return False

        # Pass other non-shift modified shortcuts through, except characters
        # made with AltGr.
        if _has_non_shift_modifier(event):
            if not is_altgr_char:
                return False

        # ----- Keys without modifiers after this ----

        # Match Normal mode character commands.
        normal_actions = _normal_actions(state, key_char, count, raw_count, operator)
        action = normal_actions.get(key_char)
        if action is not None:
            return self._consume_active_event(action)

        # Count parsing
        # - 1..9 always extend count
        # - 0 extends count only after count has started
        if _is_digit_char(key_char):
            if key_char != "0" or _get_raw_count() > 0:
                _add_to_count(int(key_char))
                # _msgbox(f"count {int(key_char)}")
                return self._consume_active_event()

        # ----- Non-character keys -----

        if _is_function_key(event):
            return False

        _reset_count()

        # TODO: bind navigation keys to movement functions so count can be used.
        if _is_navigation_key(event):
            return False
        if is_escape:
            return self._consume_active_event(lambda: _goto_mode("NORMAL"))
        if _is_delete_key(event):
            return self._consume_active_event(_delete_characters)
        if _is_backspace_key(event):
            return self._consume_active_event(lambda: _move_charwise("h", count))
        if _is_insert_key(event):
            return self._consume_active_event(lambda: _switch_to_insert(state, "i"))

        return self._consume_active_event()

    def keyReleased(self, event):
        state = _state()
        if not state["enabled"]:
            return False
        # Keep NORMAL cursor rendering for navigation releases, including
        # Ctrl+Home/Ctrl+End where Ctrl would otherwise short-circuit below.
        if state["mode"] == "NORMAL" and _is_navigation_key(event):
            _show_normal_cursor()
            return False
        if state["mode"] == "NORMAL" and _is_function_key(event):
            return False
        if state["mode"] == "NORMAL":
            _show_normal_cursor()
            return True
        return False

    def disposing(self, event):
        return None


# For debugging if needed.
def _msgbox(text, title="ViperOffice"):
    try:
        controller = _current_controller()
        if controller is None:
            return
        parent = controller.getFrame().getContainerWindow()
        toolkit = parent.getToolkit()
        try:
            # Legacy UNO signature used by some versions.
            box = toolkit.createMessageBox(
                parent, Rectangle(), "infobox", 1, title, str(text),
            )
        except Exception:
            # Newer UNO signature used by some versions.
            box = toolkit.createMessageBox(
                parent, 1, 1, title, str(text),
            )
        box.execute()
    except Exception:
        pass


def _focus_findbar() -> bool:
    """Show default LibreOffice find bar. Command '/'.
    """
    try:
        dispatcher = _get_dispatcher()
        frame = _get_frame()
        if dispatcher is None or frame is None:
            return False
        dispatcher.executeDispatch(frame, "vnd.sun.star.findbar:FocusToFindbar", "", 0, ())
        return True
    except Exception:
        # dispatcher.executeDispatch(frame, ".uno:SearchDialog", "", 0, ())
        return False


# Normalize UNO key event payload into a single-character command key when possible.
# Handles runtime-specific KeyChar/KeyCode representations used by LO/UNO.
def _normalize_key_char(event):
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
    except Exception:
        pass

    try:
        code = int(k)
    except Exception:
        code = -1

    if 0 <= code <= 255:
        return chr(code)

    # Fallback: some environments deliver characters as key codes.
    try:
        key_code = int(event.KeyCode)
        # com.sun.star.awt.Key.A..Z are typically 512..537.
        if 512 <= key_code <= 537:
            # Respect Shift when falling back to key codes, otherwise "HJKLIX"
            # would be treated as lowercase NORMAL-mode commands.
            shift_mask = getattr(KeyModifier, "SHIFT", 1)
            is_shift = bool(_event_modifiers(event) & shift_mask)
            base = ord("A") if is_shift else ord("a")
            return chr(base + (key_code - 512))

        # Ignore NUL keycode (0); it is often a non-printable placeholder.
        if 1 <= key_code <= 255:
            return chr(key_code)

    except Exception:
        pass

    return ""


def _event_modifiers(event):
    try:
        return int(event.Modifiers)
    except Exception:
        return 0


def _has_non_shift_modifier(event):
    mods = _event_modifiers(event)
    return bool(mods & (KeyModifier.MOD1 | KeyModifier.MOD2 | KeyModifier.MOD3))


def _is_digit_char(ch):
    return isinstance(ch, str) and len(ch) == 1 and "0" <= ch <= "9"


def _is_only_ctrl(mods):
    ctrl = getattr(KeyModifier, "MOD1", 0)
    alt = getattr(KeyModifier, "MOD2", 0)
    meta = getattr(KeyModifier, "MOD3", 0)
    return bool(mods & ctrl) and not bool(mods & (alt | meta))


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


def _is_altgr_char_event(event, key_char, key_code):
    if not (isinstance(key_char, str) and len(key_char) == 1 and ord(key_char) >= 32):
        return False
    mods = _event_modifiers(event)
    mod2 = getattr(KeyModifier, "MOD2", 0)
    mod3 = getattr(KeyModifier, "MOD3", 0)
    # Treat AltGr as text-producing modified input. In this environment these
    # events arrive with key_code == 0 (e.g. AltGr+4 -> "$"), while normal
    # Ctrl/Alt shortcuts have concrete key codes.
    return bool(mods & mod2) and not bool(mods & mod3) and key_code == 0


def _key_code(event):
    try:
        return int(event.KeyCode)
    except Exception:
        return -1


def _is_escape(key_code, is_ctrl):
    # Ctrl+[ is interpreted as Esc like in terminal.
    return (key_code == 1281) or (
        key_code == 1315 and is_ctrl
    )


def _is_navigation_key(event):
    # LibreOffice key codes: Home/End/Left/Right/Up/Down/PageUp/PageDown.
    return _key_code(event) in (1024, 1025, 1026, 1027, 1028, 1029, 1030, 1031)


def _is_insert_key(event):
    try:
        return _key_code(event) == int(getattr(Key, "INSERT"))
    except Exception:
        return False


def _is_delete_key(event):
    try:
        return _key_code(event) == int(getattr(Key, "DELETE"))
    except Exception:
        return False


def _is_backspace_key(event):
    try:
        return _key_code(event) == int(getattr(Key, "BACKSPACE"))
    except Exception:
        return _key_code(event) == 1283


def _is_function_key(event):
    key_code = _key_code(event)
    for i in range(1, 13):
        try:
            if key_code == int(getattr(Key, f"F{i}")):
                return True
        except Exception:
            continue
    return False


# ---------------
# Infra
# ---------------

# Non-editor functionality: initialization, enabling and disabling for all
# windows, listening events.

def _desktop():
    try:
        return XSCRIPTCONTEXT.getDesktop()
    except Exception:
        return None


# If component is oducment not for example Calc sheet.
def _is_text_document(doc):
    if doc is None:
        return False
    try:
        return bool(doc.supportsService("com.sun.star.text.TextDocument"))
    except Exception:
        return False


def _iter_text_document_controllers():
    desktop = _desktop()
    if desktop is None:
        return
    try:
        components = desktop.getComponents()
    except Exception:
        return
    if components is None:
        return
    try:
        if not components.hasElements():
            return
        enum = components.createEnumeration()
        while enum.hasMoreElements():
            component = enum.nextElement()
            if not _is_text_document(component):
                continue
            try:
                controller = component.getCurrentController()
            except Exception:
                controller = None
            if controller is not None:
                yield controller
    except Exception:
        return


def _global_event_broadcaster():
    state = _state()
    if state["global_event_broadcaster"] is not None:
        return state["global_event_broadcaster"]
    try:
        ctx = XSCRIPTCONTEXT.getComponentContext()
        broadcaster = ctx.getByName("/singletons/com.sun.star.frame.theGlobalEventBroadcaster")
        state["global_event_broadcaster"] = broadcaster
        return broadcaster
    except Exception:
        return None


def _detach_key_handler_from_all_views():
    for controller in _iter_text_document_controllers():
        _detach_controller(controller)


def _detach_controller(controller):
    state = _state()
    if controller is None or state["key_handler"] is None:
        return
    for _ in range(MAX_HANDLER_REMOVE_ATTEMPTS):
        try:
            controller.removeKeyHandler(state["key_handler"])
        except Exception:
            break


def _attach_key_handler_to_all_views():
    controller_count = 0
    for controller in _iter_text_document_controllers():
        _attach_controller(controller)
        controller_count += 1
    return controller_count


def _attach_controller(controller):
    state = _state()
    if controller is None or state["key_handler"] is None:
        return
    # LibreOffice can accumulate duplicate registrations of the same handler.
    # Drain old registrations so addKeyHandler keeps one effective handler.
    for _ in range(MAX_HANDLER_REMOVE_ATTEMPTS):
        try:
            controller.removeKeyHandler(state["key_handler"])
        except Exception:
            break
    try:
        controller.addKeyHandler(state["key_handler"])
    except Exception:
        pass


def _show_normal_cursor_for_controller(controller):
    if controller is None:
        return
    try:
        cursor = controller.getViewCursor()
        textCursor = cursor.getText().createTextCursorByRange(cursor)
        textCursor.gotoRange(textCursor.getStart(), False)
        moved = textCursor.goRight(1, False)
        if moved:
            textCursor.goLeft(1, True)
        controller.select(textCursor)
    except Exception:
        pass


def _show_insert_cursor_for_controller(controller):
    if controller is None:
        return
    try:
        cursor = controller.getViewCursor()
        textCursor = cursor.getText().createTextCursorByRange(cursor)
        textCursor.gotoRange(textCursor.getStart(), False)
        controller.select(textCursor)
    except Exception:
        pass


class ViewEventListener(unohelper.Base, XEventListener):
    def notifyEvent(self, event):
        state = _state()
        if not state["enabled"] or event is None:
            return
        source = getattr(event, "Source", None)
        if not _is_text_document(source):
            return
        event_name = getattr(event, "EventName", "")
        try:
            if source is None:
                controller = None
            else:
                controller = source.getCurrentController()
        except Exception:
            controller = None
        if event_name == "OnFocus":
            # Do not reattach on every focus change: in Python UNO this can
            # accumulate duplicate callbacks for the same handler.
            _update_statusline(controller)
            if state["mode"] == "NORMAL":
                _show_normal_cursor_for_controller(controller)
            else:
                _show_insert_cursor_for_controller(controller)
        elif event_name == "OnViewCreated":
            _attach_controller(controller)

    def disposing(self, event):
        return None


def _start_view_event_listener():
    _stop_view_event_listener()
    state = _state()
    broadcaster = _global_event_broadcaster()
    if broadcaster is None:
        return
    listener = ViewEventListener()
    try:
        broadcaster.addEventListener(listener)
        state["view_event_listener"] = listener
    except Exception:
        state["view_event_listener"] = None


def _stop_view_event_listener():
    state = _state()
    broadcaster = _global_event_broadcaster()
    listener = state.get("view_event_listener")
    if broadcaster is not None and listener is not None:
        try:
            broadcaster.removeEventListener(listener)
        except Exception:
            pass
    state["view_event_listener"] = None


def _activate_for_current_view():
    state = _state()
    controller = _current_controller()
    if controller is None:
        return
    _update_statusline(controller)
    if state["mode"] == "NORMAL":
        _show_normal_cursor_for_controller(controller)
    else:
        _show_insert_cursor_for_controller(controller)


def _restore_status_for_controller(controller):
    if controller is None:
        return
    try:
        layout = controller.getFrame().LayoutManager
        layout.destroyElement("private:resource/statusbar/statusbar")
        layout.createElement("private:resource/statusbar/statusbar")
    except Exception:
        pass


def _restore_status_all_views():
    for controller in _iter_text_document_controllers():
        _restore_status_for_controller(controller)


def _restore_default_cursor_all_views():
    for controller in _iter_text_document_controllers():
        _show_insert_cursor_for_controller(controller)


def _initialize():
    state = _state()
    state["started"] = True
    # Detach while handler reference is still available.
    _detach_key_handler_from_all_views()
    _state()["key_handler"] = None
    _start_view_event_listener()
    _reinitialize()


def _reinitialize():
    _set_mode("NORMAL")
    _show_normal_cursor()


def _ensure_initialized():
    state = _state()
    if not state["started"]:
        _initialize()
    else:
        _reinitialize()


def _set_vibreoffice_enabled(enable_value):
    state = _state()
    if enable_value == state["enabled"]:
        return
    state["enabled"] = enable_value
    state["swallow_once_insert_press"] = False
    if state["enabled"]:
        state["active_handler_token"] += 1
        state["key_handler"] = KeyHandler(state["active_handler_token"])
        attached = _attach_key_handler_to_all_views()
        # Fallback only when enumeration finds no eligible text views.
        if attached == 0:
            _attach_controller(_current_controller())
        _activate_for_current_view()
    else:
        # Invalidate any stale attached handlers immediately, even if LO keeps
        # old registrations around internally.
        state["active_handler_token"] += 1
        _detach_key_handler_from_all_views()
        state["key_handler"] = None
        _restore_status_all_views()
        _restore_default_cursor_all_views()


def enable_viper_office():
    state = _state()
    state["enable_calls"] += 1
    _dbg(f"ENABLE call#{state['enable_calls']} state={id(state)}")
    _ensure_initialized()
    _set_vibreoffice_enabled(True)


def disable_viper_office():
    state = _state()
    state["disable_calls"] += 1
    _dbg(f"DISABLE call#{state['disable_calls']} state={id(state)}")
    _set_vibreoffice_enabled(False)


def toggle_viper_office():
    state = _state()
    state["toggle_calls"] += 1
    _dbg(f"TOGGLE call#{state['toggle_calls']} enabled_before={state['enabled']} state={id(state)}")
    _ensure_initialized()
    _set_vibreoffice_enabled(not state["enabled"])


g_exportedScripts = (toggle_viper_office, enable_viper_office, disable_viper_office)

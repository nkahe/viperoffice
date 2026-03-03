from __future__ import annotations
from typing import Any, Callable, Final, NamedTuple, TYPE_CHECKING
import builtins
import datetime
import threading
import unohelper   # This project allow typings for the full LibreOffice API.
from functools import lru_cache  # For compiling word specs.
from com.sun.star.awt import KeyModifier, XKeyHandler, Key, Rectangle
from com.sun.star.document import XEventListener

if TYPE_CHECKING:
    from com.sun.star.text import XViewCursor

class KeyEvent(NamedTuple):
    char: str
    code: int
    is_ctrl: bool
    is_escape: bool

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
MAX_HANDLER_REMOVE_ATTEMPTS: Final[int] = 3

def _state():
    key = "_vipereoffice_state"
    state = getattr(builtins, key, None)
    if state is None:
        state = {
            # Has the extension been started. Will only be set to true.
            "started": False,
            "enabled": False,
            # Current vi input mode. Can be currently "normal", "insert",
            # "pending" or "visual". "pending" is short for Operator pending
            # mode. Happens after operator command "d", "c" or "y" and it's
            # pending for motion.
            "mode": "normal",
            # Visible cursor. Type is XViewCursor UNO object.
            "view_cursor": None,
            # An optional number that may precede the command to multiply or
            # iterate the command. Type int.
            "count": 0,
            # Pending commands like 'd' or 'g'. Type str | None. Note that only
            # operator commands result to operator pending mode.
            "pending_keys": None,
            "key_handler": None,
            # Saved cursor position for example position need to be restored
            # after motion.
            "cursor_position": None,
            "view_event_listener": None,
            "global_event_broadcaster": None,
            # Anchor (fixed end) of visual mode selection. Saved when entering
            # visual mode so motions know which end is the caret.
            "visual_anchor": None,
        }
        setattr(builtins, key, state)
    return state


def _get_cursor():
    return _state()["view_cursor"]


def _get_text_cursor():
    cursor = _get_cursor()
    if cursor is None:
        return None
    try:
        return cursor.getText().createTextCursorByRange(cursor)
    except Exception:
        return None


def _set_visual_anchor(anchor) -> None:
    _state()["visual_anchor"] = anchor


def _clear_visual_anchor() -> None:
    _state()["visual_anchor"] = None


def _set_mode(new_mode: str) -> bool:
    if new_mode in ("normal", "insert", "pending", "visual"):
        _state()["mode"] = new_mode
        _update_statusline()
        return True
    else:
        return False

def _get_mode() -> str:
    return _state()["mode"]


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


def _get_pending_keys() -> None|str:
    return _state().get("pending_keys", None)


def _add_pending_key(new_key:str) -> bool:
    pending_keys = _state()["pending_keys"]
    if pending_keys == None:
        _state()["pending_keys"] = new_key
    else:
        _state()["pending_keys"] = pending_keys + new_key

    _update_statusline()
    return True


def _reset_pending_keys():
    _state()["pending_keys"] = None
    _update_statusline()


def _set_position() -> bool:
    """Save current view cursor position"""
    try:
        cursor = _get_cursor()
        _state()["cursor_position"] = cursor.getStart()
        return True
    except Exception:
        return False


def _get_position():
    return _state()["cursor_position"]

# ------------------------
# General helper functions
# ------------------------

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

# For debugging

def _dbg(msg):
    """Log [msg] to log file."""
    if not DEBUG:
        return
    try:
        ts = datetime.datetime.now().strftime("%m-%d %H:%M:%S.%f")
        with open("/tmp/viperffice-debug.log", "a", encoding="utf-8") as f:
            f.write(f"{ts} {msg}\n")
    except Exception:
        pass


def msg(text, title="ViperOffice"):
    """Show [text] in a pop-up window for debugging."""
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


def _debug_cursor_state():
    """Show debug info about view cursor and text cursor ranges. For development use."""
    cursor = _get_cursor()
    if cursor is None:
        msg("No view cursor available.", "ViperOffice cursor debug")
        return
    try:
        text_cursor = _get_text_cursor()
        state = _state()
        lines = [f"Mode: {state['mode']}  pending: {state['pending_keys']}"]

        # View cursor info
        try:
            pos = cursor.getPosition()
            x = pos.X() if callable(pos.X) else pos.X
            y = pos.Y() if callable(pos.Y) else pos.Y
            lines.append(f"ViewCursor pos: X={x}, Y={y}")
        except Exception:
            lines.append("ViewCursor pos: unavailable")
        try:
            vc_start = cursor.getStart()
            vc_end   = cursor.getEnd()
            lines.append(f"ViewCursor collapsed: {cursor.isCollapsed()}")
            lines.append(f"ViewCursor at start of line: {cursor.isAtStartOfLine()}")
        except Exception:
            lines.append("ViewCursor range: unavailable")

        # Text cursor info
        if text_cursor is None:
            lines.append("TextCursor: unavailable")
        else:
            try:
                lines.append(f"TextCursor collapsed: {text_cursor.isCollapsed()}")
                lines.append(f"TextCursor start of paragraph: {text_cursor.isStartOfParagraph()}")
                lines.append(f"TextCursor end of paragraph: {text_cursor.isEndOfParagraph()}")
                lines.append(f"TextCursor start of word: {text_cursor.isStartOfWord()}")
                lines.append(f"TextCursor end of word: {text_cursor.isEndOfWord()}")
                lines.append(f"TextCursor string: {repr(text_cursor.getString()[:40])}")
            except Exception as e:
                lines.append(f"TextCursor info error: {e}")

        msg("\n".join(lines), "ViperOffice cursor debug")
    except Exception as e:
        msg(f"Error: {e}", "ViperOffice cursor debug")


# ------------
# Editor
# ------------

# Functions to manipulate view and model (document).

def _update_statusline(controller=None):
    if controller is None:
        controller = _current_controller()
    if controller is None:
        return
    try:
        state = _state()
        padding = "   "
        if state["mode"] == "pending":
            mode_name = "o-pending"
        else:
            mode_name = state["mode"]
        text = mode_name.upper()
        if _get_raw_count() != 0:
            count_text = _get_count()
            text += f"{padding}{count_text}"
        pendings_keys = _get_pending_keys()
        if pendings_keys is not None:
            text += f"{padding}{pendings_keys}"
        controller.StatusIndicator.start(text, 0)
    except Exception:
        # Non-fatal for status update.
        pass


# Selects character right to cursor as Normal mode cursor.
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


def _get_visual_caret_range(text_cursor):
    """Return the caret (active/moving) end of the visual selection as an XTextRange.

    LibreOffice's getStart()/getEnd() always return left/right ends regardless of
    direction, so we compare against the saved anchor to determine which end is fixed.
    - If anchor == getStart(): forward selection, caret is at getEnd().
    - Otherwise: backward selection, caret is at getStart().
    """
    anchor = _state().get("visual_anchor")
    if anchor is None:
        return text_cursor.getEnd()
    try:
        text = text_cursor.getText()
        probe = text.createTextCursorByRange(text_cursor.getStart())
        probe.gotoRange(anchor, True)
        if len(probe.getString()) == 0:
            return text_cursor.getEnd()   # forward selection
        else:
            return text_cursor.getStart() # backward selection
    except Exception:
        return text_cursor.getEnd()


# Sets mode handling cursor accordingly. In operator pending and visual modes
#  cursor state is saved so it can be used by operator commands.
def _goto_mode(mode_name: str) -> bool:
    if mode_name == "normal":
        _reset_pending_keys()
        # When leaving visual mode, keep cursor at the caret (active) end.
        current_mode = _get_mode()
        if current_mode == "visual":
            controller = _current_controller()
            text_cursor = _get_text_cursor()
            if controller is not None and text_cursor is not None:
                # Use the saved anchor to find the caret end before clearing it.
                caret = _get_visual_caret_range(text_cursor)
                _clear_visual_anchor()
                text_cursor.gotoRange(caret, False)
                # text_cursor.goLeft(1, False)
                controller.select(text_cursor)
            else:
                _clear_visual_anchor()
        _show_normal_cursor()

    elif mode_name == "insert":
        _show_insert_cursor()
        _reset_pending_keys()

    elif mode_name in ("pending", "visual"):
        controller = _current_controller()
        text_cursor = _get_text_cursor()
        if controller is not None and text_cursor is not None:
            text_cursor.gotoRange(text_cursor.getStart(), False)
            if mode_name == "visual":
                # Save current position as anchor before expanding selection.
                _set_visual_anchor(text_cursor.getStart())
                # text_cursor.goRight(1, True)
            controller.select(text_cursor)

        if mode_name == "pending":
            _show_normal_cursor()
    else:
        return False
    _set_mode(mode_name)
    return True


def _focus_findbar() -> bool:
    """Show default LibreOffice find bar. Command '/'. """
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


# Commands 'h', 'j', 'k', 'l'.
def _charwise_motion(cmd:str, count:int, expand:bool) -> bool:
    cursor = _get_cursor()
    if cursor is None:
        return False
    try:
        if cmd == "h":
            return bool(cursor.goLeft(count, expand))
        if cmd == "l":
            return bool(cursor.goRight(count, expand))
        if cmd == "j":
            return bool(cursor.goDown(count, expand))
        if cmd == "k":
            return bool(cursor.goUp(count, expand))
    except Exception:
        return False
    return False


def _copy_and_delete(yank:bool, delete:bool) -> bool:
    """Copy and/or delete selection to clipboard."""
    try:
        if yank == False and delete == False:
            return False
        text_cursor = _get_text_cursor()
        if yank == True:
            dispatcher = _get_dispatcher()
            frame = _get_frame()
            if dispatcher is None or frame is None:
                return False
            dispatcher.executeDispatch(frame, ".uno:Copy", "", 0, ())
        if delete == True:
            if text_cursor is not None:
                text_cursor.setString("")
        return True
    except Exception:
        return False


def _paste(count:int, after_cursor:bool):
    """Paste text from clipboard after or before cursor {count} times."""
    text_cursor = _get_text_cursor()
    cursor = _get_cursor()
    mode = _get_mode()
    if text_cursor is None or cursor is None:
        return False

    try:
        # msg(f"{after_cursor=} {text_cursor.isEndOfParagraph()=}")
        if after_cursor == True and text_cursor.isEndOfParagraph() == False:
            text_cursor.goRight(1, False)

        if mode == "normal":
            text_cursor.gotoRange(text_cursor.getStart(), False)
            controller = _current_controller()
            if controller is None:
                return False
            controller.select(text_cursor)

        dispatcher = _get_dispatcher()
        frame = _get_frame()
        if dispatcher is None or frame is None:
            return False

        for _ in range(count):
            dispatcher.executeDispatch(frame, ".uno:Paste", "", 0, ())

        return True
    except Exception:
        return False


def _to_start_of_line(expand:bool, first_non_blank:bool) -> bool:
    """Motion to start of line. Commands '0' and '^'."""

    cursor = _get_cursor()
    if cursor is None:
        return False
    try:
        if not first_non_blank:
            return bool(cursor.gotoStartOfLine(expand))

        # This variable represents the original line the cursor was on before
        # any of the following changes.
        old_line = cursor.getPosition().Y

        # Select all of the current line and put it into a string.
        cursor.gotoEndOfLine(False)

        if cursor.getPosition().Y > old_line:
            # If gotoEndOfLine moved cursor to next line then move it back.
            cursor.goLeft(1, False)

        cursor.gotoStartOfLine(True)
        text_cursor = _get_text_cursor()
        line_text = cursor.getString()
        cursor.gotoRange(text_cursor, False)
        cursor.gotoStartOfLine(expand)

        i = 0
        while i < len(line_text):
            ch = line_text[i]
            if ch != " " and ch != "\t":
                break
            i += 1

        # Move the cursor to the first non space/tab character.
        if i > 0:
            cursor.goRight(i, expand)
        return True
    except Exception:
        return False


def _to_end_of_line(expand:bool, count:int, pending_keys:str|None=None) -> bool:
    """Motion to end of line. Command '$' """
    cursor = _get_cursor()
    if cursor is None:
        return False
    try:
        if count > 1:
            cursor.goDown(count - 1, expand)
        old_pos = cursor.getPosition()
        cursor.gotoEndOfLine(expand)
        new_pos = cursor.getPosition()

        old_y = getattr(old_pos, "Y", None)
        if callable(old_y):
            old_y = old_y()
        new_y = getattr(new_pos, "Y", None)
        if callable(new_y):
            new_y = new_y()

        if pending_keys is None:
            # LibreOffice can place cursor at next line start; move left back
            # to previous line end unless this was an empty-line no-op.
            if cursor.isAtStartOfLine() and old_y != new_y:
                cursor.goLeft(1, expand)
        return True
    except Exception:
        return False


# NOTE: 'Text container' is not same as document. Can be for example a text frame.
def _to_line(expand:bool, raw_count:int, default_end:bool) -> bool:
    """Go to line [count] motion. Commands 'G' and 'gg'.
    Other args:
    default_end: bool  To default to end of text container if no count given.
                       else default of start of text container.
    """
    cursor = _get_cursor()
    if cursor is None:
        return False
    try:
        if raw_count == 0 and default_end == True:  # Command 'G'
            cursor.gotoEnd(expand)
            return True

        cursor.gotoStart(expand) # Command 'gg'
        if raw_count > 1:
            cursor.goDown(raw_count - 1, expand)
        return True
    except Exception:
        return False


# ISKEYWORD is constant, so compile checker once and reuse for word motions.
@lru_cache(maxsize=1)
def _compile_iskeyword_checker() -> Callable[[str], bool]:
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


def _normalize_motion_range(result, for_operator:bool = False):
    if not isinstance(result, dict):
        return {"moved": False}
    normalized = dict(result)
    normalized["operator_inclusive"] = bool(for_operator and normalized.get("inclusive", False))
    normalized["operator_exclusive"] = bool(for_operator and not normalized.get("inclusive", False))
    return normalized


def _clone_text_range(text_cursor):
    try:
        return text_cursor.getStart()
    except Exception:
        return None


def _range_xy(rng):
    return _pos_xy(rng)


def _query_word_motion(spec, count:int, expand:bool=False):
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
        else:
            # Visual mode: scan from the caret (active/moving) end of the selection.
            # _get_visual_caret_range uses the saved anchor to determine which end
            # is fixed and which is the caret, regardless of selection direction.
            caret = _get_visual_caret_range(text_cursor)
            text_cursor.gotoRange(caret, False)
            if spec.get("direction") == WORD_DIRECTION_BACKWARD:
                # The caret range's right edge is C+1 chars from para start.
                # Backward scans use offset = C (i = offset-1), so step left 1.
                text_cursor.goLeft(1, False)
        if not _word_motion_once(
            text_cursor,
            expand,
            is_keyword_char,
            spec,
        ):
            break
        moved_any = True
        steps_done += 1

    # end_range = _clone_text_range(text_cursor)
    end_range = text_cursor.getEnd() if expand else _clone_text_range(text_cursor)

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


def _apply_motion_result(result, expand:bool) -> bool:
    if not isinstance(result, dict) or not result.get("moved", False):
        return False
    end_range = result.get("end_range")
    cursor = _get_cursor()
    if cursor is None or end_range is None:
        return False
    try:
        cursor.gotoRange(end_range, expand)
    except Exception:
        return False
    return True


def _goto_next_paragraph_with_policy(text_cursor, expand:bool, cross_empty:bool) -> bool:
    if not text_cursor.gotoNextParagraph(expand):
        return False
    if not cross_empty:
        while _is_current_paragraph_empty(text_cursor):
            if not text_cursor.gotoNextParagraph(expand):
                break
    return True


def _goto_previous_paragraph_with_policy(text_cursor, expand:bool, cross_empty:bool) -> bool:
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

    if target == WORD_TARGET_END:
        # ge/gE: skip current word chars backward, then skip blanks,
        # landing on the last char of the previous word.
        cls = classify(paragraph_text[i], is_keyword_char, big_word)
        if cls != "blank":
            while i >= 0 and classify(paragraph_text[i], is_keyword_char, big_word) == cls:
                i -= 1
        while i >= 0 and classify(paragraph_text[i], is_keyword_char, big_word) == "blank":
            i -= 1
        return i if i >= 0 else None

    # Skip trailing blanks when scanning backward.
    while i >= 0 and classify(paragraph_text[i], is_keyword_char, big_word) == "blank":
        i -= 1
    if i < 0:
        return None

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
        move = next_offset
        # e/E are inclusive: include the char at next_offset in the selection.
        if expand and spec.get("target") == WORD_TARGET_END:
            move = next_offset + 1
        if move > 0:
            text_cursor.goRight(move, expand)
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


def _word_motion(
    spec,
    expand: bool,
    count: int = 1,
    pending_keys: str | None = None,
) -> bool:
    """Run a word-motion command (e.g. `w`) and optionally apply an operator.

    Args:
        spec: Word motion specification (direction/target/big_word/etc.).
        expand: If True, keeps selection expanded while moving.
        count: Number of word motions to perform (minimum 1).
        pending_keys: pending keys, or None for plain cursor motion.

    Returns:
        True if cursor moved at least once, otherwise False.
    """
    try:
        if not _validate_word_motion_spec(spec):
            return False
        if expand:
            if pending_keys is not None:
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


def _is_current_paragraph_empty(text_cursor) -> bool:
    if text_cursor is None:
        return False
    try:
        probe = text_cursor.getText().createTextCursorByRange(text_cursor)
        probe.gotoStartOfParagraph(False)
        probe.gotoEndOfParagraph(True)
        return len(probe.getString()) == 0
    except Exception:
        return False


def _sync_view_cursor_to_text_cursor(view_cursor, text_cursor, expand: bool, backward: bool = False):
    if expand and backward:
        edge = text_cursor.getStart()
    elif expand:
        edge = text_cursor.getEnd()
    else:
        edge = text_cursor.getStart()
    view_cursor.gotoRange(edge, expand)


def _to_next_non_empty_paragraph(text_cursor, expand: bool) -> bool:
    moved = False
    while True:
        if not text_cursor.gotoNextParagraph(expand):
            break
        moved = True
        if not _is_current_paragraph_empty(text_cursor):
            break
    return moved


def _to_next_sentence(text_cursor, cursor, expand: bool) -> bool:
    # Implements one ")" motion with paragraph-edge handling.
    old_pos = cursor.getPosition()

    # From an empty line, jump directly to the next non-empty paragraph.
    if _is_current_paragraph_empty(text_cursor):
        moved = _to_next_non_empty_paragraph(text_cursor, expand)
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
                _to_next_non_empty_paragraph(text_cursor, expand)
            else:
                text_cursor.gotoNextParagraph(expand)
        else:
            text_cursor.goRight(1, expand)
            text_cursor.gotoNextSentence(expand)
        _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
    return True


# Repeats ")" motion by count times.
def _sentences_forward(expand: bool, count: int = 1) -> bool:
    text_cursor = _get_text_cursor()
    cursor = _get_cursor()
    if text_cursor is None or cursor is None:
        return False
    try:
        if expand:
            # Collapse to the caret end so forward scan starts from the right place.
            caret = _get_visual_caret_range(text_cursor)
            text_cursor.gotoRange(caret, False)
        steps = max(1, int(count))
        moved_any = False
        for _ in range(steps):
            if not _to_next_sentence(text_cursor, cursor, expand):
                break
            moved_any = True
        return moved_any
    except Exception:
        return False


def _is_at_sentence_start_heuristic(text_cursor) -> bool:
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


def _to_previous_sentence(text_cursor, cursor, expand:bool) -> bool:
    # Implements one "(" motion with sentence-start/paragraph-edge handling.
    old_pos = cursor.getPosition()

    # From inside a sentence, first "(" should go to current sentence start.
    if not _is_at_sentence_start_heuristic(text_cursor):
        text_cursor.gotoStartOfSentence(expand)
        _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand, backward=True)
        return True

    # Paragraph-boundary behavior matching logic.
    if text_cursor.isStartOfParagraph():
        if _is_current_paragraph_empty(text_cursor):
            moved = text_cursor.gotoPreviousParagraph(expand)
            if not moved:
                return False
            while _is_current_paragraph_empty(text_cursor):
                if not text_cursor.gotoPreviousParagraph(expand):
                    _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand, backward=True)
                    return True
            text_cursor.gotoEndOfParagraph(expand)
            if not text_cursor.isStartOfParagraph():
                text_cursor.goLeft(1, expand)
            text_cursor.gotoStartOfSentence(expand)
            _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand, backward=True)
            return True
        else:
            if text_cursor.gotoPreviousParagraph(expand):
                if _is_current_paragraph_empty(text_cursor):
                    _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand, backward=True)
                    return True
                text_cursor.gotoEndOfParagraph(expand)
                if not text_cursor.isStartOfParagraph():
                    text_cursor.goLeft(1, expand)
                text_cursor.gotoStartOfSentence(expand)
                _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand, backward=True)
                return True

    text_cursor.gotoPreviousSentence(expand)
    if _same_pos(old_pos, cursor.getPosition()):
        if text_cursor.goLeft(1, expand):
            text_cursor.gotoPreviousSentence(expand)
        _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand, backward=True)
    return True


# Repeats '(' motion by count times.
def _sentences_backwards(expand: bool, count: int = 1) -> bool:
    text_cursor = _get_text_cursor()
    cursor = _get_cursor()
    if text_cursor is None or cursor is None:
        return False
    try:
        if expand:
            # In visual mode, scan from the caret (active) end, not the anchor.
            caret = _get_visual_caret_range(text_cursor)
            text_cursor.gotoRange(caret, False)
        steps = max(1, int(count))
        moved_any = False
        for _ in range(steps):
            if not _to_previous_sentence(text_cursor, cursor, expand):
                break
            moved_any = True
        return moved_any
    except Exception:
        return False


def _delete_characters(count:int, key, mode:str) -> bool:
    """Delete characters. Commands 'x','X' and 's'."""
    text_cursor = _get_text_cursor()
    if text_cursor is None:
        return False
    try:
        if mode != "visual":
            text_cursor.gotoRange(text_cursor.getStart(), False)
            if key.char == "X":
                text_cursor.collapseToStart()
                # At start of line
                if not text_cursor.goLeft(count, True):
                    return False
            # At end of line
            elif not text_cursor.goRight(count, True):
                return False

        elif key.char == "X":
            # X in visual mode: expand selection to cover full visual lines.
            # gotoStartOfLine/gotoEndOfLine are view cursor methods, so use
            # the view cursor to navigate to each end of the selection first.
            cursor = _get_cursor()
            if cursor is None:
                return False
            sel_start = text_cursor.getStart()
            sel_end   = text_cursor.getEnd()
            cursor.gotoRange(sel_start, False)
            cursor.gotoStartOfLine(False)
            line_start = cursor.getStart()
            cursor.gotoRange(sel_end, False)
            cursor.gotoEndOfLine(False)
            line_end = cursor.getStart()
            text_cursor.gotoRange(line_start, False)
            text_cursor.gotoRange(line_end, True)

        text_cursor.setString("")

        if key.char == "s":
            _switch_to_insert("i")
        elif mode == "visual":
            _goto_mode("normal")
        return True
    except Exception:
        return False


# Commmand 'Esc',
def _leave_insert_to_normal():
    if _get_mode() == "normal":
        return True
    cursor = _get_cursor()
    if cursor is not None:
        try:
            if not cursor.isAtStartOfLine():
                cursor.goLeft(1, False)
        except Exception:
            pass
    return _goto_mode("normal")


def _switch_to_insert(cmd:str):
    """For Normal mode commands 'a', 'I', 'A', 'o', 'O'."""
    try:
        if cmd == "a" or cmd == "A":
            textCursor = _get_text_cursor()
            cursor = _get_cursor()
            if cmd == "A":
                _to_end_of_line(False, 1, None)
            elif textCursor is not None and not textCursor.isEndOfParagraph():
                 cursor.goRight(1, False)

        elif cmd == "I":
            _to_start_of_line(False, True)

        elif cmd == "o":
            cursor = _get_cursor()
            if cursor is not None:
                _to_end_of_line(False, 0, None)
                cursor.goRight(1, False)
                cursor.setString(chr(13))  # CR
                if not cursor.isAtStartOfLine():
                    cursor.setString(chr(13) + chr(13))
                    cursor.goRight(1, False)

        elif cmd == "O":
            _to_start_of_line(False, False)
            cursor = _get_cursor()
            if cursor is not None:
                cursor.setString(chr(13))  # CR
                if not cursor.isAtStartOfLine():
                    cursor.goLeft(1, False)
                    cursor.setString(chr(13) + chr(13))
                    cursor.goRight(1, False)

    except Exception:
        pass
    _goto_mode("insert")


def _undo_and_redo(count=1, redo=False) -> bool:
    """Undo or redo changes. Commands 'u' and 'C-r'. """
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


def _scroll_window(count:int, forward:bool, halfpage=False) -> bool:
    """Scroll window by one page. Commands 'C-f' (forward) and 'C-b' (backward).
    """
    try:
        cursor = _get_cursor()
        if cursor is None:
            return False
        if halfpage:
            pass
            return False
        else:
            if forward:
                for _ in range(count):
                    cursor.screenDown()
            else:
                for _ in range(count):
                    cursor.screenUp()
            return True
    except Exception:
        return False


def _jump_to_page(expand: bool, target: str, pending_keys: str | None) -> bool:
    """Motion to start or end of a page. Commands 'H' and 'L'."""
    try:
        cursor = _get_cursor()
        if cursor is None:
            return False
        if target == "start":
            if expand:
                anchor = cursor.getStart()
                cursor.jumpToStartOfPage()
                new_pos = cursor.getStart()
                cursor.gotoRange(anchor, False)
                cursor.gotoRange(new_pos, True)
            else:
                cursor.jumpToStartOfPage()
            return True
        elif target == "end":
            if expand:
                anchor = cursor.getStart()
                cursor.jumpToEndOfPage()
                new_pos = cursor.getStart()
                cursor.gotoRange(anchor, False)
                cursor.gotoRange(new_pos, True)
            else:
                cursor.jumpToEndOfPage()
        return False
    except Exception:
        return False


def _delete_and_replace(count:int, pending_keys, key_char:str, mode:str) -> bool:
    """Delete text {motion} moves over. Commands: 'd', 'dd', 'D', 'c', 'C', 'S'"""
    if key_char in ("C", "D", "S"):
        cursor = _get_cursor()
        text_cursor = _get_text_cursor()
        if cursor is None or text_cursor is None:
            return False
        # collapse to pos 0 (char under cursor)
        cursor.gotoRange(text_cursor.getStart(), False)
        _to_end_of_line(True, count, None)
        _copy_and_delete(True, True)
        if key_char in ("C", "S"):
            _goto_mode("insert")
        else:
            _goto_mode("normal")
        return True

    if mode == "normal" and pending_keys is None:
        if key_char in ("c", "d"):
            _add_pending_key(key_char)
            _goto_mode("pending")
            return True
        return False

    # Linewise delete/replace 'dd' and 'cc'.
    if pending_keys in ("c", "d") and key_char == pending_keys:
        _to_start_of_line(False, False)
        _charwise_motion("j", count, True)

    _copy_and_delete(True, True)

    if key_char == 'c' or pending_keys is not None and pending_keys[0] == "c":
        _goto_mode("insert")
    else:
        _goto_mode("normal")
    return True


def _yank(count, pending_keys:str|None, key_char:str, mode) -> bool:
    """Yanks text {motion} moves over. Commands `y`, `yy`, `Y`."""
    # msg(f"d-command: {pending_keys=} {key_char}")
    if key_char == "Y":
        cursor = _get_cursor()
        text_cursor = _get_text_cursor()
        if cursor is None or text_cursor is None:
            return False
        # collapse to pos 0 (char under cursor)
        cursor.gotoRange(text_cursor.getStart(), False)
        _to_end_of_line(True, count, None)
        _copy_and_delete(True, False)
        return True

    if mode == "normal" and pending_keys is None:
        _set_position()
        _add_pending_key("y")
        _goto_mode("pending")
        return True

    # Linewise yanking 'yy'.
    elif pending_keys == "y" and key_char == "y":
            _to_start_of_line(False, False)
            _charwise_motion("j", count, True)

    _copy_and_delete(True, False)
    position = _get_position()
    cursor = _get_cursor()
    if (position is not None and cursor is not None) and mode != "visual":
        # Keep yanked range visually selected briefly, then restore cursor.
        # Use _set_mode instead of _goto_mode so the selection isn't
        # collapsed immediately by _show_normal_cursor().
        def _flash_restore():
            cursor.gotoRange(position, False)
            _show_normal_cursor()
        threading.Timer(0.08, _flash_restore).start()

    _reset_pending_keys()
    _set_mode("normal")
    return True


def _replace_character(count:int, pending_keys, key_char:str) -> bool:
    """Replace character(s) under cursor with {key_char}.
       With {count} replace {count} characters with {count} {key_char}.
    """
    if pending_keys is None:
        _add_pending_key("r")
        return True

    _reset_pending_keys()
    try:
        cursor = _get_cursor()
        length = len(cursor.getString())

        if length > 1:
            cursor.setString(key_char * length)
        else:
            cursor.setString(key_char * count)
        return True
    except Exception:
        return False


# --------------
# Input handling
# --------------

# UNO key handler
# Return values for keyPressed/keyReleased:
#   True  -> event is swallowed (LibreOffice should not process it)
#   False -> event is passed through to LibreOffice default handling
class KeyHandler(unohelper.Base, XKeyHandler):

    def __init__(self):
        pass

    # Consume {action} and after that {post_action} if set.
    def _consume_action(self, action, post_action=None) -> bool:
        if action is not None:
            action()
            # Can't be passed as parameter since that might been updated.
            pending_keys = _get_pending_keys()
            # msg(f"consume_active: {pending_keys=})
            if pending_keys is None:
                _reset_count()
            if post_action is not None:
                post_action()
                _reset_count()
        return True

    @staticmethod
    def _normal_ctrl_actions(count):
        b_code = int(getattr(Key, "B", 512))
        c_code = int(getattr(Key, "C", 514))
        d_code = int(getattr(Key, "D", 515))
        f_code = int(getattr(Key, "F", 517))
        r_code = int(getattr(Key, "R", 529))
        u_code = int(getattr(Key, "U", 532))
        actions = {
            c_code: lambda: _reset_pending_keys(),
            b_code: lambda: _scroll_window(count, False, False),
            f_code: lambda: _scroll_window(count, True, False),
            d_code: lambda: _scroll_window(count, True, True),
            u_code: lambda: _scroll_window(count, False, True),
            r_code: lambda: _undo_and_redo(count, False),
        }
        return actions

    @staticmethod
    def _normal_actions(key, count:int, pending_keys, expand):
        """Build Normal-mode command dispatch map for actions."""
        mode = _get_mode()
        # Available commands after "g" command.
        if "g" in (pending_keys or ""):
            actions = {
            }
        else:
            # "j" and "k" are here since operators don't support them.
            actions = {
                "c": lambda: _delete_and_replace(count, pending_keys, key.char, mode),
                "C": lambda: _delete_and_replace(count, pending_keys, key.char, mode),
                "d": lambda: _delete_and_replace(count, pending_keys, key.char, mode),
                "D": lambda: _delete_and_replace(count, pending_keys, key.char, mode),
                "g": lambda: KeyHandler._g_command(pending_keys),
                "j": lambda: _charwise_motion("j", count, expand),
                "k": lambda: _charwise_motion("k", count, expand),
                "i": lambda: _goto_mode("insert"),
                "I": lambda: _switch_to_insert("I"),
                "a": lambda: _switch_to_insert("a"),
                "A": lambda: _switch_to_insert("A"),
                "o": lambda: _switch_to_insert("o"),
                "O": lambda: _switch_to_insert("O"),
                "p": lambda: _paste(count, True),
                "P": lambda: _paste(count, False),
                "r": lambda: _replace_character(count, pending_keys, key.char),
                "u": lambda: _undo_and_redo(count),
                "U": lambda: _undo_and_redo(count, True),
                "s": lambda: _delete_characters(count, key, mode),
                "S": lambda: _delete_and_replace(count, pending_keys, key.char, _get_mode()),
                "x": lambda: _delete_characters(count, key, mode),
                "X": lambda: _delete_characters(count, key, mode),
                "y": lambda: _yank(count, pending_keys, key.char, mode),
                "Y": lambda: _yank(count, pending_keys, key.char, mode),
                "v": lambda: _goto_mode("visual"),
                "/": _focus_findbar,
            }
        return actions

    # These can be used independently or with operators.
    @staticmethod
    def _motions(key, expand, count:int, pending_keys):
        """Build motion dispatch map for actions."""
        # Available motions after "g" command.
        if "g" in (pending_keys or ""):
            motions = {
                "g": lambda: _to_line(expand, _get_raw_count(), False),
                "e": lambda: _word_motion(_WORD_MOTION_GE, expand, count, pending_keys),
                "E": lambda: _word_motion(_WORD_MOTION_G_BIG_E, expand, count, pending_keys)
            }
        else:
            motions = {
                "h": lambda: _charwise_motion("h", count, expand),
                "l": lambda: _charwise_motion("l", count, expand),
                "w": lambda: _word_motion(_WORD_MOTION_W, expand, count, pending_keys),
                "W": lambda: _word_motion(_WORD_MOTION_BIG_W, expand, count, pending_keys),
                "e": lambda: _word_motion(_WORD_MOTION_E, expand, count, pending_keys),
                "E": lambda: _word_motion(_WORD_MOTION_BIG_E, expand, count, pending_keys),
                "b": lambda: _word_motion(_WORD_MOTION_B, expand, count, pending_keys),
                "B": lambda: _word_motion(_WORD_MOTION_BIG_B, expand, count, pending_keys),
                "$": lambda: _to_end_of_line(expand, count, pending_keys),
                "^": lambda: _to_start_of_line(expand, True),
                "H": lambda: _jump_to_page(expand, "start", pending_keys),
                "L": lambda: _jump_to_page(expand, "end", pending_keys),
                "G": lambda: _to_line(expand, _get_raw_count(), True),
                ")": lambda: _sentences_forward(expand, count),
                "(": lambda: _sentences_backwards(expand, count),
            }
            if key.char == "0" and _get_raw_count() == 0:
                motions["0"] = lambda: _to_start_of_line(expand, False)

        return motions

    # ------------------------------------------
    def keyPressed(self, event):
        state = _state()
        if not state["enabled"]:
            return False

        # Don't do anything if text cursor isn't working (as in annotations).
        if _get_text_cursor() is None:
            return False

        mode: str = _get_mode()
        mods: int = _event_modifiers(event)
        _code = _key_code(event)
        _is_ctrl = _is_only_ctrl(mods)
        key = KeyEvent(
            char=_normalize_key_char(event),
            code=_code,
            is_ctrl=_is_ctrl,
            is_escape=_is_escape(_code, _is_ctrl),
        )
        # msg(f"{expand=} {pending_keys=} {key=}")

        # Insert mode commands matching.
        if mode == "insert":
            if key.is_escape or (key.is_ctrl and key.code == 514):  # C-c
                return self._consume_action(_leave_insert_to_normal)
            return False

        pending_keys: str | None = _get_pending_keys()
        count: int = _get_count()

        if pending_keys == "r":
            if key.char.isprintable() or key.code in (1280, 1282):  # enter, tab
                return self._consume_action(
                    lambda: _replace_character(count, pending_keys, key.char)
                )
            _reset_pending_keys()
            return True

        # Count parsing
        # - 1..9 always extend count
        # - 0 extends count only after count has started
        # Commands that can take count should be before this.
        if _is_digit_char(key.char):
            if key.char != "0" or _get_raw_count() > 0:
                _add_to_count(int(key.char))
                return self._consume_action(None)

        # msg(f"mods: {mods}\nkey: {key}\n is_only_ctrl: {key['is_ctrl']}")

        if key.is_ctrl:
            actions = self._normal_ctrl_actions(count)
            action = actions.get(key.code)
            if action is not None:
                return self._consume_action(action)
            else:
                return False

        # Pass other non-shift modified shortcuts through, except characters
        # made with AltGr.
        if _has_non_shift_modifier(event):
            if not bool(_is_altgr_char(event, key.char, key.code)):
                return False

        # ----- Keys without modifiers after this ----

        # Match and handle motions that support operators.
        matched_motions = self._match_motions(key, count, pending_keys)
        if matched_motions is not None:
            return matched_motions

        # Match and handle non-motion commands.
        matched_commands = self._match_commands(key, count, pending_keys)
        if matched_commands is not None:
            return matched_commands

        # No suitable commands matched for "g" so cancel.
        if "g" in (pending_keys or ""):
            _reset_count()
            _goto_mode("normal")
            return True

        # Before count parsing.
        if _is_backspace_key(event):
            return self._consume_action(lambda: _charwise_motion("h", count, False))

        # ----- Non-character keys -----

        if _is_function_key(event):
            return False

        # Now suitable key matched so reset.
        _reset_count()

        if pending_keys:  # Cancel rest of keys since no match.
            _reset_pending_keys()
            _set_mode("normal")
            return True

        # TODO: bind navigation keys to movement functions so count can be used.
        if _is_navigation_key(event):
            if pending_keys is not None:
                _reset_pending_keys()
                _set_mode("normal")
            return False
        if key.is_escape:
            return self._consume_action(lambda: _goto_mode("normal"))
        # Deliberately cancels operator pending mode.
        if _is_del_key(event):
            return self._consume_action(lambda: _delete_characters(count, "x", mode))
        if _is_insert_key(event):
            return self._consume_action(lambda: _switch_to_insert("i"))

        return self._consume_action(None)
    # -----------------------------------------

    def _match_motions(self, key, count, pending_keys):
        expand: bool = _get_mode() in ("visual", "pending")
        motions = self._motions(key, expand, count, pending_keys)
        motion = motions.get(key.char)
        if motion is None:
            return None
        # If operator is pending, add it to be done after motion.
        if "c" in (pending_keys or "") or "d" in (pending_keys or ""):
            return self._consume_action(motion,
                lambda: _delete_and_replace(count, pending_keys, key.char, _get_mode())
            )
        elif "y" in (pending_keys or ""):
            return self._consume_action(motion,
                lambda: _yank(count, pending_keys, key.char, _get_mode())
            )
        elif "g" in (pending_keys or ""):
            return self._consume_action(motion, lambda: _reset_pending_keys())
        return self._consume_action(motion)

    def _match_commands(self, key, count, pending_keys):
        expand: bool = _get_mode() in ("visual", "pending")
        normal_actions = self._normal_actions(key, count, pending_keys, expand)
        action = normal_actions.get(key.char)
        if action is None:
            return None
        if pending_keys in ("c", "d", "y"):
            if key.char == pending_keys:        # dd, yy, cc
                return self._consume_action(action)
            if key.char == "g":                 # dg, yg, cg
                return self._consume_action(action)
            # non-operator key while pending: cancel
            _reset_pending_keys()
            _set_mode("normal")
            return None
        return self._consume_action(action)

    @staticmethod
    def _g_command(pending_keys:str|None):
        if pending_keys in (None, "d", "y", "c"):
            _add_pending_key("g")
            return True

    def keyReleased(self, event):
        state = _state()
        if not state["enabled"]:
            return False
        # Keep normal cursor rendering for navigation releases, including
        # Ctrl+Home/Ctrl+End where Ctrl would otherwise short-circuit below.
        if state["mode"] == "normal" and _is_navigation_key(event):
            _show_normal_cursor()
            return False
        if state["mode"] == "normal" and _is_function_key(event):
            return False
        if state["mode"] == "normal":
            _show_normal_cursor()
            return True
        return False

    def disposing(self, event):
        return None


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
            # would be treated as lowercase normal-mode commands.
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


def _is_altgr_char(event, key_char, key_code) -> bool:
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


def _is_del_key(event):
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
            if controller is not None:
                _state()["view_cursor"] = controller.getViewCursor()
            _update_statusline(controller)
            _reset_count()
            if state["mode"] == "normal":
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
    _state()["view_cursor"] = controller.getViewCursor()
    _update_statusline(controller)
    if state["mode"] == "normal":
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
    # Detach any previously registered handler before creating a new one.
    _detach_key_handler_from_all_views()
    state["key_handler"] = KeyHandler()
    _attach_key_handler_to_all_views()
    _start_view_event_listener()
    enable_viper_office()


def enable_viper_office():
    """Enable ViperOffice"""
    state = _state()
    if not state["started"]:
        _initialize()
    state["enabled"] = True
    _activate_for_current_view()
    controller = _current_controller()
    if controller is not None:
        state["view_cursor"] = controller.getViewCursor()
    _set_mode("normal")
    _show_normal_cursor()


def disable_viper_office():
    """Disable ViperOffice"""
    state = _state()
    state["enabled"] = False
    _restore_status_all_views()
    _restore_default_cursor_all_views()


def toggle_viper_office():
    """Toggle enabling of ViperOffice"""
    state = _state()
    if state["enabled"] is True:
        disable_viper_office()
    else:
        enable_viper_office()


g_exportedScripts = (toggle_viper_office, enable_viper_office, disable_viper_office)

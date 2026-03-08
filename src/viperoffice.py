from __future__ import annotations
from typing import Any, Final, NamedTuple, TYPE_CHECKING
import builtins
import datetime
import threading
import unohelper   # This project allow typings for the full LibreOffice API.
from com.sun.star.awt import KeyModifier, XKeyHandler, Key, Rectangle
from com.sun.star.awt import XMouseClickHandler
from com.sun.star.document import XEventListener

if TYPE_CHECKING:
    from com.sun.star.text import XViewCursor

class KeyEvent(NamedTuple):
    char: str
    code: int
    pending: str | None


# ------------
# Global state
# ------------

# Provided by LibreOffice's Python macro runtime.
if "XSCRIPTCONTEXT" not in globals():
    XSCRIPTCONTEXT: Any = None

DEBUG = False

# Additional characters which are considered to be a part of word for word
# motions w, b, e, ge. Alphabets are always included. "chars" are written
# together like: "chars": "_,."
ISWORD: Final[dict] = {
    "digits": True,
    "chars": "_",
}

# Retry limit when detaching key handlers to avoid stale-UNO handler buildup.
MAX_HANDLER_REMOVE_ATTEMPTS: Final[int] = 3

# How many lines should C-d and C-u scroll.
SCROLL: Final[int] = 21
# Guard for paragraph scans to avoid malformed cursor loops freezing the UI.
PARAGRAPH_SCAN_LIMIT: Final[int] = 10000

def _state():
    key = "_viperoffice_state"
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
            # Saved snapshot of cursor position in situations when the original
            # position need to be restored after motion.
            "cursor_position": None,
            "view_event_listener": None,
            "global_event_broadcaster": None,
            "mouse_listener": None,
            # Anchor (fixed end) of visual mode selection. Saved when entering
            # visual mode so motions know which end is the caret.
            "visual_anchor": None,
            "mouse_press_anchor": None
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
    new_mode = new_mode.lower()
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
    if pending_keys is None:
        _state()["pending_keys"] = new_key
    else:
        _state()["pending_keys"] = pending_keys + new_key
    _update_statusline()
    return True


def _reset_pending_keys():
    _state()["pending_keys"] = None
    _update_statusline()

def _reset_prefix():
    """Reset "a" or "i" text-object prefix."""
    pending_keys = _state()["pending_keys"]
    if pending_keys is None or pending_keys not in ("a", "i"):
        return False
    _state()["pending_keys"] = pending_keys[:-1]


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


def _get_scroll() -> int:
    """Lines to scroll with C-b and C-u commands."""
    default = 20
    try:
        lines_to_scroll = int(SCROLL)
        if 1 <= lines_to_scroll <= 99:
            return lines_to_scroll
    except Exception:
        pass
    return default


# -----------------
# Utility functions
# -----------------

def _current_doc():
    try:
        return XSCRIPTCONTEXT.getDocument()
    except Exception:
        return None


def _get_controller():
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
        controller = _get_controller()
        if controller is None:
            return None
        frame = controller.getFrame()
        return frame
    except Exception:
        return None

# For debugging

def _dbg(msg):  # noqa: F811  # pyright: ignore[reportUnusedFunction]
    """Log [msg] to log file."""
    if not DEBUG:
        return
    try:
        ts = datetime.datetime.now().strftime("%m-%d %H:%M:%S.%f")
        with open("/tmp/viperffice-debug.log", "a", encoding="utf-8") as f:
            f.write(f"{ts} {msg}\n")
    except Exception:
        pass


def msg(text, title="ViperOffice"): # noqa: F811  # pyright: ignore[reportUnusedFunction]
    """Show [text] in a pop-up window for debugging."""
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
        except Exception:
            # Newer UNO signature used by some versions.
            box = toolkit.createMessageBox(
                parent, 1, 1, title, str(text),
            )
        box.execute()
    except Exception:
        pass


def debug_cursor_state():  # noqa: F811  # pyright: ignore[reportUnusedFunction]
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


# ------------------
# UI and input modes
# ------------------

# Functions to manipulate view and model (document).

def _update_statusline(controller=None):
    if controller is None:
        controller = _get_controller()
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


# Sets cursor style and save cursor position info.
def _show_cursor(mode:str):
    text_cursor = _get_text_cursor()
    cursor = _get_cursor()
    controller = _get_controller()
    if text_cursor is None or controller is None:
        return False
    mode = mode.lower()
    try:
        if mode in ("normal", "pending"):
            # Select 1 character right side of caret as Normal mode cursor.
            text_cursor.gotoRange(text_cursor.getStart(), False)
            moved = text_cursor.goRight(1, False)
            if moved:
                text_cursor.goLeft(1, True)

        elif mode == "visual":
            # Coming from Normal mode (1-char cursor): collapse and re-select so
            # anchor and caret are known.
            if len(cursor.getString()) == 1:
                text_cursor.gotoRange(text_cursor.getStart(), False)
                _set_visual_anchor(text_cursor.getStart())
                text_cursor.goRight(1, True)
            # else:
                # Mouse selection: use the saved press position as anchor.
                # press_anchor = _state().pop("mouse_press_anchor", None)
                # if press_anchor is not None:
                #     _set_visual_anchor(press_anchor)
        elif mode == "insert":
            # Use collapsed cursor.
            text_cursor.gotoRange(text_cursor.getStart(), False)
        else:
            return False

        controller.select(text_cursor)
    except Exception:
        return False


# Sets mode handling cursor accordingly. In operator pending and visual modes
#  cursor state is saved so it can be used by operator commands.
def _goto_mode(new_mode: str) -> bool:
    current_mode = _get_mode()
    if new_mode == "normal":
        _reset_pending_keys()
        if current_mode == new_mode:
            return True
        if current_mode == "insert":
            cursor = _get_cursor()
            if cursor is not None and not cursor.isAtStartOfLine():
                # Mimics Vi/Vim cursor behavior.
                cursor.goLeft(1, False)

        # Make selection start where caret is in Normal mode.
        elif current_mode == "visual":
            controller = _get_controller()
            text_cursor = _get_text_cursor()
            if controller is not None and text_cursor is not None:
                # Use the saved anchor to find the caret end before clearing it.
                caret = _get_visual_caret_range(text_cursor)
                _clear_visual_anchor()
                text_cursor.gotoRange(caret, False)
                text_cursor.goLeft(1, False)
                controller.select(text_cursor)
            else:
                _clear_visual_anchor()

        _show_cursor("normal")

    elif new_mode == "insert":
        _reset_pending_keys()
        _show_cursor("insert")

    elif new_mode == "visual":
        _reset_pending_keys()
        _show_cursor("visual")

    elif new_mode == "pending":
        _show_cursor("pending")
    else:
        return False
    _set_mode(new_mode)
    return True


def _ctrl_c_command(mode:str):
    if mode == "normal":
        _reset_pending_keys()
    elif mode == "visual":
        _copy_and_delete(True, False)
    _goto_mode("normal")


# --------------------
# Cursor and selection
# --------------------

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
    except Exception:
        return 0


def _range_starts_before(range_a, range_b) -> bool:
    if range_a is None or range_b is None:
        return False
    try:
        text = range_a.getText()
        # compareRegionStarts returns 1 when range_a starts before range_b.
        return text.compareRegionStarts(range_a, range_b) == 1
    except Exception:
        return False


def _range_ends_before(range_a, range_b) -> bool:
    if range_a is None or range_b is None:
        return False
    try:
        text = range_a.getText()
        return text.compareRegionEnds(range_a, range_b) == 1
    except Exception:
        return False


def _try_go_left(cursor, distance: int) -> bool:
    if cursor is None or distance <= 0:
        return False
    moved = False
    hide_cursor = False
    try:
        visible_before = None
        try:
            visible_before = cursor.isVisible()
        except Exception:
            visible_before = None
        if visible_before:
            cursor.setVisible(False)
            hide_cursor = True
        moved = cursor.goLeft(distance, True)
    except Exception:
        moved = False
    finally:
        if hide_cursor:
            cursor.setVisible(True)
    return moved


def _try_go_right(cursor, distance: int) -> bool:
    if cursor is None or distance <= 0:
        return False
    moved = False
    hide_cursor = False
    try:
        visible_before = None
        try:
            visible_before = cursor.isVisible()
        except Exception:
            visible_before = None
        if visible_before:
            cursor.setVisible(False)
            hide_cursor = True
        moved = cursor.goRight(distance, True)
    except Exception:
        moved = False
    finally:
        if hide_cursor:
            cursor.setVisible(True)
    return moved


def _ensure_visual_caret(cursor, at_end: bool) -> None:
    """Ensure view cursor caret is on the requested selection end without changing selection."""
    if cursor is None:
        return
    try:
        if at_end:
            cursor.gotoRange(cursor.getEnd(), True)
        else:
            cursor.gotoRange(cursor.getStart(), True)
    except Exception:
        pass


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


def _sync_view_cursor_to_text_cursor(view_cursor, text_cursor, expand: bool, backward: bool = False):
    if expand and backward:
        edge = text_cursor.getStart()
    elif expand:
        edge = text_cursor.getEnd()
    else:
        edge = text_cursor.getStart()
    anchor = _state().get("visual_anchor") if expand else None
    if anchor is not None:
        # Visual mode: use _set_visual_selection so direction changes work correctly.
        _set_visual_selection(view_cursor, anchor, edge)
    else:
        view_cursor.gotoRange(edge, expand)


def _set_visual_selection(cursor, anchor, new_caret):
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
            except Exception:
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
            except Exception:
                current_left = None
            prev_length = _range_length_between(current_left, anchor) if current_left is not None else 0
            new_length = _range_length_between(new_caret, anchor)
            if current_left is not None and _range_starts_before(new_caret, current_left):
                delta = prev_length - new_length
                if delta > 0 and _try_go_left(cursor, delta):
                    return

            cursor.gotoRange(anchor, False)
            distance = _range_length_between(new_caret, anchor)
            if _try_go_left(cursor, distance):
                return

        # Fallback: collapse directly to the requested caret range.
        cursor.gotoRange(new_caret, True)
    except Exception:
        pass


# Based on Commit f33d46f from fedorov-ao/vibreoffice
def _go_to_other_end(mode: str) -> bool:
    """Move cursor to the other end of highlighted text. Command 'o' / 'O' in visual mode.

    The current cursor position becomes the start of the highlighted text and
    the cursor is moved to the other end of the highlighted text. The highlighted
    area remains the same.
    """
    if mode != "visual":
        return False
    cursor = _get_cursor()
    if cursor is None:
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


# ----------------------
# Navigating in document
# ----------------------

def _scroll_window(expand:bool, count:int, forward:bool, mode:str, lines:int|None=None) -> bool:
    """Scroll window. Commands 'C-f', 'C-b', 'C-u', 'C-d'.
    """
    try:
        cursor = _get_cursor()
        if cursor is None:
            return False
        if lines:
            if forward:
                for _ in range(count):
                    _hjkl_motion("j", lines, expand, mode)
            else:
                for _ in range(count):
                    _hjkl_motion("k", lines, expand, mode)
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
    except Exception:
        return False


def _to_line(expand:bool, raw_count:int, default_end:bool) -> bool:
    """Go to line [count] motion. Commands 'G' and 'gg'.
    Args:

    expand: bool       Expand selection
    raw_count: int     Move to line [count].
    default_end: bool  To default to end of text document if no count given.
                       else default of start of text document.
    """
    cursor = _get_cursor()
    if cursor is None:
        return False
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
        return True
    except Exception:
        return False


def _jump_to_page(expand: bool, target: str, count:int=1) -> bool:
    """Motion to start or end of a page. Commands 'H' and 'L'."""
    target = target.lower()
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
        elif target == "end":
            if expand:
                anchor = cursor.getStart()
                cursor.jumpToEndOfPage()
                new_pos = cursor.getStart()
                cursor.gotoRange(anchor, False)
                cursor.gotoRange(new_pos, True)
            else:
                cursor.jumpToEndOfPage()
        elif target == "next":
            if expand:
                anchor = cursor.getStart()
                cursor.jumpToNextPage()
                new_pos = cursor.getStart()
                cursor.gotoRange(anchor, False)
                cursor.gotoRange(new_pos, True)
            else:
                cursor.jumpToNextPage()
        elif target == "previous":
            if expand:
                anchor = cursor.getStart()
                cursor.jumpToPreviousPage()
                new_pos = cursor.getStart()
                cursor.gotoRange(anchor, False)
                cursor.gotoRange(new_pos, True)
            else:
                cursor.jumpToPreviousPage()
        else:
            return False
        return True
    except Exception:
        return False


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


# ------------------
# Lines
# ------------------

def _hjkl_motion(cmd:str, count:int, expand:bool, mode) -> bool:
    """Motion to left/right [count] characters for commands 'h' and 'l' and
    [count] lines up and down for commands 'j' and 'k'.
    """
    cursor = _get_cursor()
    if cursor is None:
        return False
    try:
        if cmd == "h":
            if mode == "pending":
                cursor.collapseToStart()
            return bool(cursor.goLeft(count, expand))
        if cmd == "l":
            if mode == "pending":
                count += 1
            return bool(cursor.goRight(count, expand))
        if cmd == "j":
            if mode == "pending":
                _to_start_of_line(False, False)
                count += 1
            return bool(cursor.goDown(count, expand))

        if cmd == "k":
            if mode == "pending":
                _to_end_of_line(False, 1)
                # At a soft-wrap point the inter-word space sits at the start of
                # the next visual line. Step past it so the selection includes it
                # and doesn't get left behind as a leading space after deletion.
                tc = _get_text_cursor()
                if tc is not None and not tc.isEndOfParagraph():
                    cursor.goRight(1, False)
                _to_start_of_line(True, False)
                count += 1
            return bool(cursor.goUp(count, expand))

    except Exception:
        return False
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


def _to_end_of_line(expand:bool, count:int, key=None) -> bool:
    """Motion to end of line and optionally [count -1 ] lines down.
       Command '$'. """
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

        # msg("f{key.pending=}")

        if key is not None and key.pending is None:
            # LibreOffice can place cursor visually at next line start; move left
            # back to previous line end unless this was an empty-line no-op.
            if cursor.isAtStartOfLine() and old_y != new_y:
                cursor.goLeft(1, expand)
        return True
    except Exception:
        return False


def _delete_and_replace_lines(key:KeyEvent):
    """Delete/replaces lines which have selection. Commands 'S' and in visual mode 'X'."""
    text_cursor = _get_text_cursor()
    if text_cursor is None:
        return False
    try:
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

        if key.char == "S":
            _insert_commands("i")
        else:
            _goto_mode("normal")
        return True
    except Exception:
        return False


# ------------------
# Character editing
# ------------------

# Insert, delete, replace characters

def _insert_commands(cmd:str, mode="normal"):
    """For Normal mode commands 'a', 'I', 'A', 'o', 'O'."""
    try:
        cursor = _get_cursor()
        if cursor is None:
            return False

        if cmd == "a" or cmd == "A":
            textCursor = _get_text_cursor()
            if cmd == "A":
                if mode == "visual":
                    if cursor is not None:
                        cursor.gotoRange(cursor.getEnd(), False)
                _to_end_of_line(False, 1, None)
            elif textCursor is not None and not textCursor.isEndOfParagraph():
                 cursor.goRight(1, False)
            return _goto_mode("insert")

        elif cmd == "I":
            if mode == "visual":
                # Move to the line where the selection starts before going to line start
                cursor.gotoRange(cursor.getStart(), False)
            _to_start_of_line(False, True)
            return _goto_mode("insert")

        if cmd in ("o", "O") and mode == "visual":
            return _go_to_other_end(mode)

        if cmd == "o":
            _to_end_of_line(False, 0, None)
            cursor.goRight(1, False)
        elif cmd == "O":
            _to_start_of_line(False, False)
        else:
            return False

        cursor.setString(chr(13))  # CR
        if not cursor.isAtStartOfLine():
            cursor.goLeft(1, False)
            cursor.setString(chr(13) + chr(13))
            cursor.goRight(1, False)
        return _goto_mode("insert")

    except Exception:
        return False


def _delete_characters(count:int, key:KeyEvent, mode:str) -> bool:
    """Delete single characters. Commands 'x','X' and 's'."""
    text_cursor = _get_text_cursor()
    if text_cursor is None:
        return False
    try:
        if mode == "visual" and key.char == "X":
            _delete_and_replace_lines(key)

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

        text_cursor.setString("")
        if key.char == "s":
            _insert_commands("i", mode)
        elif mode == "visual":
            _goto_mode("normal")
        return True
    except Exception:
        return False


def _replace_characters(count:int, key:KeyEvent, mode) -> bool:
    """Replace character(s) under cursor with {key_char}.
       With count replace [count] characters with [count] {key_char}.
       Command 'r'.
    """
    if key.pending is None:
        _add_pending_key("r")
        return True

    _reset_pending_keys()
    try:
        cursor = _get_cursor()
        length = len(cursor.getString())

        if length > 1:
            cursor.setString(key.char * length)
        else:
            cursor.setString(key.char * count)

        if mode == "visual":
            _goto_mode("normal")
        return True
    except Exception:
        return False


# -----------------------
# Operators and clipboard
# -----------------------

def _delete_and_replace(count:int, key:KeyEvent, mode:str) -> bool:
    """Delete text {motion} moves over. Commands: 'd', 'dd', 'D', 'c', 'C', 'S'"""
    if mode == "normal" and key.char in ("c", "d"):
        _add_pending_key(key.char)
        return _goto_mode("pending")

    if key.char in ("C", "D"):  # To end of line commands.
        cursor = _get_cursor()
        text_cursor = _get_text_cursor()
        if cursor is None or text_cursor is None:
            return False
        # collapse to pos 0 (char under cursor)
        cursor.gotoRange(text_cursor.getStart(), False)
        _to_end_of_line(True, count, None)

    # Linewise delete/replace 'dd', 'cc' and 'S'.
    if (key.pending in ("c", "d") and key.char == key.pending) or key.char == "S":
        _to_start_of_line(False, False)
        cursor = _get_cursor()
        cursor.goDown(count, True)

    _copy_and_delete(True, True)

    if key.char in ('c', 'C', 'S') or \
        key.pending is not None and key.pending[0] in ("c", "C"):
        _goto_mode("insert")
    else:
        _goto_mode("normal")
    return True


def _yank(count, key, mode) -> bool:
    """Yanks text {motion} moves over. Commands `y`, `yy`, `Y'."""
    if key.char == "Y":
        cursor = _get_cursor()
        text_cursor = _get_text_cursor()
        if cursor is None or text_cursor is None:
            return False
        # collapse to pos 0 (char under cursor)
        cursor.gotoRange(text_cursor.getStart(), False)
        _to_end_of_line(True, count, None)
        _copy_and_delete(True, False)
        return True

    if mode == "normal" and key.pending is None:
        _set_position()
        _add_pending_key("y")
        _goto_mode("pending")
        return True

    # Linewise yanking 'yy'.
    elif key.pending == "y" and key.char == "y":
            _to_start_of_line(False, False)
            _hjkl_motion("j", count, True, mode)

    _copy_and_delete(True, False)

    position = _get_position()
    cursor = _get_cursor()
    if (position is not None and cursor is not None) and mode != "visual":
        # Keep yanked range visually selected briefly, then restore cursor.
        # Use _set_mode instead of _goto_mode so the selection isn't
        # collapsed immediately by _show_cursor("normal")().
        def _flash_restore():
            if key.char != "h":
                cursor.gotoRange(position, False)
            _show_cursor("normal")
        threading.Timer(0.08, _flash_restore).start()

    _reset_pending_keys()
    _set_mode("normal")
    return True


def _copy_and_delete(yank:bool, delete:bool) -> bool:
    """Copy and/or delete selection to clipboard."""
    try:
        if not yank and not delete:
            return False
        text_cursor = _get_text_cursor()
        if yank:
            dispatcher = _get_dispatcher()
            frame = _get_frame()
            if dispatcher is None or frame is None:
                return False
            dispatcher.executeDispatch(frame, ".uno:Copy", "", 0, ())
        if delete:
            if text_cursor is not None:
                text_cursor.setString("")
        return True
    except Exception:
        return False


def _paste(count:int, after_cursor:bool):
    """Paste text from clipboard after or before cursor {count} times.
       Commands 'p' and 'P'.
    """
    text_cursor = _get_text_cursor()
    cursor = _get_cursor()
    mode = _get_mode()
    if text_cursor is None or cursor is None:
        return False

    try:
        # msg(f"{after_cursor=} {text_cursor.isEndOfParagraph()=}")
        if after_cursor and not text_cursor.isEndOfParagraph():
            text_cursor.goRight(1, False)

        if mode == "normal":
            text_cursor.gotoRange(text_cursor.getStart(), False)
            controller = _get_controller()
            if controller is None:
                return False
            controller.select(text_cursor)

        dispatcher = _get_dispatcher()
        frame = _get_frame()
        if dispatcher is None or frame is None:
            return False

        for _ in range(count):
            dispatcher.executeDispatch(frame, ".uno:Paste", "", 0, ())

        _goto_mode("normal")
    except Exception:
        return False


def _undo_and_redo(count=1, redo=False) -> bool:
    """Undo or redo changes. Commands 'u' and 'C-r'."""
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


# ------------------
# Word motions
# ------------------

# Word motion specs.
FORWARD = "forward"
BACKWARD = "backward"
START = "start"
END = "end"

_WORD_MOTION_W = {
    "direction": FORWARD,
    "target": START,
    "big_word": False,
    "cross_empty": True,
    "inclusive": False,
}
_WORD_MOTION_B = {
    "direction": BACKWARD,
    "target": START,
    "big_word": False,
    "cross_empty": True,
    "inclusive": False,
}
_WORD_MOTION_BIG_B = {
    "direction": BACKWARD,
    "target": START,
    "big_word": True,
    "cross_empty": True,
    "inclusive": False,
}
_WORD_MOTION_E = {
    "direction": FORWARD,
    "target": END,
    "big_word": False,
    "cross_empty": False,
    "inclusive": True,
}
_WORD_MOTION_BIG_E = {
    "direction": FORWARD,
    "target": END,
    "big_word": True,
    "cross_empty": False,
    "inclusive": True,
}
_WORD_MOTION_GE = {
    "direction": BACKWARD,
    "target": END,
    "big_word": False,
    "cross_empty": True,
    "inclusive": True,
}
_WORD_MOTION_G_BIG_E = {
    "direction": BACKWARD,
    "target": END,
    "big_word": True,
    "cross_empty": True,
    "inclusive": True,
}
_WORD_MOTION_BIG_W = {
    "direction": FORWARD,
    "target": START,
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
    if spec["direction"] not in (FORWARD, BACKWARD):
        return False
    if spec["target"] not in (START, END):
        return False
    return True


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


def _query_word_motion(spec, count:int, expand:bool=False):
    text_cursor = _get_text_cursor()
    cursor = _get_cursor()
    if text_cursor is None or cursor is None or not _validate_word_motion_spec(spec):
        return _normalize_motion_range({"moved": False})

    steps = max(1, int(count))
    start_range = _clone_text_range(text_cursor)
    moved_any = False

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


def _scan_forward_word_target(paragraph_text, offset, spec):
    length = len(paragraph_text)
    if offset >= length:
        return None

    classify = _word_char_class
    big_word = bool(spec.get("big_word", False))
    target = spec.get("target", START)
    i = offset

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
    i = min(offset - 1, length - 1)

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


def _word_motion_once_forward(text_cursor, expand: bool, spec) -> bool:
    cross_empty = bool(spec.get("cross_empty", True))
    paragraph_text, offset = _current_paragraph_text_and_offset(text_cursor)
    length = len(paragraph_text)

    if length == 0:
        return _goto_next_paragraph_with_policy(text_cursor, expand, cross_empty)

    if offset >= length:
        return _goto_next_paragraph_with_policy(text_cursor, expand, cross_empty)

    next_offset = _scan_forward_word_target(paragraph_text, offset, spec)

    if next_offset is not None and next_offset < length:
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

    return _goto_next_paragraph_with_policy(text_cursor, expand, cross_empty)


def _word_motion_once_backward(text_cursor, expand: bool, spec) -> bool:
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

    prev_offset = _scan_backward_word_target(paragraph_text, offset, spec)
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
    prev_offset = _scan_backward_word_target(paragraph_text, length, spec)
    if prev_offset is None:
        return False
    text_cursor.gotoStartOfParagraph(False)
    if prev_offset > 0:
        text_cursor.goRight(prev_offset, expand)
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


def _word_motion(spec, expand: bool, count: int, mode: str) -> bool:
    """Run a word-motion command (e.g. `w`) and optionally apply an operator.

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
    except Exception:
        return False


def _apply_motion_result(result, expand:bool) -> bool:
    if not isinstance(result, dict) or not result.get("moved", False):
        return False
    end_range = result.get("end_range")
    cursor = _get_cursor()
    if cursor is None or end_range is None:
        return False
    try:
        # old HEAD
        # if expand and _state().get("visual_anchor") is None:
        #     # Pending mode: collapse to start_range (P) first so the selection
        #     # starts at the block cursor char, not at P+1 (the anchor side).
        #     start = result.get("start_range")
        #     if start is not None:
        #         cursor.gotoRange(start, False)
        # cursor.gotoRange(end_range, expand)
        # if expand and _state().get("visual_anchor") is None and result.get("inclusive"):
        if not expand:
            return cursor.gotoRange(end_range, False)

        anchor = _state().get("visual_anchor")
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
    except Exception:
        return False
    return True


# ------------------
# Sentence motions
# ------------------

def _to_next_sentence(text_cursor, cursor, expand: bool, include_whitespace: bool = True) -> bool:
    # Implements one ")" motion with paragraph-edge handling.
    old_pos = cursor.getPosition()

    # From an empty line, jump directly to the next non-empty paragraph.
    if _is_current_paragraph_empty(text_cursor):
        moved = _to_next_non_empty_paragraph(text_cursor, expand)
        if moved:
            _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
        return moved

    print(f"_to_next_sentence: before gotoNextSentence, tc={repr(text_cursor.getString()[:30])}, expand={expand}")
    text_cursor.gotoNextSentence(expand)
    _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
    print(f"_to_next_sentence: after sync, tc_str={repr(text_cursor.getString()[:30])}, same_pos={_same_pos(old_pos, cursor.getPosition())}, old_pos={_pos_xy(old_pos)}, new_pos={_pos_xy(cursor.getPosition())}")

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


def _sentences_forward(expand: bool, count: int, include_whitespace: bool = True) -> bool:
    """Sentences forward motion."""
    text_cursor = _get_text_cursor()
    cursor = _get_cursor()
    if text_cursor is None or cursor is None:
        return False
    try:
        if expand:
            # Collapse to the caret end so forward scan starts from the right place.
            print(f"_sentences_forward: tc before collapse={repr(text_cursor.getString()[:40])}, len={len(text_cursor.getString())}")
            caret = _get_visual_caret_range(text_cursor)
            text_cursor.gotoRange(caret, False)
            print(f"_sentences_forward: tc after collapse={repr(text_cursor.getString()[:40])}")
        steps = max(1, int(count))
        moved_any = False
        for _ in range(steps):
            if not _to_next_sentence(text_cursor, cursor, expand, include_whitespace):
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
    _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand, backward=True)
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


def _sentence_text_object(expand, count, key, mode, include_whitespace:bool = True):
    """Text object "as": select a sentence (pending/visual modes)."""
    text_cursor = _get_text_cursor()
    cursor = _get_cursor()
    if text_cursor is None or cursor is None:
        return False

    if mode == "pending":
        if not _is_at_sentence_start_heuristic(text_cursor):
            if not _sentences_backwards(False, 1):
                return False
        return _sentences_forward(expand, count, include_whitespace)

    if mode != "visual":
        return False

    # Without selection, current and possible next sentences are selected.
    selection_len = len(cursor.getString())
    if selection_len <= 1:      # Cursor len at start of Visual mode is 1.
        if not _is_at_sentence_start_heuristic(text_cursor):
            if not _sentences_backwards(False, 1):
                return False
        # After moving back without selection, re-anchor at sentence start
        # so _sentences_forward expands from there, not from the original
        # mid-sentence position.
        new_tc = _get_text_cursor()
        if new_tc is not None:
            _set_visual_anchor(new_tc.getStart())
        moved =_sentences_forward(expand, count)

    else:
        # With selection, it's direction defines which direction sentences are
        # going to get selected from cursor point.
        anchor = _state().get("visual_anchor")
        caret_before_anchor = anchor is not None and _range_starts_before(cursor, anchor)
        if caret_before_anchor:
            moved = _sentences_backwards(expand, count)
        else:
            moved = _sentences_forward(expand, count)

    _reset_pending_keys()
    _reset_count()
    return moved


# ------------------
# Paragraph motions
# ------------------

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


def _paragraph_scan_steps(limit: int = PARAGRAPH_SCAN_LIMIT):
    # Guard against malformed cursor loops freezing the UI.
    return range(limit)


def _to_next_non_empty_paragraph(text_cursor, expand: bool) -> bool:
    moved = False
    for _ in _paragraph_scan_steps():
        if not text_cursor.gotoNextParagraph(expand):
            break
        moved = True
        if not _is_current_paragraph_empty(text_cursor):
            break
    return moved


def _to_previous_non_empty_paragraph(text_cursor, expand: bool) -> bool:
    moved = False
    for _ in _paragraph_scan_steps():
        if not text_cursor.gotoPreviousParagraph(expand):
            break
        moved = True
        if not _is_current_paragraph_empty(text_cursor):
            break
    return moved


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


def _range_after_paragraph_break(text_range):
    try:
        text_obj = text_range.getText()
        probe = text_obj.createTextCursorByRange(text_range)
        if probe.goRight(1, False):
            return probe.getStart()
    except Exception:
        pass
    return None


def _normalize_paragraph_text_object_start(text_cursor, cursor, started_empty) -> bool:
    if started_empty:
        _move_to_empty_block_start(text_cursor)
        cursor.gotoRange(text_cursor.getStart(), False)
        return True
    if text_cursor.isStartOfParagraph():
        return True
    return _paragraphs_backward(False, 1)


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


def _select_ap_units_forward_visual(cursor, count: int, started_empty: bool) -> bool:
    steps = max(1, int(count))
    moved = False
    for i in range(steps):
        if not _paragraphs_forward(True, 1):
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


def _select_ap_units_backward_visual(cursor, count: int) -> bool:
    steps = max(1, int(count))
    moved = False
    for _ in range(steps):
        if not _paragraphs_backward(True, 1):
            break
        moved = True
        text_cursor = _get_text_cursor()
        if text_cursor is None:
            return False
        _extend_selection_over_leading_empty_block(text_cursor, cursor)
    return moved


def _paragraphs_forward(expand: bool, count: int = 1) -> bool:
    """Motion to for [count] paragraphs forward. Command '}'.

    From a non-empty paragraph, moves to the start of the next paragraph
    (which may itself be an empty separator line). From an empty separator
    line, jumps past all consecutive empty lines to the first non-empty
    paragraph start.
    """
    text_cursor = _get_text_cursor()
    cursor = _get_cursor()
    if text_cursor is None or cursor is None:
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
                    _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
                break
            moved_any = True
        if moved_any:
            _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand)
        return moved_any
    except Exception:
        return False


def _paragraphs_backward(expand: bool, count: int = 1) -> bool:
    """Motion for [count] paragraphs backward. Command '{'.

    From inside a paragraph, moves to the start of the current paragraph.
    From the start of a non-empty paragraph, moves to the start of the
    previous paragraph (which may be an empty separator line). From an
    empty separator line, skips backward past all consecutive empty lines
    to the start of the previous non-empty paragraph.
    """
    text_cursor = _get_text_cursor()
    cursor = _get_cursor()
    if text_cursor is None or cursor is None:
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
            _sync_view_cursor_to_text_cursor(cursor, text_cursor, expand, backward=True)
        return moved_any
    except Exception:
        return False


def _paragraph_text_object(expand, count, key, mode):
    """Text objects "ip"/"ap": select paragraphs (pending/visual modes).

    ip: inner paragraph is either a text paragraph or a contiguous empty-line block.
    ap: a paragraph is text plus trailing empty lines (forward) or leading empty
    lines (backward in visual mode).
    """
    text_cursor = _get_text_cursor()
    cursor = _get_cursor()
    if text_cursor is None or cursor is None:
        return False

    if mode == "pending":
        is_around = key.pending is not None and key.pending[-1] == "a"
        started_empty = _is_current_paragraph_empty(text_cursor)
        if not _normalize_paragraph_text_object_start(text_cursor, cursor, started_empty):
            return False

        moved = _paragraphs_forward(expand, count)
        if not moved:
            return False

        text_cursor = _get_text_cursor()
        if text_cursor is None:
            return False
        return _post_adjust_paragraph_text_object(text_cursor, cursor, is_around, started_empty)

    if mode != "visual":
        return False

    is_around = key.pending is not None and key.pending[-1] == "a"
    selection_len = len(cursor.getString())
    select_backwards = False

    # No extended selection -> select from start of current paragraph.
    if selection_len <= 1:
        started_empty = _is_current_paragraph_empty(text_cursor)
        if started_empty:
            _move_to_empty_block_start(text_cursor)
        else:
            text_cursor.gotoStartOfParagraph(False)
        _set_visual_anchor(text_cursor.getStart())
        cursor.gotoRange(text_cursor.getStart(), False)

    # Extended selection -> select from cursor point.
    else:
        anchor = _state().get("visual_anchor")
        caret_before_anchor = anchor is not None and _range_starts_before(cursor, anchor)
        if caret_before_anchor:
            select_backwards = True

        text_cursor = _get_text_cursor()
        if text_cursor is None:
            return False
        caret = _get_visual_caret_range(text_cursor)
        text_cursor.gotoRange(caret, False)
        started_empty = _is_current_paragraph_empty(text_cursor)
        if started_empty:
            _move_to_empty_block_start(text_cursor)
            cursor.gotoRange(text_cursor.getStart(), True)

    if select_backwards:
        if is_around:
            moved = _select_ap_units_backward_visual(cursor, count)
        else:
            moved = _paragraphs_backward(True, count)
    elif is_around:
        moved = _select_ap_units_forward_visual(cursor, count, started_empty)
        if not moved:
            return False
        return True
    else:
        moved = _paragraphs_forward(True, count)

    if not moved:
        return False
    text_cursor = _get_text_cursor()
    if text_cursor is None:
        return False

    if select_backwards:
        if is_around:
            return True
        return True

    return _post_adjust_paragraph_text_object(text_cursor, cursor, is_around, started_empty)


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
    def _normal_ctrl_actions(expand, count, mode):
        b_code = int(getattr(Key, "B", 512))
        c_code = int(getattr(Key, "C", 514))
        d_code = int(getattr(Key, "D", 515))
        f_code = int(getattr(Key, "F", 517))
        r_code = int(getattr(Key, "R", 529))
        u_code = int(getattr(Key, "U", 532))
        scroll_count = _get_scroll()

        actions = {
            b_code: lambda: _scroll_window(expand, count, False, mode, False),
            c_code: lambda: _ctrl_c_command(mode),
            d_code: lambda: _scroll_window(expand, count, True, mode, scroll_count),
            f_code: lambda: _scroll_window(expand, count, True, mode, False),
            r_code: lambda: _undo_and_redo(count, True),
            u_code: lambda: _scroll_window(expand, count, False, mode, scroll_count),
        }
        return actions

    @staticmethod
    def _normal_actions_keymap(key, count:int):
        """Build Normal-mode command dispatch map for actions."""
        mode = _get_mode()
        # Available commands after "g" command.
        if "g" in (key.pending or ""):
            actions = {
                # Currently "g" has only motions.
            }
        else:
            actions = {
                "c": lambda: _delete_and_replace(count, key, mode),
                "C": lambda: _delete_and_replace(count, key, mode),
                "d": lambda: _delete_and_replace(count, key, mode),
                "D": lambda: _delete_and_replace(count, key, mode),
                "g": lambda: KeyHandler._g_command(key),
                "i": lambda: _goto_mode("insert"),
                "I": lambda: _insert_commands("I", mode),
                "a": lambda: _insert_commands("a", mode),
                "A": lambda: _insert_commands("A", mode),
                "o": lambda: _insert_commands("o", mode),
                "O": lambda: _insert_commands("O", mode),
                "p": lambda: _paste(count, True),
                "P": lambda: _paste(count, False),
                "r": lambda: _replace_characters(count, key, mode),
                "u": lambda: _undo_and_redo(count, False),
                "U": lambda: _undo_and_redo(count, True),
                "s": lambda: _delete_characters(count, key, mode),
                "S": lambda: _delete_and_replace_lines(key),
                "x": lambda: _delete_characters(count, key, mode),
                "X": lambda: _delete_characters(count, key, mode),
                "y": lambda: _yank(count, key, mode),
                "Y": lambda: _yank(count, key, mode),
                "v": lambda: _goto_mode("visual"),
                "/": _focus_findbar,
            }
        return actions

    # These can be used independently or with operators.
    @staticmethod
    def _motions_keymap(key, expand, count:int, mode):
        """Build motion dispatch map for actions."""
        # Available motions after "g" command.
        if "g" in (key.pending or ""):
            motions = {
                "g": lambda: _to_line(expand, _get_raw_count(), False),
                "e": lambda: _word_motion(_WORD_MOTION_GE, expand, count, mode),
                "E": lambda: _word_motion(_WORD_MOTION_G_BIG_E, expand, count, mode)
            }
        else:
            motions = {
                "h": lambda: _hjkl_motion("h", count, expand, mode),
                "j": lambda: _hjkl_motion("j", count, expand, mode),
                "k": lambda: _hjkl_motion("k", count, expand, mode),
                "l": lambda: _hjkl_motion("l", count, expand, mode),
                "w": lambda: _word_motion(_WORD_MOTION_W, expand, count, mode),
                "W": lambda: _word_motion(_WORD_MOTION_BIG_W, expand, count, mode),
                "e": lambda: _word_motion(_WORD_MOTION_E, expand, count, mode),
                "E": lambda: _word_motion(_WORD_MOTION_BIG_E, expand, count, mode),
                "b": lambda: _word_motion(_WORD_MOTION_B, expand, count, mode),
                "B": lambda: _word_motion(_WORD_MOTION_BIG_B, expand, count, mode),
                "$": lambda: _to_end_of_line(expand, count, key),
                "^": lambda: _to_start_of_line(expand, True),
                "H": lambda: _jump_to_page(expand, "start"),
                "L": lambda: _jump_to_page(expand, "end"),
                "G": lambda: _to_line(expand, _get_raw_count(), True),
                ")": lambda: _sentences_forward(expand, count),
                "(": lambda: _sentences_backwards(expand, count),
                "}": lambda: _paragraphs_forward(expand, count),
                "{": lambda: _paragraphs_backward(expand, count),
            }
            if key.char == "0" and _get_raw_count() == 0:
                motions["0"] = lambda: _to_start_of_line(expand, False)

        return motions


    @staticmethod
    def _navigation_keys(expand, count, mode):
        backspace = int(Key.BACKSPACE)
        left      = int(Key.LEFT)
        right     = int(Key.RIGHT)
        up        = int(Key.UP)
        down      = int(Key.DOWN)
        home      = int(Key.HOME)
        end       = int(Key.END)
        pageup    = int(Key.PAGEUP)
        pagedown  = int(Key.PAGEDOWN)

        # Navigation keys are mapped to motions so they can take count, be used
        # with operators and for pageup/pagedown handle selection.
        return {
            backspace: "h",
            left:      "h",
            right:     "l",
            up:        "k",
            down:      "j",
            home:      "0",
            end:       "$",
            pageup:    lambda: _scroll_window(expand, count, False, mode, False),
            pagedown:  lambda: _scroll_window(expand, count, True, mode, False),
        }
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
        code = _key_code(event)
        is_ctrl = _is_only_ctrl(mods)
        is_escape = _is_escape(code, is_ctrl)

        # Insert mode matching. Do as little as possible.
        if mode == "insert":
            if is_escape or (is_ctrl and code == 514):  # C-c
                return self._consume_action(lambda: _goto_mode("normal"))
            return False

        key = KeyEvent(
            char=_normalize_key_char(event),
            code=code,
            pending=_get_pending_keys()
        )

        count: int = _get_count()
        expand: bool = _get_mode() in ("visual", "pending")

        # --- Keys with non-shift/AltGr modifiers -------

        if is_ctrl:
            actions = self._normal_ctrl_actions(expand, count, mode)
            action = actions.get(key.code)
            if action is not None:
                return self._consume_action(action)
            else:
                return False

        # Pass other non-shift modified shortcuts through, except characters
        # made with AltGr.
        if _has_non_shift_modifier(event):
            if not bool(_is_altgr_char(event, key)):
                return False

        # --- Keys without modifiers after this --------

        if key.pending == "r":
            if key.char.isprintable() or key.code in (1280, 1282):  # enter, tab
                return self._consume_action(
                    lambda: _replace_characters(count, key, mode)
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

        nav = self._navigation_keys(expand, count, mode).get(key.code)
        if callable(nav):
            return self._consume_action(nav)
        elif nav is not None:
            key = KeyEvent(char=nav, code=key.code, pending=key.pending)

        if mode in ("pending", "visual"):
            matched_object = self._match_text_objects(key, expand, count, mode)
            if matched_object is not None:
                return matched_object

        # Match and handle motions that support operators.
        matched_motions = self._match_motions(key, count, mode)
        if matched_motions is not None:
            return matched_motions

        # Match and handle non-motion commands.
        matched_commands = self._match_commands(key, count)
        if matched_commands is not None:
            return matched_commands

        # No suitable commands matched for "g" so cancel.
        if "g" in (key.pending or ""):
            _reset_count()
            _goto_mode("normal")
            return True

        # ----- Non-character keys after this -----

        if _is_function_key(event):
            return False

        # Now suitable key matched so reset.
        _reset_count()

        if key.pending:  # Cancel rest of keys since no match.
            _reset_pending_keys()
            _set_mode("normal")
            return True

        if is_escape:
            return self._consume_action(lambda: _goto_mode("normal"))
        # Deliberately cancels operator pending mode.
        if _is_del_key(event):
            return self._consume_action(lambda: _delete_characters(count, key, mode))
        if _is_insert_key(event):
            return self._consume_action(lambda: _insert_commands("i", mode))

        return self._consume_action(None)
    # -----------------------------------------

    @staticmethod
    def _text_objects_keymap(expand, count:int, key, mode):
        text_objects = {
            "as": lambda: _sentence_text_object(expand, count, key, mode, True),
            "is": lambda: _sentence_text_object(expand, count, key, mode, False),
            "ip": lambda: _paragraph_text_object(expand, count, key, mode),
            "ap": lambda: _paragraph_text_object(expand, count, key, mode),
        }

        return text_objects

    def _match_text_objects(self, key, expand, count, mode):
        # Key after "a" or "i"
        if key.pending is not None and key.pending[-1] in ("a", "i"):
            # if key.char in ("w", "W", "s", "p"):  <- added later.
            if key.char in ("s", "p"):
                text_objects = self._text_objects_keymap(expand, count, key, mode)
                # msg(f"text-object matched: {object}")
                text_object = text_objects.get(key.pending[-1] + key.char)

                if "c" in (key.pending or "") or "d" in (key.pending or ""):
                    return self._consume_action(text_object,
                        lambda: _delete_and_replace(count, key, _get_mode())
                    )
                elif "y" in (key.pending or ""):
                    return self._consume_action(text_object,
                        lambda: _yank(count, key, _get_mode())
                    )
                return self._consume_action(text_object)
            # Not valid key, cancel
            else:
                if mode == "pending":
                    _reset_count()
                    _goto_mode("normal")
                elif mode == "visual":
                    _reset_pending_keys()
            return True

        if key.char in ("a", "i"):
            if key.pending is None or key.pending in ("d", "c", "y"):
                _add_pending_key(key.char)

            # Not valid key, cancel
            elif mode == "pending":
                _reset_count()
                _goto_mode("normal")
            elif mode == "visual":
                _reset_prefix()
            return True

        return None

# key.pending[-1] not in ("a", "i")

    def _match_motions(self, key, count, mode):
        expand: bool = _get_mode() in ("visual", "pending")
        motions = self._motions_keymap(key, expand, count, mode)
        motion = motions.get(key.char)
        if motion is None:
            return None

        # If operator is pending, add it to be done after motion.
        if "c" in (key.pending or "") or "d" in (key.pending or ""):
            return self._consume_action(motion,
                lambda: _delete_and_replace(count, key, _get_mode())
            )
        elif "y" in (key.pending or ""):
            return self._consume_action(motion,
                lambda: _yank(count, key, _get_mode())
            )
        elif "g" in (key.pending or ""):
            return self._consume_action(motion, lambda: _reset_pending_keys())
        return self._consume_action(motion)

    def _match_commands(self, key, count):
        normal_actions = self._normal_actions_keymap(key, count)
        action = normal_actions.get(key.char)
        if action is None:
            return None
        if key.pending in ("c", "d", "y"):
            if key.char == key.pending:        # dd, yy, cc
                return self._consume_action(action)
            if key.char == "g":                 # dg, yg, cg
                return self._consume_action(action)
            # non-operator key while pending: cancel
            _reset_pending_keys()
            _set_mode("normal")
            return None
        return self._consume_action(action)

    @staticmethod
    def _g_command(key):
        if key.pending in (None, "d", "y", "c"):
            _add_pending_key("g")
            return True

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

# Non-editor functionality: initialization, enabling and disabling extension,
# handling controllers, listening events.

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
    listener = state.get("mouse_listener")
    if listener is not None:
        try:
            controller.removeMouseClickHandler(listener)
        except Exception:
            pass
        state["mouse_listener"] = None


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
    if state.get("mouse_listener") is None:
        listener = MouseSelectionListener()
        try:
            controller.addMouseClickHandler(listener)
            state["mouse_listener"] = listener
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


class MouseSelectionListener(unohelper.Base, XMouseClickHandler):
    """Switches to visual mode when user selects text with the mouse.

    XMouseClickHandler is added to the controller via addMouseClickHandler
    and receives mouse events from the document editing area.
    Returns False to not consume the event (pass through to LibreOffice).
    """

    def mouseReleased(self, event):
        state = _state()
        if not state["enabled"]:
            return False
        anchor = state.get("mouse_press_anchor")
        cursor = _get_cursor()
        # print(f"mouseReleased: cursor={cursor}, anchor={anchor}")
        # if cursor is not None and anchor is not None:
            # try:
                # dist = _range_length_between(anchor, cursor.getStart())
                # print(f"mouseReleased: dist={dist}")
                # if dist == 0:
                    # print("mouseReleased: same position (click, no drag)")
                # else:
                    # print("mouseReleased: different position (drag selection)")
            # except Exception as e:
            #     print(f"mouseReleased: comparison error {e}")
        _reset_count()
        _reset_pending_keys()
        _set_mode("visual")
        return False

    def mousePressed(self, event):
        state = _state()
        if not state["enabled"]:
            return False
        # Save cursor position at press time as the future visual anchor.
        cursor = _get_cursor()
        # text_cursor.collapseToEnd()
        # Doesn't yet work.
        if cursor is not None:
            try:
                state["mouse_press_anchor"] = cursor.getStart()
            except Exception:
                pass
                # _state()["mouse_press_anchor"] = None
        return False

    def disposing(self, event):
        return None


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
    controller = _get_controller()
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
    controller = _get_controller()
    if controller is not None:
        state["view_cursor"] = controller.getViewCursor()
    _set_mode("normal")
    _show_cursor("normal")


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


g_exportedScripts = (toggle_viper_office, enable_viper_office, disable_viper_office, debug_cursor_state)

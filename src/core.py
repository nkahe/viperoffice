from __future__ import annotations
from typing import TYPE_CHECKING, Any, Final, Literal, NamedTuple
import builtins
import datetime
import inspect
import os
import tempfile
import traceback

if TYPE_CHECKING:
    from com.sun.star.text import XViewCursor, XTextCursor

# Core module holds shared state, UNO context access, and common helpers used
# across modules.

class KeyEvent(NamedTuple):
    char: str
    code: int
    pending: str | None

# ------------
# Global state
# ------------

# Constants

# Provided by LibreOffice's Python macro runtime.
if "XSCRIPTCONTEXT" not in globals():
    XSCRIPTCONTEXT: Any = None

DEBUG = True

# Guard for paragraph scans to avoid malformed cursor loops freezing the UI.
PARAGRAPH_SCAN_LIMIT: Final[int] = 10000

# Additional characters which are considered to be a part of word for word
# motions w, b, e, ge. Alphabets are always included. "chars" are written
# together like: "chars": "_,."
ISWORD: Final[dict] = {
    "digits": True,
    "chars": "_",
}

# Current Vi input mode. "pending" is short for Operator-pending mode. Happens
# after operator command "d", "c" or "y". ViperOffice is then waiting for motion.
MODES: Final[tuple] = ("normal", "insert", "pending", "visual")
Mode = Literal[*MODES]

# How many lines should C-d and C-u scroll.
SCROLL: Final[int] = 21

_StateDict = dict[str, Any]
def _state() -> _StateDict:

    key = "_viperoffice_state"
    state = getattr(builtins, key, None)
    if state is None:
        state = {
            # If the extension been started. Will only be set to True.
            "started": False,
            "enabled": False,
            "mode": "normal",
            # Visible cursor. Type is XViewCursor UNO object.
            "view_cursor": None,
            "key_handler": None,
            "view_event_listener": None,
            "global_event_broadcaster": None,
            # One mouse listener shared across controllers.
            "mouse_listener": None,
            "mouse_listener_controllers": set(),
            # Anchor (fixed end) of visual mode selection. Saved when entering
            # visual mode so motions know which end is the caret.
            "visual_anchor": None,
            # Saved snapshot of cursor position in situations when the original
            # position need to be restored after motion.
            "cursor_position": None,
            # Last f, F, t or T motion and character following it. Used for
            # commands ; and , that repeat last of that motion type.
            "last_ft": None
        }
        setattr(builtins, key, state)
    return state


def _get_visual_anchor() -> Any | None:
    return _state()["visual_anchor"]


def _get_cursor() -> XViewCursor | None:
    return _state()["view_cursor"]


# Text cursors are snapshots of view cursor.
def _get_text_cursor() -> XTextCursor | None:
    cursor = _get_cursor()
    if cursor is None:
        return None
    try:
        return cursor.getText().createTextCursorByRange(cursor)
    except Exception as e:
        _handle_exc(err=e)
        return None


def _set_visual_anchor(anchor) -> None:
    _state()["visual_anchor"] = anchor


def _clear_visual_anchor() -> None:
    _state()["visual_anchor"] = None


def _set_mode(new_mode: Mode) -> bool:
    if new_mode not in MODES:
        return False
    _state()["mode"] = new_mode
    _update_statusline()
    return True


def _get_mode() -> Mode:
    return _state()["mode"]


def _get_last_ft() -> dict[str, str] | None:
    return _state().get("last_ft")


def _set_last_ft(ft_type: str, ch: str) -> None:
    if ft_type not in ("f", "F", "t", "T"):
        _state()["last_ft"] = None
        return
    if not isinstance(ch, str) or len(ch) != 1:
        _state()["last_ft"] = None
        return
    _state()["last_ft"] = {"type": ft_type, "char": ch}


def _set_position() -> bool:
    """Save current view cursor position"""
    try:
        cursor = _get_cursor()
        if cursor is None:
            return False
        _state()["cursor_position"] = cursor.getStart()
        return True
    except Exception as e:
        _handle_exc(err=e)
        return False


def _get_position() -> dict | None:
    return _state()["cursor_position"]


def _get_scroll() -> int:
    """Lines to scroll with C-b and C-u commands."""
    default = 20
    try:
        lines_to_scroll = int(SCROLL)
        if 1 <= lines_to_scroll <= 99:
            return lines_to_scroll
    except Exception as e:
        _handle_exc(err=e)
        pass
    return default


def _paragraph_scan_steps(limit: int = PARAGRAPH_SCAN_LIMIT):
    # Guard against malformed cursor loops freezing the UI.
    return range(limit)


def _get_pending_keys() -> None | str:
    handler = _state().get("key_handler")
    try:
        if handler is None:
            return None
        pending_keys = handler.pending_keys
        return pending_keys
    except Exception as e:
        _handle_exc(err=e)
        return None


def _reset_pending_keys():
    handler = _state().get("key_handler")
    try:
        if handler is not None and hasattr(handler, "reset_pending_keys"):
            return bool(handler.reset_pending_keys())
    except Exception as e:
        _handle_exc(err=e)
        pass
    _update_statusline()
    return True


def _get_count() -> int:
    """Return effective count: prefer active KeyHandler's count if available."""
    handler = _state().get("key_handler")
    try:
        if handler is None:
            return 1
        count = int(handler.count)
        return count
    except Exception as e:
        _handle_exc(err=e)
        return 1


def _get_raw_count() -> int:
    """Return the raw numeric count (0 if none). Prefer KeyHandler's value when present."""
    handler = _state().get("key_handler")
    try:
        if handler is None:
            return 0
        count = int(handler.get_raw_count())
        return count
    except Exception as e:
        _handle_exc(err=e)
        return 0


def _reset_count() -> bool:
    """Reset the active count. Delegates to the active KeyHandler when present.

    Many call sites call this module helper; keep compatibility by checking
    _state()["key_handler"] and delegating to its reset method if available.
    """
    handler = _state().get("key_handler")
    try:
        if handler is not None and hasattr(handler, "_reset_count"):
            return bool(handler._reset_count())
    except Exception as e:
        _handle_exc(err=e)
        pass
    _update_statusline()
    return True


# Error handling

def _dbg(msg):  # noqa: F811  # pyright: ignore[reportUnusedFunction]
    """Log [msg] to log file."""
    # if not DEBUG:
    #     return
    try:
        ts = datetime.datetime.now().strftime("%m-%d %H:%M:%S")
        log_path = os.path.join(tempfile.gettempdir(), "viperffice-debug.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{ts} {msg}\n")
    except Exception:
        pass


def _handle_exc(err: Exception | None = None) -> None:
    """Log and print exception details for current caller."""
    module_name = "<unknown>"
    filename = "<unknown>"
    func_name = "<unknown>"
    try:
        frame = inspect.currentframe()
        if frame is not None and frame.f_back is not None:
            caller = frame.f_back
            func_name = caller.f_code.co_name
            module_name = caller.f_globals.get("__name__", "<unknown>")
            filename = caller.f_code.co_filename
    except Exception:
        func_name = "<unknown>"

    try:
        if err is None:
            details = traceback.format_exc()
        else:
            details = f"{type(err).__name__}: {err}"

        msg = f"Exception in {module_name}.{func_name} ({filename}): {details}"
        _dbg(msg)
        print(msg)
    except Exception:
        pass

# UNO Helpers

def _current_doc():
    try:
        return XSCRIPTCONTEXT.getDocument()
    except Exception as e:
        _handle_exc(err=e)
        return None


def _get_controller():
    doc = _current_doc()
    if doc is None:
        return None
    try:
        return doc.getCurrentController()
    except Exception as e:
        _handle_exc(err=e)
        return None


def _get_dispatcher():
    try:
        ctx = XSCRIPTCONTEXT.getComponentContext()
        smgr = ctx.getServiceManager()
        dispatcher = smgr.createInstanceWithContext("com.sun.star.frame.DispatchHelper", ctx)
        return dispatcher
    except Exception as e:
        _handle_exc(err=e)
        return None


def _execute_dispatch(command: str, args: tuple = ()) -> bool | None:
    """Execute UNO dispatch command on current frame."""
    try:
        dispatcher = _get_dispatcher()
        frame = _get_frame()
        if dispatcher is None or frame is None:
            return False
        dispatcher.executeDispatch(frame, command, "", 0, args)
        return True
    except Exception as e:
        _handle_exc(err=e)
        return None


def _get_frame():
    """Get current frame."""
    try:
        controller = _get_controller()
        if controller is None:
            return None
        frame = controller.getFrame()
        return frame
    except Exception as e:
        _handle_exc(err=e)
        return None


# Mode switching

def _goto_mode(new_mode: Mode) -> bool:
    """Change Vi input mode to [new_mode]. Resets pending keys for other than
    Operator pending mode."""
    old_mode = _get_mode()
    if new_mode == "normal":
        _reset_pending_keys()
        _clear_visual_anchor()
        if old_mode in ("normal", "pending"):
            pass

        elif old_mode == "insert":
            cursor = _get_cursor()
            if cursor is not None and not cursor.isAtStartOfLine():
                # Mimics Vi/Vim cursor behavior.
                cursor.goLeft(1, False)
            _show_cursor("normal")

        elif old_mode.startswith("visual"):
            # Place caret to correct end of selection.
            cursor = _get_cursor()
            controller = _get_controller()
            tc = _get_text_cursor()
            try:
                if controller is not None and \
                    tc is not None and  \
                    cursor is not None:
                    # Use the saved anchor to find the caret end before
                    # clearing it.
                    caret = _get_visual_caret_range(tc)
                    tc.gotoRange(caret, False)
                    if not cursor.isAtStartOfLine():
                        tc.goLeft(1, False)
                    controller.select(tc)
            finally:
                _clear_visual_anchor()
                _show_cursor("normal")

    elif new_mode == "insert":
        _reset_pending_keys()
        _clear_visual_anchor()
        _show_cursor("insert")

    elif new_mode.startswith("visual"):
        _reset_pending_keys()
        _show_cursor("visual")

    elif new_mode == "pending":
        _clear_visual_anchor()
        _show_cursor("pending")
    else:
        return False

    _set_mode(new_mode)
    return True


def _get_visual_caret_range(tc):
    """Return the caret (active/moving) end of the visual selection as an XTextRange.

    LibreOffice's getStart()/getEnd() always return left/right ends regardless of
    direction, so we compare against the saved anchor to determine which end is fixed.
    - If anchor == getStart(): forward selection, caret is at getEnd().
    - Otherwise: backward selection, caret is at getStart().
    """
    anchor = _get_visual_anchor()
    if anchor is None:
        return tc.getEnd()
    try:
        text = tc.getText()
        probe = text.createTextCursorByRange(tc.getStart())
        probe.gotoRange(anchor, True)
        if len(probe.getString()) == 0:
            return tc.getEnd()   # forward selection
        else:
            return tc.getStart() # backward selection
    except Exception as e:
        _handle_exc(err=e)
        return tc.getEnd()


def _show_cursor(mode: Mode):
    """Sets cursor style and saves cursor position info. """
    tc = _get_text_cursor()
    cursor = _get_cursor()
    controller = _get_controller()
    if tc is None or controller is None or cursor is None:
        return False
    try:
        if mode in ("normal", "pending"):
            # Select 1 character right side of caret as Normal mode cursor.
            tc.gotoRange(tc.getStart(), False)
            moved = tc.goRight(1, False)
            if moved:
                tc.goLeft(1, True)

        elif mode.startswith("visual"):
            # Collapse cursor since caret is the anchor point in LibreOffice.
            tc.gotoRange(tc.getStart(), False)
            _set_visual_anchor(tc.getStart())
        elif mode == "insert":
            # Use collapsed cursor.
            tc.gotoRange(tc.getStart(), False)
        else:
            raise ValueError("Unknown mode: " + str(mode))

        controller.select(tc)
    except Exception as e:
        _handle_exc(err=e)
        return False


def _update_statusline(controller=None):
    if controller is None:
        controller = _get_controller()
    if controller is None:
        return
    try:
        mode = _get_mode()
        padding = "   "
        mode_name = "o-pending" if mode == "pending" else mode
        text = ""
        if _get_raw_count() != 0:
            count_text = _get_count()
            text += f"{padding}{count_text}"

        pendings_keys = _get_pending_keys()
        if pendings_keys:
            text += padding + pendings_keys
        text = mode_name.upper() + text
        controller.StatusIndicator.start(text, 0)
    except Exception as e:
        _handle_exc(err=e)
        # Non-fatal for status update.
        pass

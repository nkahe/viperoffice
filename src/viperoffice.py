from __future__ import annotations
from typing import TYPE_CHECKING, Any, Callable, Final, Literal, NamedTuple
import builtins
import datetime
from functools import partial
import threading
import unohelper
from com.sun.star.awt import KeyModifier, XKeyHandler, Key, Rectangle, XMouseClickHandler
from com.sun.star.document import XEventListener

if TYPE_CHECKING:
    from com.sun.star.text import XViewCursor, XTextCursor

# Current vi input mode. "pending" is short for Operator-pending mode. Happens
# after operator command "d", "c" or "y". ViperOffice is then waiting for motion.
Mode = Literal["normal", "insert", "pending", "visual"]

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

DEBUG = True

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


def _get_cursor() -> XViewCursor | None:
    return _state()["view_cursor"]


# Text cursors are snapshots of view cursor.
def _get_text_cursor() -> XTextCursor | None:
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


def _set_mode(new_mode: Mode) -> bool:
    if new_mode not in ("normal", "insert", "pending", "visual"):
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
    except Exception:
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
    """Show [text] in a pop-up window."""
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
        para = range.getText()
        # Compute start offset relative to paragraph start
        start_range = range.getStart()
        start_cursor = para.createTextCursorByRange(start_range)
        start_cursor.gotoStartOfParagraph(False)
        start_cursor.gotoRange(start_range, True)
        start_offset = len(start_cursor.getString())
        # Compute end offset relative to paragraph start (exclusive)
        end_range = range.getEnd()
        end_cursor = para.createTextCursorByRange(end_range)
        end_cursor.gotoStartOfParagraph(False)
        end_cursor.gotoRange(end_range, True)
        end_offset = len(end_cursor.getString())
        snippet = text.replace("\n", "\\n")
        if len(snippet) > 120:
            snippet = snippet[:117] + "..."
        return f"'{snippet}' (start_offset={start_offset}, end_excl={end_offset})"
    except Exception:
        try:
            return f"<unprintable range: {range}>"
        except Exception:
            return "<unprintable range>"


def _debug_cursor_state(pop_up: bool = False):  # noqa: F811  # pyright: ignore[reportUnusedFunction]
    """Print debug info about view cursor and text cursor ranges to console. For development use."""
    cursor = _get_cursor()
    if cursor is None:
        print("ViperOffice cursor debug: No view cursor available.")
        return
    try:
        text_cursor = _get_text_cursor()
        lines = [f"Mode: {_get_mode()}  pending: {_get_pending_keys()}"]

        try:
            pos = cursor.getPosition()
            x = pos.X() if callable(pos.X) else pos.X
            y = pos.Y() if callable(pos.Y) else pos.Y
            lines.append("-- view cursor --")
            lines.append(f"position: X={x}, Y={y}")
        except Exception:
            lines.append("Position: unavailable")
        try:
            lines.append(f"Collapsed: {cursor.isCollapsed()}")
            lines.append(f"At start of line: {cursor.isAtStartOfLine()}")
        except Exception:
            lines.append("Range: unavailable")

        # Text cursor info
        if text_cursor is None:
            lines.append("TextCursor: unavailable")
        else:

            try:
                paragraph_text, offset = _current_paragraph_text_and_offset(text_cursor)
                char = text_cursor.getString()[:40]
                anchor = _state().get("visual_anchor")
                caret_before_anchor = anchor is not None and _range_starts_before(cursor, anchor)
                lines.append("-- text cursor LO API --")
                lines.append(f"String: {char}")
                lines.append(f"collapsed: {text_cursor.isCollapsed()}")
                lines.append(f"start of paragraph: {text_cursor.isStartOfParagraph()}")
                lines.append(f"end of paragraph: {text_cursor.isEndOfParagraph()}")
                # Cursor needs to be collapsed for this to give True.
                lines.append(f"start of sentence: {text_cursor.isStartOfSentence()}")
                lines.append(f"start of word: {text_cursor.isStartOfWord()}")
                lines.append(f"end of word: {text_cursor.isEndOfWord()}")
                lines.append("-- ViperOffice custom functions --")
                lines.append(f"caret before anchor: {caret_before_anchor}")
                lines.append(f"Is at whitespace: {_is_cursor_at_whitespace(text_cursor)}")
                lines.append(f"at whitespace after sentence: {_is_cursor_at_whitespace(text_cursor, "after_sentence")}")
                lines.append(f"at whitespace before paragraph: {_is_cursor_at_whitespace(text_cursor, "before_paragraph")}")
                lines.append(f"Is at empty paragraph: {_is_current_paragraph_empty(text_cursor)}")
                lines.append(f"Paragraph length: {len(paragraph_text)}")
                lines.append(f"Paragraph offset: {offset}")
                lines.append(f"start of sentence: {_is_at_sentence_start(text_cursor)}")
                lines.append(f"Word character class: {_word_char_class(char)}")
                lines.append("")
            except Exception as e:
                lines.append(f"TextCursor info error: {e}")

        if pop_up:
            msg("\n".join(lines), "ViperOffice cursor debug")
        else:
            print("ViperOffice cursor debug:\n" + "\n".join(lines))
    except Exception as e:
        print(f"ViperOffice cursor debug: Error: {e}")


# ------------------
# UI and input modes
# ------------------

# Functions to manipulate view and model (document).

def _get_pending_keys() -> None | str:
    handler = _state().get("key_handler")
    try:
        if handler is None:
            return None
        pending_keys = handler.pending_keys
        return pending_keys
    except Exception:
        return None


def _reset_pending_keys():
    handler = _state().get("key_handler")
    try:
        if handler is not None and hasattr(handler, "reset_pending_keys"):
            return bool(handler.reset_pending_keys())
    except Exception:
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
    except Exception:
        return 1


def _get_raw_count() -> int:
    """Return the raw numeric count (0 if none). Prefer KeyHandler's value when present."""
    handler = _state().get("key_handler")
    try:
        if handler is None:
            return 0
        count = int(handler.get_raw_count())
        return count
    except Exception:
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
    except Exception:
        pass
    _update_statusline()
    return True


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
    except Exception:
        # Non-fatal for status update.
        pass


def _show_cursor(mode: Mode):
    """Sets cursor style and saves cursor position info. """
    text_cursor = _get_text_cursor()
    cursor = _get_cursor()
    controller = _get_controller()
    if text_cursor is None or controller is None or cursor is None:
        return False
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
                # text_cursor.collapseToStart()
                text_cursor.gotoRange(text_cursor.getStart(), False)
                _set_visual_anchor(text_cursor.getStart())
                # text_cursor.goRight(1, True)
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
    except Exception:
        pass


# UNO doesn't offer call to get caret position when there's selection. Usually
# state.visual_anchor is set and tracked but for situations it's not available
# this can be used.
def _is_forward_selection(cursor) -> bool:
    """Return True if caret is at right end of selection, False if at left end."""
    try:
        original_len = len(cursor.getString())
        moved = cursor.goRight(1, True)
        if moved:
            new_len = len(cursor.getString())
            cursor.goLeft(1, True)
            return new_len > original_len
        return True
    except Exception:
        return True


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


def _sync_view_cursor_to_text_cursor(text_cursor, expand: bool, view_cursor, backward: bool = False):
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
    except Exception:
        pass


# Based on Commit f33d46f from fedorov-ao/vibreoffice
def _go_to_other_end(mode: Mode, cursor) -> bool:
    """Move cursor to the other end of highlighted text. Command 'o' / 'O' in visual mode.

    The current cursor position becomes the start of the highlighted text and
    the cursor is moved to the other end of the highlighted text. The highlighted
    area remains the same.
    """
    if mode != "visual":
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

def _scroll_window(expand:bool, count:int, forward:bool, mode:Mode, lines:int|None=None) -> bool:
    """Scroll window. Commands 'C-f' or <PageDown>, 'C-b' or <PageUp>, 'C-u', 'C-d'.
    """
    try:
        cursor = _get_cursor()
        if cursor is None:
            return False
        if lines:
            if forward:
                for _ in range(count):
                    _lines_down(lines, expand, mode, cursor)
            else:
                for _ in range(count):
                    _lines_up(lines, expand, mode, cursor)
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


def _to_line(expand: bool, raw_count: int, default_end: bool, mode, cursor) -> bool:
    """Go to line [count] motion. Commands 'G' and 'gg'. Linewise in
       Operation-pending mode. Args:

    expand: bool       Expand selection
    raw_count: int     Move to line [count].
    default_end: bool  To default to end of text document if no count given.
                       else default of start of text document.
    """
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

        if mode == "pending":
            _select_linewise(cursor)
        return True
    except Exception:
        return False


def _jump_to_page(expand: bool, count, cursor, target: str = "start") -> bool:
    """Motion to start or end of a page. Commands 'H' and 'L'."""
    target = target.lower()
    try:
        if target not in ("start", "end", "next", "previous"):
            return False

        anchor = cursor.getStart() if expand else None

        match target:
            case "start":
                cursor.jumpToStartOfPage()
            case "end":
                cursor.jumpToEndOfPage()
            case "next":
                for _ in range(count):
                    cursor.jumpToNextPage()
            case "previous":
                for _ in range(count):
                    cursor.jumpToPreviousPage()

        if expand and anchor:
            new_pos = cursor.getStart()
            cursor.gotoRange(anchor, False)
            cursor.gotoRange(new_pos, True)

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


def _repeat_search(count, backward: bool = False) -> bool:
    """Repeat last LibreOffice search count times. Commands 'n' and 'N'."""
    try:
        dispatcher = _get_dispatcher()
        frame = _get_frame()
        if dispatcher is None or frame is None:
            return False
        # FindbarFindNext / FindbarFindPrev repeat the last findbar search.
        cmd = "vnd.sun.star.findbar:FindPrev" if backward else "vnd.sun.star.findbar:FindNext"
        for _ in range(max(1, count)):
            dispatcher.executeDispatch(frame, cmd, "", 0, ())
        return True
    except Exception:
        return False


def _to_character(expand:bool, count:int, cursor, command: str, char: str) -> bool:
    """Motions to move to [count]'th next / previous occurances of a character [char].
       Commands 'f', 'F', 't', 'T'.
    """
    doc = _current_doc()
    if doc is None:
        return False
    try:
        if command not in ("f", "F", "t", "T"):
            return False
        if not isinstance(char, str) or len(char) != 1:
            return False

        backward = command in ("F", "T")
        visual_forward_extra = expand and command in ("f", "t") and not backward
        steps = max(1, int(count))
        moved_any = False

        search_desc = doc.createSearchDescriptor()
        search_desc.setSearchString(char)
        search_desc.SearchCaseSensitive = True
        search_desc.SearchBackwards = backward

        def _range_same_start(range_a, range_b) -> bool:
            if range_a is None or range_b is None:
                return False
            try:
                text = range_a.getText()
                return text.compareRegionStarts(range_a, range_b) == 0
            except Exception:
                return False

        def _offset_range(text, base_range, delta: int):
            if base_range is None:
                return None
            try:
                probe = text.createTextCursorByRange(base_range)
                if delta < 0 and not probe.goLeft(-delta, False):
                    return None
                if delta > 0 and not probe.goRight(delta, False):
                    return None
                return probe.getStart()
            except Exception:
                return None

        for _ in range(steps):
            text_cursor = _get_text_cursor()
            if text_cursor is None:
                return moved_any

            text = text_cursor.getText()
            if text is None:
                return moved_any

            if expand:
                caret = _get_visual_caret_range(text_cursor)
                text_cursor.gotoRange(caret, False)
            else:
                text_cursor.gotoRange(text_cursor.getStart(), False)

            start_cursor = text.createTextCursorByRange(text_cursor.getStart())
            if backward:
                if not start_cursor.goLeft(1, False):
                    break
            else:
                start_offset = 2 if command == "t" else 1
                if not start_cursor.goRight(start_offset, False):
                    break

            start_range = start_cursor.getStart()
            found_range = doc.findNext(start_range, search_desc)
            if found_range is None:
                break

            target_range = found_range.getStart()
            if command == "t":
                target_range = _offset_range(text, target_range, -1)
            elif command == "T":
                target_range = _offset_range(text, target_range, 1)
            if target_range is not None and visual_forward_extra:
                extra = _offset_range(text, target_range, 1)
                if extra is not None:
                    target_range = extra

            if target_range is None:
                break

            if not _range_same_start(text_cursor.getStart(), target_range):
                moved_any = True

            probe = text.createTextCursorByRange(target_range)
            _sync_view_cursor_to_text_cursor(probe, expand, cursor, backward=backward)

        return moved_any
    except Exception:
        return False


def _repeat_last_to_character(count: int, expand: bool, key: KeyEvent, cursor) -> bool:
    """ Repeat last to-character motion (commands f, F, t, T). Commands ';' and ','.
        key.char ';' : use same direction
        key.char ',' : use opposite direction
    """
    try:
        last_ft = _get_last_ft()

        if not last_ft or not last_ft["type"] or not last_ft["type"]:
            return False

        ft_type = last_ft["type"]
        ft_char = last_ft["char"]

        if not isinstance(ft_type, str) or \
            not isinstance(ft_char, str) or \
            len(ft_char) != 1:
            return False

        match key.char:
            case ",":
                search_type = ft_type.swapcase()
            case ";":
                search_type = ft_type
            case _:
                return False

        text_cursor = _get_text_cursor()
        if text_cursor is None:
            return False

        match search_type:
            case "t":
                text_cursor.goRight(1, expand)
            case "T", "F":
                text_cursor.goLeft(1, expand)
            case _:
                pass

        if len(search_type) != 1:
            return False

        return _to_character(expand, count, cursor, search_type, ft_char)

    except Exception:
        return False


# ------------------
# Lines
# ------------------


def _characters_left(count, expand, mode, cursor) -> bool:
    """Motion for [count] characters left. Command 'h', <Left> or <BS>."""
    try:
        if mode == "pending":
            cursor.collapseToStart()
        return bool(cursor.goLeft(count, expand))
    except Exception:
        return False


def _characters_right(count, expand, mode, cursor) -> bool:
    """Motion for [count] characters right. Command 'l' or <Right>. """
    try:
        if mode == "pending":
            cursor.collapseToStart()
        return bool(cursor.goRight(count, expand))
    except Exception:
        return False


def _lines_up(count:int, expand:bool, mode: Mode, cursor) -> bool:
    """Motion for [count] lines up. Command 'k' or <Up>. """
    try:
        if mode == "pending":
            _to_end_of_line(False, 1, None, cursor)
            # At a soft-wrap point the inter-word space sits at the start of
            # the next visual line. Step past it so the selection includes it
            # and doesn't get left behind as a leading space after deletion.
            tc = _get_text_cursor()
            if tc is not None and not tc.isEndOfParagraph():
                cursor.goRight(1, False)
                cursor.gotoStartOfLine(True)
                count += 1
        return bool(cursor.goUp(count, expand))
    except Exception:
        return False


def _lines_down(count:int, expand:bool, mode: Mode, cursor) -> bool:
    """Motion for [count] lines down. Command 'j' or <Down>. """
    try:
        if mode == "pending":
            cursor.gotoStartOfLine(False)
            count += 1
        return bool(cursor.goDown(count, expand))
    except Exception:
        return False


def _to_start_of_line(expand: bool, mode, cursor) -> bool:
    """Motion to start of line. Command '0' or <Home>"""
    if mode == "pending":
        cursor.collapseToStart()
    cursor.gotoStartOfLine(expand)
    return True


def _to_first_non_blank(expand, count, cursor, up: bool = False) -> bool:
    """Motion to first non-blank character in current line, [count] lines down
       or up if 'up' is True. Commands '^', '-', '+', <CR>.
    """
    try:
        if count:
            if up:
                cursor.goUp(count, expand)
            else:
                cursor.goDown(count, expand)

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


def _to_end_of_line(expand:bool, count:int, mode, cursor) -> bool:
    """Motion to end of line and optionally [count] -1 lines down.
    Command '$' or <End>.
    """
    try:
        # Range of motion needs start where caret is, not after Normal/Pending mode cursor.
        if mode == "pending":
            cursor.collapseToStart()

        if count > 1:
            cursor.goDown(count - 1, expand)
        old_pos = cursor.getPosition()

        cursor.gotoEndOfLine(expand)
        new_pos = cursor.getPosition()

        _, old_y = _pos_xy(old_pos)
        _, new_y = _pos_xy(new_pos)

        if not expand:
            # LibreOffice can place cursor visually at next line start; move left
            # back to previous line end unless this was an empty-line no-op.
            if cursor.isAtStartOfLine() and old_y != new_y:
                cursor.goLeft(1, expand)

        return True
    except Exception:
        return False


def _select_linewise(cursor) -> bool:
    """Expand selection to cover full lines. Command 'S' and in visual mode
       commands 'C', 'D', 'X', 'Y'."""
    text_cursor = _get_text_cursor()
    if text_cursor is None:
        return False
    try:
        # X in visual mode: expand selection to cover full visual lines.
        # gotoStartOfLine/gotoEndOfLine are view cursor methods, so use
        # the view cursor to navigate to each end of the selection first.
        sel_start = text_cursor.getStart()
        sel_end   = text_cursor.getEnd()
        cursor.gotoRange(sel_start, False)
        cursor.gotoStartOfLine(False)
        cursor.gotoRange(sel_end, True)
        cursor.gotoEndOfLine(True)
        return True
    except Exception:
        return False


# ------------------
# Character editing
# ------------------

# Insert, delete, replace characters

def _append_text(cursor):
    """Command 'a'."""
    textCursor = _get_text_cursor()
    try:
        if textCursor is not None and not textCursor.isEndOfParagraph():
            cursor.goRight(1, False)
    except Exception:
        pass


def _append_text_to_end_of_line(mode: Mode = "normal", cursor=None):
    """Command 'A'."""
    try:
        if mode == "visual":
            if cursor is not None:
                cursor.gotoRange(cursor.getEnd(), False)
        _to_end_of_line(False, 1, None, cursor)
        return
    except Exception:
        pass


def _insert_before_first_non_blank(mode, cursor):
    """Command 'I'."""
    try:
        if mode == "visual":
            # Move to the line where the selection starts before going to line start.
            cursor.gotoRange(cursor.getStart(), False)
        return _to_first_non_blank(False, 0, cursor)
    except Exception:
        pass


def _begin_new_paragraph(above: bool, cursor):
    """Begin to write new paragraph above or below current line.
       Normal mode commands 'o', 'O'."""
    try:
        if above:
            cursor.gotoStartOfLine(False)
        else:
            _to_end_of_line(False, 0, None, cursor)
            cursor.goRight(1, False)

        cursor.setString(chr(13))  # CR
        if not cursor.isAtStartOfLine():
            cursor.goLeft(1, False)
            cursor.setString(chr(13) + chr(13))
            cursor.goRight(1, False)
        return True

    except Exception:
        return False


def _copy_and_delete_linewise(yank: bool, delete: bool) -> bool:
    """Copy and/or delete whole lines where selection is.
       Command 'S' and 'C', 'D', 'X', 'Y' in Visual mode.
    """
    cursor = _get_cursor()
    if cursor is None:
        return False
    _select_linewise(cursor)
    return _copy_and_delete(yank, delete)


def _delete_characters(count:int, backward: bool = False) -> bool:
    """Delete single characters. Normal mode commands 'x','X' and 's'."""
    try:
        text_cursor = _get_text_cursor()
        if text_cursor is None:
            return False
        # Collapse to start of normal-mode block cursor position
        text_cursor.gotoRange(text_cursor.getStart(), False)
        if backward:
            # Delete count chars to the left; bail if already at start of line
            if not text_cursor.goLeft(count, True):
                return False
        else:
            # Delete count chars from cursor position rightward
            if not text_cursor.goRight(count, True):
                return False
        text_cursor.setString("")
        return True
    except Exception:
        return False


def _replace_characters(count:int, key:KeyEvent, cursor) -> bool:
    """Replace character(s) under cursor with {key_char}.
       With count replace [count] characters with [count] {key_char}.
       Command 'r'.
    """
    try:
        length = len(cursor.getString())

        if length > 1:
            cursor.setString(key.char * length)
        else:
            cursor.setString(key.char * count)

        return True
    except Exception:
        return False


# -----------------------
# Operators and clipboard
# -----------------------

def _yank(key, mode:Mode) -> bool:
    """Yanks text {motion} moves over. Commands `y`, `yy`.
       Flashes yanked range.
    """
    _copy_and_delete(True, False)
    position = _get_position()
    cursor = _get_cursor()
    if (position is not None and cursor is not None) and \
        (mode != "visual" or key.char == "Y"):
        # Keep yanked range visually selected briefly, then restore cursor.
        # Use _set_mode instead of _goto_mode so the selection isn't
        # collapsed immediately by _show_cursor("normal")().
        def _flash_restore():
            if key.char != "h":
                cursor.gotoRange(position, False)
            _show_cursor("normal")
        threading.Timer(0.1, _flash_restore).start()
    return True


def _copy_and_delete(yank:bool, delete:bool) -> bool:
    """Copy and/or delete selection to clipboard.
       yank  : Yank selection
       delete: Delete selection
    """
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


def _yank_and_delete_to_end_of_line(count: int, mode: Mode, yank: bool , delete: bool = True) -> bool:
    """ Delete the characters until the end of the line and
        [count] - 1 more lines. Normal mode commands 'C', 'D', 'Y'."""
    # Makes cursor to collapse to get correct range.
    motion_mode = "pending" if mode == "normal" else mode
    cursor = _get_cursor()
    if cursor is None:
        return False
    _to_end_of_line(True, count, motion_mode, cursor)
    return _copy_and_delete(yank, delete)


def _paste(count:int, mode, after_cursor:bool = True):
    """Paste text from clipboard after or before cursor [count] times.
       Commands 'p' and 'P'.
    """
    text_cursor = _get_text_cursor()
    cursor = _get_cursor()
    if text_cursor is None or cursor is None:
        return False

    try:
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

    except Exception:
        return False


def _undo(count=1) -> bool:
    """Undo changes. Command 'u'."""
    doc = _current_doc()
    if doc is None:
        return False
    try:
        for _ in range(count):
            doc.getUndoManager().undo()
        return True
    except Exception:
        # Non-fatal when no more undo actions exist.
        return False


def _redo(count=1) -> bool:
    """Redo changes. Command 'C-r' or 'U'."""
    doc = _current_doc()
    if doc is None:
        return False
    try:
        for _ in range(count):
            doc.getUndoManager().redo()
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
_WORD_OBJECT_UNIT_FORWARD = {
    "direction": FORWARD,
    "target": END,
    "big_word": False,
    "cross_empty": True,
    "inclusive": True,
    "unit_mode": True,
}
_WORD_OBJECT_UNIT_BACKWARD = {
    "direction": BACKWARD,
    "target": START,
    "big_word": False,
    "cross_empty": True,
    "inclusive": False,
    "unit_mode": True,
}
_WORD_OBJECT_UNIT_BIG_FORWARD = {
    "direction": FORWARD,
    "target": END,
    "big_word": True,
    "cross_empty": True,
    "inclusive": True,
    "unit_mode": True,
}
_WORD_OBJECT_UNIT_BIG_BACKWARD = {
    "direction": BACKWARD,
    "target": START,
    "big_word": True,
    "cross_empty": True,
    "inclusive": False,
    "unit_mode": True,
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


def _clone_text_range(text_cursor) -> XTextCursor | None:
    """Return a cloned TextCursor positioned at the start of text_cursor.
    """
    try:
        return text_cursor.getText().createTextCursorByRange(text_cursor.getStart())
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
        if DEBUG:
            try:
                print(
                    "iw debug: unit_mode backward",
                    f"offset={offset}",
                    f"len={length}",
                    f"bounds={bounds}",
                )
            except Exception:
                pass
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


def _word_motion_once_forward(text_cursor, expand: bool, spec) -> bool:
    cross_empty = bool(spec.get("cross_empty", True))
    paragraph_text, offset = _current_paragraph_text_and_offset(text_cursor)
    length = len(paragraph_text)

    if length == 0:
        return _goto_next_paragraph_with_policy(text_cursor, expand, cross_empty)

    if offset >= length:
        return _goto_next_paragraph_with_policy(text_cursor, expand, cross_empty)

    next_offset = _scan_forward_word_target(paragraph_text, offset, spec)

    if next_offset is None or next_offset >= length:
        return _goto_next_paragraph_with_policy(text_cursor, expand, cross_empty)

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

    if DEBUG:
        try:
            print(
                "iw debug: backward fell through",
                f"offset={offset}",
                f"len={length}",
                f"prev_offset={prev_offset}",
                f"text_snip={paragraph_text[max(0, offset-10):offset+10]!r}",
            )
        except Exception:
            pass

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
        if not expand:
            moved = cursor.gotoRange(end_range, False)
            return True if moved else False

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


def _range_at_paragraph_offset(paragraph_cursor, offset: int):
    try:
        probe = paragraph_cursor.getText().createTextCursorByRange(paragraph_cursor.getStart())
        probe.gotoStartOfParagraph(False)
        if offset > 0:
            probe.goRight(offset, False)
        return probe.getStart()
    except Exception:
        return None


def _word_object_start_range(text_cursor, big_word: bool = False):
    """Return range at the start of the current word/whitespace unit."""
    try:
        paragraph_text, offset = _current_paragraph_text_and_offset(text_cursor)
        if len(paragraph_text) == 0:
            return _range_at_paragraph_offset(text_cursor, 0)
        start_offset, _ = _word_unit_bounds(paragraph_text, offset, big_word)
        return _range_at_paragraph_offset(text_cursor, start_offset)
    except Exception:
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
    except Exception:
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
                except Exception:
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
            except Exception:
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
    except Exception:
        return None, None


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
    except Exception:
        return None


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
        probe = text_cursor.getText().createTextCursorByRange(text_cursor.getStart())
        start_range, end_range = _around_word_ranges_forward(probe, steps, big_word)
    else:
        start_range = _word_object_start_range(text_cursor, big_word)
        if start_range is None:
            return False
        end_range = _advance_word_object_caret(text_cursor, start_range, steps, \
                                               FORWARD, big_word)
    if end_range is None:
        return False
    if mode == "visual":
        _set_visual_anchor(start_range)
        _set_visual_selection(cursor, start_range, end_range)
        return True
    cursor.gotoRange(start_range, False)
    cursor.gotoRange(end_range, True)
    return True


def _expand_with_word_text_objects(count, key, mode, cursor) -> bool:
    """Expand selection with [count] word text-objects 'iw' in Visual mode to
    direction of selection. Command 'iw' in Visual mode with extended selection.
    """
    text_cursor = _get_text_cursor()
    if text_cursor is None:
        return False

    is_around = True if key.pending and key.pending[-1] == "a" else False

    # Start from beginning of current word if haven't expanded selection.
    if len(cursor.getString()) <= 1:
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
    except Exception:
        return False


# ------------------
# Sentence motions
# ------------------

def _is_cursor_at_whitespace(text_cursor, condition:str|None=None) -> bool:
    """Return True if cursor is on a whitespace character.

    condition: optional qualifier for additional check:
        None               – any whitespace at cursor position.
        "after_sentence"   – whitespace that immediately follows a sentence end
                             (., !, ?), ruling out mid-sentence whitespace.
        "before_paragraph" – whitespace at the start of a paragraph (paragraph
                             begins with whitespace characters).
    """
    if text_cursor is None:
        return False
    try:
        probe = text_cursor.getText().createTextCursorByRange(text_cursor.getStart())
        if not probe.goRight(1, True):
            return False
        if probe.getString() not in (" ", "\t", "\n"):
            return False

        if not condition:
            return True

        if _is_current_paragraph_empty(text_cursor):
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
            # Check that the cursor is within leading whitespace of the paragraph.
            para_probe = text_cursor.getText().createTextCursorByRange(text_cursor.getStart())
            para_probe.gotoStartOfParagraph(False)
            para_probe.gotoRange(text_cursor.getStart(), True)
            leading = para_probe.getString()
            return len(leading) == 0 or all(c in (" ", "\t") for c in leading)
        else:
            return False
    except Exception:
        return False


def _move_to_sentence_whitespace_start(text_cursor) -> bool:
    """Move text cursor to the start of the current whitespace unit."""
    if text_cursor is None:
        return False
    try:
        probe = text_cursor.getText().createTextCursorByRange(text_cursor.getStart())
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
    except Exception:
        return False


def _normalize_sentence_unit_start(text_cursor) -> bool:
    """Normalize to the start of current sentence unit for 'is'."""
    if text_cursor is None:
        return False
    try:
        if _is_current_paragraph_empty(text_cursor):
            text_cursor.gotoStartOfParagraph(False)
            return True
        if _is_cursor_at_whitespace(text_cursor, "after_sentence") or \
                _is_cursor_at_whitespace(text_cursor, "before_paragraph"):
            return _move_to_sentence_whitespace_start(text_cursor)
        if not _is_at_sentence_start(text_cursor):
            text_cursor.gotoStartOfSentence(False)
        return True
    except Exception:
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


def _to_end_of_sentence(text_cursor) -> bool:
    """Move text cursor to end of current sentence for 'is'."""
    if text_cursor is None:
        return False
    try:
        probe = text_cursor.getText().createTextCursorByRange(text_cursor.getStart())
        if not probe.gotoNextSentence(False):
            probe.gotoEndOfParagraph(False)
        if not _move_probe_to_sentence_end(probe):
            return False
        text_cursor.gotoRange(probe.getStart(), False)
        text_cursor.goRight(1, False)
        return True
    except Exception:
        return False


def _advance_empty_paragraph_unit_forward(text_cursor) -> bool:
    """Advance cursor over a single empty paragraph unit."""
    if text_cursor is None or not _is_current_paragraph_empty(text_cursor):
        return False
    try:
        if not text_cursor.isCollapsed():
            caret = _get_visual_caret_range(text_cursor)
            text_cursor.gotoRange(caret, False)
        text_cursor.gotoStartOfParagraph(False)
        if text_cursor.gotoNextParagraph(False):
            return True
        if text_cursor.goDown(1, False):
            return True
        end_after = _range_after_paragraph_break(text_cursor.getEnd())
        if end_after is not None:
            text_cursor.gotoRange(end_after, False)
            return True
        text_cursor.gotoEndOfParagraph(False)
        return True
    except Exception:
        return False


def _inner_sentences_forward(text_cursor, count: int) -> bool:
    """Advance over [count] inner sentences forward for 'is'. Whitespace
    before paragraph or between sentences is also inner sentence.
    """
    if text_cursor is None:
        return False
    try:
        if not text_cursor.isCollapsed():
            caret = _get_visual_caret_range(text_cursor)
            text_cursor.gotoRange(caret, False)
        steps = max(1, int(count))
        moved_any = False
        for _ in range(steps):
            if _is_current_paragraph_empty(text_cursor):
                moved = _advance_empty_paragraph_unit_forward(text_cursor)
            elif _is_cursor_at_whitespace(text_cursor, "after_sentence"):
                moved = text_cursor.gotoNextWord(False)
            else:
                moved = _to_end_of_sentence(text_cursor)
            if not moved:
                break
            moved_any = True
        return moved_any
    except Exception:
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
    except Exception:
        return False


def _to_whitespace_start(text_cursor, expand: bool, cursor) -> bool:
    try:
        if not _move_to_sentence_whitespace_start(text_cursor):
            return False
        _sync_view_cursor_to_text_cursor(text_cursor, expand, cursor, backward=True)
        return True
    except Exception:
        return False


def _to_start_of_next_sentence(text_cursor, expand: bool, cursor) -> bool:
    """To next start of a sentence. Commands ')' and 'as' text-objects.

    Handles edge cases: empty paragraphs (jumps to next non-empty), leading paragraph
    whitespace, and backends that stall on paragraph-end markers.
    """
    old_pos = cursor.getPosition()

    # From an empty line, jump directly to the next non-empty paragraph.
    if _is_current_paragraph_empty(text_cursor):
        moved = _to_next_non_empty_paragraph(text_cursor, expand)
        if moved:
            if _is_cursor_at_whitespace(text_cursor, "before_paragraph"):
                text_cursor.gotoNextWord(expand)
            _sync_view_cursor_to_text_cursor(text_cursor, expand, cursor)
        return moved

    # From leading whitespace of a paragraph, gotoNextSentence would skip the
    # first sentence entirely. Jump to the next word instead, which lands at
    # the start of that sentence.
    if _is_cursor_at_whitespace(text_cursor, "before_paragraph"):
        moved = text_cursor.gotoNextWord(expand)
        if moved:
            _sync_view_cursor_to_text_cursor(text_cursor, expand, cursor)
        return moved

    text_cursor.gotoNextSentence(expand)
    _sync_view_cursor_to_text_cursor(text_cursor, expand, cursor)

    # Some backends land on the paragraph end marker first; skip that stop.
    if text_cursor.isEndOfParagraph() and not _is_current_paragraph_empty(text_cursor):
        text_cursor.gotoNextParagraph(expand)
        _sync_view_cursor_to_text_cursor(text_cursor, expand, cursor)
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
        _sync_view_cursor_to_text_cursor(text_cursor, expand, cursor)
    return True


def _start_of_sentences_forward(expand: bool, count: int, cursor) -> bool:
    """To start of [count] sentences forward. Commands ')', 'as'."""
    text_cursor = _get_text_cursor()
    if text_cursor is None:
        return False
    try:
        if expand:
            # Collapse to the caret end so forward scan starts from the right place.
            caret = _get_visual_caret_range(text_cursor)
            text_cursor.gotoRange(caret, False)
        steps = max(1, int(count))
        moved_any = False

        for _ in range(steps):
            if not _to_start_of_next_sentence(text_cursor, expand, cursor):
                break
            moved_any = True
        return moved_any
    except Exception:
        return False


# LibreOffice isStartOfSentence() doesn't work with non-collapsed cursors and
# have locale boundary quirks, so we use a punctuation/whitespace heuristic.
def _is_at_sentence_start(text_cursor) -> bool:
    if text_cursor is None:
        return False
    try:
        # Whitespace is never a sentence start.
        if _is_cursor_at_whitespace(text_cursor):
            return False
        probe = text_cursor.getText().createTextCursorByRange(text_cursor.getStart())
        if probe.isStartOfParagraph():
            return True
        ch = ""
        while True:
            if not probe.goLeft(1, True):
                # Reached document start through whitespace only.
                return True
            ch = probe.getString()
            probe.collapseToStart()
            if probe.isStartOfParagraph():
                # Walked back to paragraph start through whitespace only.
                return True
            if ch not in (" ", "\t", "\"", "'", ")", "]"):
                break
        return ch in (".", "!", "?")
    except Exception:
        return False


def _to_start_of_previous_sentence(text_cursor, expand:bool, cursor) -> bool:
    """Move cursor to the start of the previous sentence. Commands '(', 'is'.

    Handles edge cases: leading paragraph whitespace (collapses to paragraph start to
    trigger boundary crossing), first non-whitespace after leading whitespace (detected
    via heuristic and collapsed to paragraph start), and mid-sentence positions.
    """
    old_pos = cursor.getPosition()

    # From leading whitespace of a paragraph, gotoStartOfSentence moves forward
    # to the first sentence content rather than backward. Collapse to the real
    # paragraph start so the isStartOfParagraph() branch below handles crossing
    # to the previous paragraph correctly.
    force_paragraph_boundary = False
    if _is_cursor_at_whitespace(text_cursor, "before_paragraph"):
        text_cursor.gotoStartOfParagraph(False)
        _sync_view_cursor_to_text_cursor(text_cursor, expand, cursor, backward=True)
        force_paragraph_boundary = True
        # Fall through — cursor is now at isStartOfParagraph(), handled below.

    # From inside a sentence, first motion should go to current sentence start.
    # Skip this for empty paragraphs so we can cross to previous sentence.
    if (not force_paragraph_boundary
            and not _is_at_sentence_start(text_cursor)
            and not _is_current_paragraph_empty(text_cursor)):
        text_cursor.gotoStartOfSentence(expand)
        _sync_view_cursor_to_text_cursor(text_cursor, expand, cursor, backward=True)
        return True

    # If cursor is at the first non-whitespace character after leading paragraph
    # whitespace, isStartOfParagraph() is False but we must treat it as paragraph
    # start so the boundary logic below crosses to the previous paragraph.
    if not force_paragraph_boundary and not text_cursor.isStartOfParagraph():
        try:
            para_probe = text_cursor.getText().createTextCursorByRange(text_cursor.getStart())
            para_probe.gotoStartOfParagraph(False)
            para_probe.gotoRange(text_cursor.getStart(), True)
            if all(c in (" ", "\t") for c in para_probe.getString()):
                text_cursor.gotoStartOfParagraph(False)
                _sync_view_cursor_to_text_cursor(text_cursor, expand, cursor, backward=True)
        except Exception:
            pass

    # Paragraph-boundary behavior matching logic.
    if text_cursor.isStartOfParagraph():
        if _is_current_paragraph_empty(text_cursor):
            moved = text_cursor.gotoPreviousParagraph(expand)
            if not moved:
                return False
            while _is_current_paragraph_empty(text_cursor):
                if not text_cursor.gotoPreviousParagraph(expand):
                    _sync_view_cursor_to_text_cursor(text_cursor, expand, cursor, backward=True)
                    return True
        else:
            if text_cursor.gotoPreviousParagraph(expand):
                if _is_current_paragraph_empty(text_cursor):
                    _sync_view_cursor_to_text_cursor(text_cursor, expand, cursor, backward=True)
                    return True

        text_cursor.gotoEndOfParagraph(expand)
        if not text_cursor.isStartOfParagraph():
            text_cursor.goLeft(1, expand)
        text_cursor.gotoStartOfSentence(expand)
        _sync_view_cursor_to_text_cursor(text_cursor, expand, cursor, backward=True)
        return True

    text_cursor.gotoPreviousSentence(expand)
    _sync_view_cursor_to_text_cursor(text_cursor, expand, cursor, backward=True)
    if _same_pos(old_pos, cursor.getPosition()):
        if text_cursor.goLeft(1, expand):
            text_cursor.gotoPreviousSentence(expand)
        _sync_view_cursor_to_text_cursor(text_cursor, expand, cursor, backward=True)
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
            moved = _to_start_of_previous_sentence(text_cursor, expand, cursor)
            _sync_view_cursor_to_text_cursor(text_cursor, expand, cursor, backward=True)
            if not moved:
                break
            moved_any = True
        return moved_any
    except Exception:
        return False


def _select_sentence_text_objects(count: int, key: KeyEvent, cursor):
    """Select 'as' or 'is' sentence text-objects forward from current object.
       Operator-pending mode or Visual mode without extended selection.
    """
    text_cursor = _get_text_cursor()
    if text_cursor is None:
        return False
    is_around = key.pending is not None and key.pending[-1] == "a"

    if _is_cursor_at_whitespace(text_cursor, "after_sentence"):
        _to_whitespace_start(text_cursor, False, cursor)
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

    elif _is_cursor_at_whitespace(text_cursor, "before_paragraph"):
        _start_of_sentences_forward(False, 1, cursor)

    elif _is_current_paragraph_empty(text_cursor):
        if is_around:
            # Empty paragraph counts as one unit; around includes trailing whitespace unit.
            count = count * 2
            is_around = False

    elif not _is_at_sentence_start(text_cursor):
        _to_start_of_sentences_backwards(False, 1, cursor)

    text_cursor = _get_text_cursor()
    if text_cursor is None:
        return False

    _set_visual_anchor(text_cursor.getStart())

    if is_around:
        return _start_of_sentences_forward(True, count, cursor)

    probe = _clone_text_range(text_cursor)
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

    if len(cursor.getString()) <= 1:
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

    text_cursor = _get_text_cursor()
    if text_cursor is None:
        return False

    anchor = cursor.getStart() if select_forward else cursor.getEnd()
    _set_visual_anchor(anchor)
    caret = cursor.getEnd() if select_forward else cursor.getStart()
    if caret is None:
        return False

    probe = text_cursor.getText().createTextCursorByRange(caret)

    if select_forward:
        moved = _inner_sentences_forward(probe, count)
    else:
        moved = _inner_sentences_backward(probe, count)

    if not moved:
        return False

    new_caret = probe.getStart()
    _set_visual_selection(cursor, anchor, new_caret)
    return True


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
    except Exception:
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
                break
            moved_any = True
        if moved_any:
            _sync_view_cursor_to_text_cursor(text_cursor, expand, cursor)
        return moved_any
    except Exception:
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
    except Exception:
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

    if mode == "visual":
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

    if len(cursor.getString()) <= 1:
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


# -----------------
# Input handling
# -----------------

# UNO key handler
# Return values for keyPressed/keyReleased:
#   True  -> event is swallowed (LibreOffice should not process it)
#   False -> event is passed through to LibreOffice default handling
class KeyHandler(unohelper.Base, XKeyHandler):
    def __init__(self):
        self._count: int = 0
        self._pending_keys: str | None = None

    # An optional number that may precede the command to multiply or
    # iterate the command.
    @property
    def count(self) -> int:
        count = self._count
        return 1 if count == 0 else count

    # Pending commands like 'd' or 'g'. Type str | None. Note that only
    # operator commands result to operator pending mode.
    @property
    def pending_keys(self) -> str | None:
        return self._pending_keys

    def get_raw_count(self) -> int:
        return self._count

    def _reset_count(self) -> bool:
        self._count = 0
        _update_statusline()
        return True

    def _add_to_count(self, n: int) -> bool:
        try:
            digit = int(n)
        except Exception:
            return False
        if digit < 0:
            return False
        if self.count > 1000:
            return False

        new_count = int(f"{self._count}{digit}")
        self._count = new_count
        _update_statusline()
        return True

    def _add_pending_key(self, new_key: str) -> bool:
        pending_keys = self._pending_keys
        if pending_keys is None:
            self._pending_keys = new_key
        else:
            self._pending_keys = pending_keys + new_key
        _update_statusline()
        return True

    def reset_pending_keys(self):
        self._pending_keys = None
        _update_statusline()
        return

    def _normal_ctrl_actions(self, expand: bool, mode: Mode):
        count = self.count
        a_code = int(getattr(Key, "A", 512))
        b_code = int(getattr(Key, "B", 513))
        c_code = int(getattr(Key, "C", 514))
        d_code = int(getattr(Key, "D", 515))
        f_code = int(getattr(Key, "F", 517))
        r_code = int(getattr(Key, "R", 529))
        u_code = int(getattr(Key, "U", 532))
        scroll_count = _get_scroll()

        actions = {
            a_code: lambda: _goto_mode("visual"),
            b_code: lambda: _scroll_window(expand, count, False, mode, False),
            c_code: lambda: self._ctrl_c_command(mode),
            d_code: lambda: _scroll_window(expand, count, True, mode, scroll_count),
            f_code: lambda: _scroll_window(expand, count, True, mode, False),
            r_code: lambda: _redo(count),
            u_code: lambda: _scroll_window(expand, count, False, mode, scroll_count),
        }
        return actions

    # Keymap for Normal mode commands and Visual mode commands which
    # aren't included in Visual mode keymap.
    def _normal_commands_keymap(self, key, mode, cursor):
        # Available commands after "g" command.
        count = self.count
        if "g" in (key.pending or ""):
            actions = {
                # Currently "g" has only motions.
            }
        else:
            do_yank = True if "_" not in (key.pending or "") else False
            actions = {
                "a": lambda: _append_text(cursor),
                "A": lambda: _append_text_to_end_of_line(mode, cursor),
                "c": lambda: self._c_d_commands(key, mode),
                "d": lambda: self._c_d_commands(key, mode),
                "C": lambda: _yank_and_delete_to_end_of_line(count, mode, do_yank),
                "D": lambda: _yank_and_delete_to_end_of_line(count, mode, do_yank),
                "Y": lambda: _yank_and_delete_to_end_of_line(count, mode, False, False),
                "i": lambda: True,
                "I": lambda: _insert_before_first_non_blank(mode, cursor),
                "o": lambda: _begin_new_paragraph(above = False, cursor = cursor),
                "O": lambda: _begin_new_paragraph(above = True, cursor = cursor),
                "p": lambda: _paste(count, mode),
                "P": lambda: _paste(count, mode, after_cursor=False),
                "r": lambda: self._r_command(count, key, cursor),
                "s": lambda: _delete_characters(count),
                "S": lambda: _copy_and_delete_linewise(yank = False, delete = True),
                "u": lambda: _undo(count),
                "U": lambda: _redo(count),
                "v": lambda: _goto_mode("visual"),
                "x": lambda: _delete_characters(count),
                "X": lambda: _delete_characters(count, backward = True),
                "y": lambda: self._y_command(key, mode),
                "/": _focus_findbar,
                # "n": lambda: _repeat_search(count),
                # "N": lambda: _repeat_search(count, backward=True),
            }

        return actions

    def _visual_commands_keymap(self, key, mode, cursor):
        do_yank = True if "_" not in (key.pending or "") else False
        actions = {
            "C": lambda: _copy_and_delete_linewise(do_yank, delete = True),
            "D": lambda: _copy_and_delete_linewise(do_yank, delete = True),
            "S": lambda: _copy_and_delete_linewise(yank = False, delete = True),
            "X": lambda: _copy_and_delete_linewise(do_yank, delete = True),
            "Y": lambda: _copy_and_delete_linewise(do_yank, delete = False),
            "o": lambda: _go_to_other_end(mode, cursor),
            "O": lambda: _go_to_other_end(mode, cursor),
            "v": lambda: _goto_mode("visual"),
            "c": lambda: _copy_and_delete(do_yank, True),
            "d": lambda: _copy_and_delete(do_yank, True),
            "s": lambda: _copy_and_delete(yank = False, delete = True),
            "x": lambda: _copy_and_delete(yank = False, delete = True),
            "y": lambda: _yank(key, mode),
        }
        return actions

    # These can be used independently or with operators.
    def _motions_keymap(self, key, expand, mode: Mode, cursor):
        count = self.count
        # Available motions after "g" command.
        if "g" in (key.pending or ""):
            motions = {
                "g": lambda: _to_line(expand, self.get_raw_count(), False, mode, cursor),
                "e": lambda: _word_motion(_WORD_MOTION_GE, expand, count, mode),
                "E": lambda: _word_motion(_WORD_MOTION_G_BIG_E, expand, count, mode)
            }
        else:
            motions = {
                "h": lambda: _characters_left(count, expand, mode, cursor),
                "l": lambda: _characters_right(count, expand, mode, cursor),
                "j": lambda: _lines_down(count, expand, mode, cursor),
                "k": lambda: _lines_up(count, expand, mode, cursor),
                "b": lambda: _word_motion(_WORD_MOTION_B, expand, count, mode),
                "e": lambda: _word_motion(_WORD_MOTION_E, expand, count, mode),
                "w": lambda: _word_motion(_WORD_MOTION_W, expand, count, mode),
                "B": lambda: _word_motion(_WORD_MOTION_BIG_B, expand, count, mode),
                "E": lambda: _word_motion(_WORD_MOTION_BIG_E, expand, count, mode),
                "W": lambda: _word_motion(_WORD_MOTION_BIG_W, expand, count, mode),
                "^": lambda: _to_first_non_blank(expand, 0, cursor),
                "$": lambda: _to_end_of_line(expand, count, mode, cursor),
                "H": lambda: _jump_to_page(expand, count, cursor, "start"),
                "L": lambda: _jump_to_page(expand, count, cursor, "end"),
                "G": lambda: _to_line(expand, self.get_raw_count(), True, mode, cursor),
                ")": lambda: _start_of_sentences_forward(expand, count, cursor),
                "(": lambda: _to_start_of_sentences_backwards(expand, count, cursor),
                "}": lambda: _paragraphs_forward(expand, count, cursor),
                "{": lambda: _paragraphs_backward(expand, count, cursor),
                # For testing.
                ";": lambda: _repeat_last_to_character(count, expand, key, cursor),
                ",": lambda: _repeat_last_to_character(count, expand, key, cursor),
                "+": lambda: _to_first_non_blank(expand, count, cursor, False),
                "-": lambda: _to_first_non_blank(expand, count, cursor, True),
                "_": lambda: _to_first_non_blank(expand, count - 1, cursor, False),
            }
            if key.char == "0" and self.get_raw_count() == 0:
                motions["0"] = lambda: _to_start_of_line(expand, mode, cursor)

        return motions

    def _normal_text_objects(self, key, mode, cursor):
        count = self.count
        text_objects = {
            "s": lambda: _select_sentence_text_objects(count, key, cursor),
            "p": lambda: _select_paragraph_text_objects(count, key, mode, cursor),
            "w": lambda: _select_word_objects_forward(count, key, mode, cursor),
            "W": lambda: _select_word_objects_forward(count, key, mode, cursor),
        }
        return text_objects

    def _visual_text_objects(self, key, mode, cursor):
        count = self.count
        text_objects = {
            "s": lambda: _expand_with_sentences_objects(count, key, cursor),
            "p": lambda: _expand_with_paragraph_objects(count, key, mode, cursor),
            "w": lambda: _expand_with_word_text_objects(count, key, mode, cursor),
            "W": lambda: _expand_with_word_text_objects(count, key, mode, cursor),
        }
        return text_objects

    # ------------------------------------------
    def keyPressed(self, event):
        state = _state()
        if not state["enabled"]:
            return False

        # Don't do anything if cursor isn't working (as in annotations).
        cursor = _get_cursor()
        if _get_text_cursor() is None or cursor is None:
            return False

        mode: Mode = _get_mode()
        mods: int = _event_modifiers(event)
        code = _key_code(event)
        is_ctrl = _is_only_ctrl(mods)
        is_escape = _is_escape(code, is_ctrl)

        # Insert mode matching. Do as little as possible.
        if mode == "insert":
            if is_escape or (is_ctrl and code == 514):  # C-c
                return self._ctrl_c_command(mode)
            return False

        key = KeyEvent(
            char = _normalize_key_char(event),
            code = code,
            pending = self.pending_keys
        )

        count: int = self.count
        expand: bool = _get_mode() in ("visual", "pending")

        # --- Keys with non-shift/AltGr modifiers -------

        if is_ctrl:
            run_command = self._match_ctrl_commands(key, expand, mode)
            if run_command is None:
                return False
            # Allow LO to handle Ctrl-A.
            a_code = int(getattr(Key, "A", 512))
            if key.code == a_code:
                return False
            return True

        # Pass other non-shift modified shortcuts through, except characters
        # made with AltGr.
        if _has_non_shift_modifier(event):
            if not bool(_is_altgr_char(event, key)):
                return False

        # --- Keys without modifiers after this --------

        if key.pending == "r":
            if key.char.isprintable() or key.code in (1280, 1282):  # enter, tab
                _replace_characters(count, key, cursor)
                self._reset_count()
                _goto_mode("normal")
            self.reset_pending_keys()
            return True

        # Don't match navigation keys like "Home" or "PageUp" if non-operator command
        # is pending.
        if not (key.pending and mode != "pending"):
            matched_action = self._navigation_keys(expand, mode, cursor).get(key.code)
            if callable(matched_action):
                matched_action()
                return self._reset_count()
            elif matched_action is not None:
                key = KeyEvent(char=matched_action, code=key.code, pending=key.pending)

        # Count parsing. 1..9 always extend count. 0 extends count only after
        # count has started.
        if _is_digit_char(key.char):
            if key.char != "0" or _get_raw_count() > 0:
                self._add_to_count(int(key.char))
                return True

        # Matches prefixes like "f", "g", "i". Must be before motions.
        added_prefix = self._match_motion_prefix(key, mode)
        if added_prefix is not None:
            return True

        moved = self._match_motions(key, mode, cursor)
        if moved is not None:
            if mode == "pending":
                if moved:
                    return self._apply_pending_operator(key, mode)
                else:
                    _reset_count()
                    _goto_mode("normal")
            return True

        if mode == "visual":
            run_command = self._match_visual_commands(key, mode, cursor)
            if run_command is not None:
                return True

        run_command = self._match_normal_commands(key, mode, cursor)
        if run_command is not None:
            return True

        # No suitable commands matched for "g" so cancel.
        if "g" in (key.pending or ""):
            self._reset_count()
            _goto_mode("normal")
            return True

        # ----- Non-character keys after this -----

        if _is_function_key(event):
            return False

        # No suitable key matched so reset.
        self._reset_count()

        if key.pending or is_escape:
            _goto_mode("normal")
            return True

        # Deliberately let possible Operator-pending mode get canceled before
        # match Del key so it can be used for that.
        if _is_del_key(event):
            self._del_key(key, mode)
            return True

        if _is_insert_key(event):
            _goto_mode("insert")

        return True
    # -----------------------------------------
    # Match first letter for multi-part motions.
    def _match_motion_prefix(self, key, mode):

        motion_prefixes = 'fFtTgai'
        if key.pending and key.pending[-1] in motion_prefixes:
            return None

        if key.char in ('fFtT"'):
            self._add_pending_key(key.char)
            return True

        if key.char == "g" and (key.pending is None or mode == "pending"):
            self._add_pending_key("g")
            return True

        if key.pending == '"':
            if key.char == "_":
                self._add_pending_key(key.char)
                return True
            else:
                self._cancel_two_part_motion(mode)
                return True

        if mode not in ("pending", "visual"):
            return None

        if key.char in ("ai"):
            if key.pending is None or mode == "pending":
                return self._add_pending_key(key.char)
            else:
                return self._cancel_two_part_motion(mode)

        return None

    def _match_motions(self, key, mode: Mode, cursor):
        """Handle any matching motion."""
        expand: bool = mode in ("visual", "pending")
        count = self.count
        has_text_obj_prefix = key.pending[-1] in ("ai") if key.pending else False

        if has_text_obj_prefix:
            if mode == "visual":
                motions = self._visual_text_objects(key, mode, cursor)
            else:
                motions = self._normal_text_objects(key, mode, cursor)
        else:
            motions = self._motions_keymap(key, expand, mode, cursor)

        motion: Callable[[], bool] | None
        if key.pending:
            if key.pending[-1] in "fFtT":
                motion = partial(self._ft_commands, expand, key, mode)
            # For dd, cc, yy, S do motion lines down from current line.
            elif mode == "pending" and (key.pending[0] == key.char or key.char == "S"):
                motion = partial(_lines_down, count -1, True, mode, cursor)
            else:
                motion = motions.get(key.char)
        else:
            motion = motions.get(key.char)

        if motion is None:
            if has_text_obj_prefix:
                self._cancel_two_part_motion(mode)
                return False
            return None

        moved = motion()
        self._reset_prefix()
        self._reset_count()
        return moved

    def _ft_commands(self, expand, key, mode) -> bool:
        if key.pending[-1].lower() not in ("ft"):
            return False
        if not key.char.isprintable():
            if mode == "pending":
                _goto_mode("normal")
            return False
        _set_last_ft(key.pending[-1], key.char)
        count = self.count
        cursor = _get_cursor()
        if cursor is None:
            return False
        return _to_character(expand, count, cursor, key.pending[-1], key.char)

    def _match_visual_commands(self, key, mode, cursor):
        visual_actions = self._visual_commands_keymap(key, mode, cursor)
        action = visual_actions.get(key.char)
        if action is None:
            return None
        did_action = action()
        # After doing action:
        self._reset_count()
        if key.char.lower() in ("cs"):
            _goto_mode("insert")
        elif key.char not in ("oOr"):
            _goto_mode("normal")
        return did_action

    def _match_ctrl_commands(self, key, expand, mode):
        actions = self._normal_ctrl_actions(expand, mode)
        action = actions.get(key.code)
        if action is None:
            return None
        did_action = action()
        self._reset_count()
        return did_action

    def _match_normal_commands(self, key, mode, cursor):
        normal_actions = self._normal_commands_keymap(key, mode, cursor)
        action = normal_actions.get(key.char)
        if action is None:
            return None
        did_action = action()
        # After doing action:
        if key.char.lower() in ("aios"):
            self._reset_count()
            _goto_mode("insert")
        elif key.char in ("cdy"):
            _goto_mode("pending")
        elif key.char not in ("rv"):
            self._reset_count()
            _goto_mode("normal")
        return did_action

    def _navigation_keys(self, expand, mode: Mode, cursor):
        count = self.count
        backspace = int(Key.BACKSPACE)
        enter     = int(Key.RETURN)
        left      = int(Key.LEFT)
        right     = int(Key.RIGHT)
        up        = int(Key.UP)
        down      = int(Key.DOWN)
        home      = int(Key.HOME)
        end       = int(Key.END)
        pageup    = int(Key.PAGEUP)   # type: ignore[attr-defined]
        pagedown  = int(Key.PAGEDOWN)  # type: ignore[attr-defined]

        # Navigation keys are mapped to motions so they can take count, be used
        # with operators and for pageup/pagedown handle selection.
        return {
            backspace: "h",
            enter:     lambda: _to_first_non_blank(expand, count, cursor),
            left:      "h",
            right:     "l",
            up:        "k",
            down:      "j",
            home:      "0",
            end:       "$",
            pageup:    lambda: _scroll_window(expand, count, False, mode, False),
            pagedown:  lambda: _scroll_window(expand, count, True, mode, False),
        }

    # If for example after i/a motion non-valid key is entered.
    def _cancel_two_part_motion(self, mode):
        """In Pending mode, return to Normal. In Visual, reset pending
           keys and count but don't change mode."""
        if mode == "visual":
            self._reset_count()
            self.reset_pending_keys()
        else:
            _goto_mode("normal")
        return True

    @staticmethod
    def _apply_pending_operator(key, mode) -> bool:
        """Apply pending operator handling mode change."""
        if not key.pending:
            return False

        if "c" in key.pending or "d" in key.pending:
            _copy_and_delete("_" not in key.pending, True)
        elif "y" in key.pending:
            _yank(key, mode)
        else:
            return False

        new_mode = "insert" if key.pending[0] == "c" else "normal"
        _goto_mode(new_mode)
        return True

    def _c_d_commands(self, key, mode) -> bool:
        if mode == "normal" and key.char in ("cd"):
            self._add_pending_key(key.char)
            return True
        return _copy_and_delete("_" not in key.pending, True)

    def _ctrl_c_command(self, mode: Mode) -> bool:
        if mode == "normal":
            self.reset_pending_keys()
        elif mode == "visual":
            _copy_and_delete(True, False)
        _goto_mode("normal")
        return True

    def _del_key(self, key, mode) -> bool:
        if mode == "visual":
            _copy_and_delete(yank = True, delete = True)
        else:
             count = self.count
             if count:
                _delete_characters(count, key)
        _goto_mode("normal")
        return True

    def _reset_prefix(self) -> bool:
        """Remove last pending command if it's a command prefix:
           g, a/i, or f/F/t/T.
        """
        pending_keys = self._pending_keys
        if not pending_keys or pending_keys[-1] not in "aigfFtT":
            return False
        remaining_keys = pending_keys[:-1]
        if remaining_keys:
            self._pending_keys = remaining_keys
        else:
            self.reset_pending_keys()
        return True

    def _r_command(self, count, key, cursor) -> bool:
        if key.pending is None:
            self._add_pending_key("r")
            return True
        return _replace_characters(count, key, cursor)

    def _y_command(self, key, mode) -> bool:
        if mode == "normal" and key.pending is None:
            # Save cursor position so it can be restored after flashing
            # yanked region.
            _set_position()
            self._add_pending_key("y")
            return True
        return _yank(key, mode)

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


def _goto_mode(new_mode: Mode) -> bool:
    """Change Vi input mode to [new_mode]. Resets pending keys for other than
    Operator pending mode."""
    old_mode = _get_mode()
    if new_mode == "normal":
        _reset_pending_keys()
        if old_mode in ("normal", "pending"):
            pass

        elif old_mode == "insert":
            cursor = _get_cursor()
            if cursor is not None and not cursor.isAtStartOfLine():
                # Mimics Vi/Vim cursor behavior.
                cursor.goLeft(1, False)
            _show_cursor("normal")

        # Make selection start where caret is in Normal mode.
        elif old_mode == "visual":
            cursor = _get_cursor()
            controller = _get_controller()
            text_cursor = _get_text_cursor()
            try:
                if controller is not None and \
                    text_cursor is not None and  \
                    cursor is not None:
                    # Use the saved anchor to find the caret end before
                    # clearing it.
                    caret = _get_visual_caret_range(text_cursor)
                    text_cursor.gotoRange(caret, False)
                    if not cursor.isAtStartOfLine():
                        text_cursor.goLeft(1, False)
                    controller.select(text_cursor)
            finally:
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
    _restore_lo_default_cursor_all_views()


def toggle_viper_office():
    """Toggle enabling of ViperOffice"""
    state = _state()
    if state["enabled"] is True:
        disable_viper_office()
    else:
        enable_viper_office()


def _initialize():
    state = _state()
    state["started"] = True
    # Detach any previously registered handler before creating a new one.
    _detach_key_handler_from_all_views()
    state["key_handler"] = KeyHandler()
    _attach_key_handler_to_all_views()
    _start_view_event_listener()
    enable_viper_office()


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


def _restore_status_all_views():
    for controller in _iter_text_document_controllers():
        _restore_status_for_controller(controller)


def _restore_lo_default_cursor_all_views():
    for controller in _iter_text_document_controllers():
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
    controllers = state.get("mouse_listener_controllers")
    if listener is not None and controllers and id(controller) in controllers:
        try:
            controller.removeMouseClickHandler(listener)
        except Exception:
            pass
        controllers.discard(id(controller))


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
    listener = state.get("mouse_listener")
    if listener is None:
        listener = MouseSelectionListener()
        state["mouse_listener"] = listener
    controllers = state.setdefault("mouse_listener_controllers", set())
    if id(controller) not in controllers:
        try:
            controller.addMouseClickHandler(listener)
            controllers.add(id(controller))
        except Exception:
            pass


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


class MouseSelectionListener(unohelper.Base, XMouseClickHandler):
    """Switches to visual mode when user selects text with the mouse.

    XMouseClickHandler is added to the controller via addMouseClickHandler
    and receives mouse events from the document editing area.
    Returns False to not consume the event (pass through to LibreOffice).
    """

    # NOTE: It isn't reliable way to get start of selection by setting it in
    # this function. This gets called before LO has moved the cursor to position
    # where click happened.
    def mousePressed(self, event):
        state = _state()
        if not state["enabled"]:
            return False
        if _get_mode() == "visual":
            _goto_mode("normal")
            _reset_count()
        return False

    def mouseReleased(self, event):
        state = _state()
        if not state["enabled"]:
            return False
        _reset_count()
        _reset_pending_keys()
        controller = _get_controller()
        if controller is not None:
            try:
                state["view_cursor"] = controller.getViewCursor()
            except Exception:
                pass
        cursor = _get_cursor()
        if cursor is None:
            return False
        try:
            sel_len = len(cursor.getString())
        except Exception:
            sel_len = 0

        if sel_len < 2:
            # No extended selection -> apply Normal mode cursor.
            #
            # Use a short delay so LibreOffice finishes placing its
            # own cursor before we override it (race condition otherwise).
            if _get_mode() != "insert":
                threading.Timer(0.05, _show_cursor, args=["normal"]).start()
            return False

        self._apply_visual_mode_when_mouse_selection(cursor)
        return False

    # Didn't manage to get MouseDragListener to work correctly so this is used.
    # This needs less boilerplate code so better in that regard.
    def _apply_visual_mode_when_mouse_selection(self, cursor):
        """Applies Visual mode when text is selected with mouse in any window."""
        if cursor is None:
            return
        sel_start = cursor.getStart()
        sel_end = cursor.getEnd()
        if _is_forward_selection(cursor):
            anchor = sel_start
            caret = sel_end
        else:
            anchor = sel_end
            caret = sel_start
        # Apply after LO finishes its own mouse-up cursor update.
        threading.Timer(0.02, self._finalize_mouse_selection, args=[anchor, caret]).start()

    def _finalize_mouse_selection(self, anchor, caret) -> None:
        """Finalizes applying visual selection after mouse release (post-LO cursor update)."""
        try:
            cursor = _get_cursor()
            if cursor is None:
                return
            _set_mode("visual")
            _set_visual_anchor(anchor)
            _set_visual_selection(cursor, anchor, caret)
        except Exception:
            pass

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
            _reset_count()
            if state["mode"] == "insert":
                _reset_pending_keys()
                _update_statusline(controller)
                _show_insert_cursor_for_controller(controller)
            else:
                _goto_mode("normal")
                _update_statusline(controller)
                _show_normal_cursor_for_controller(controller)
        elif event_name == "OnViewCreated":
            _attach_controller(controller)

    def disposing(self, event):
        return None


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


g_exportedScripts = (toggle_viper_office, enable_viper_office, disable_viper_office, \
                     _debug_cursor_state)

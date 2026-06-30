import sys
import os
import threading
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi
gi.require_version("IBus", "1.0")
from gi.repository import IBus, GLib

from daemon.llm_client import get_suggestions
from daemon.memory import init_db, record_accepted, record_rejected, get_top_phrases
from daemon.config import load as load_config


class LLMEngine(IBus.Engine):
    __gtype_name__ = "LLMEngine"

    def __init__(self, connection, object_path):
        super().__init__(connection=connection, object_path=object_path)
        # Context from surrounding text (what's actually in the document)
        self._surrounding: str = ""
        self._cursor_pos: int = 0
        # Current word being typed (to know how many chars to delete on commit)
        self._current_word: str = ""
        self._suggestions: list[str] = []
        self._lookup_table = IBus.LookupTable.new(5, 0, True, True)
        self._lookup_table.set_orientation(IBus.Orientation.VERTICAL)
        self._pending = False
        self._timer_id: int | None = None
        self._app_name = ""
        init_db()

    # ------------------------------------------------------------------ #
    # IBus Engine overrides
    # ------------------------------------------------------------------ #

    def do_focus_in(self):
        self.register_properties(IBus.PropList())
        self._app_name = self._get_active_app()
        # Tell the app we want surrounding text updates
        self.get_surrounding_text()
        self._reset()

    def do_focus_out(self):
        self._reset()

    def do_reset(self):
        self._reset()

    def do_candidate_clicked(self, index: int, button: int, state: int) -> None:
        self._commit_suggestion(index)

    def do_set_surrounding_text(self, text, cursor_pos: int, anchor_pos: int) -> None:
        """App tells us what text surrounds the cursor — use it as LLM context."""
        full = text.get_text() if text else ""
        self._surrounding = full[:cursor_pos]
        self._cursor_pos = cursor_pos
        # Track the current in-progress word (chars after last whitespace)
        words = self._surrounding.rsplit(None, 1)
        self._current_word = words[-1] if words else ""
        self._schedule_suggest()

    def do_process_key_event(self, keyval: int, keycode: int, state: int) -> bool:
        if state & IBus.ModifierType.RELEASE_MASK:
            return False

        cfg = load_config()
        if not cfg.get("enabled", True):
            return False

        # Only intercept keys when suggestions are visible
        if self._suggestions:
            if keyval == IBus.KEY_Escape:
                self._dismiss()
                return True
            if keyval == IBus.KEY_Down:
                self._lookup_table.cursor_down()
                self.update_lookup_table(self._lookup_table, True)
                return True
            if keyval == IBus.KEY_Up:
                self._lookup_table.cursor_up()
                self.update_lookup_table(self._lookup_table, True)
                return True
            if keyval == IBus.KEY_Right:
                self._commit_suggestion(self._lookup_table.get_cursor_pos())
                return True

        # Let ALL other keys pass through to the app unchanged
        return False

    # ------------------------------------------------------------------ #
    # Suggestion helpers
    # ------------------------------------------------------------------ #

    def _schedule_suggest(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
        self._timer_id = GLib.timeout_add(400, self._trigger_suggest)

    def _trigger_suggest(self) -> bool:
        self._timer_id = None
        context = self._get_context()
        if len(context.strip()) < 3:
            self._dismiss()
            return False

        memory_hits = get_top_phrases(context, self._app_name, limit=3)
        if memory_hits:
            self._show_suggestions(memory_hits)

        if not self._pending:
            self._pending = True
            threading.Thread(target=self._fetch_llm, args=(context,), daemon=True).start()

        return False

    def _fetch_llm(self, context: str) -> None:
        cfg = load_config()
        suggestions = get_suggestions(
            context,
            n=cfg.get("max_suggestions", 3),
            app_name=self._app_name,
        )
        self._pending = False
        if suggestions:
            GLib.idle_add(self._show_suggestions, suggestions)

    def _get_context(self) -> str:
        cfg = load_config()
        words = self._surrounding.split()
        limit = cfg.get("context_words", 30)
        return " ".join(words[-limit:])

    def _show_suggestions(self, suggestions: list[str]) -> None:
        if not suggestions:
            return
        self._suggestions = suggestions
        self._lookup_table.clear()
        for s in suggestions:
            self._lookup_table.append_candidate(IBus.Text.new_from_string(s))
        self.update_lookup_table(self._lookup_table, True)
        self.show_lookup_table()

    def _commit_suggestion(self, idx: int) -> None:
        if idx >= len(self._suggestions):
            return
        phrase = self._suggestions[idx]
        for i, s in enumerate(self._suggestions):
            if i != idx:
                record_rejected(s, self._get_context(), self._app_name)
        record_accepted(phrase, self._get_context(), self._app_name)

        # Delete the in-progress word then insert suggestion
        word_len = len(self._current_word)
        if word_len > 0:
            self.delete_surrounding_text(-word_len, word_len)
        self.commit_text(IBus.Text.new_from_string(phrase + " "))
        self._dismiss()

    def _dismiss(self) -> None:
        self._suggestions = []
        self._lookup_table.clear()
        self.hide_lookup_table()

    def _reset(self) -> None:
        self._surrounding = ""
        self._current_word = ""
        self._cursor_pos = 0
        self._suggestions = []
        self._pending = False
        self._lookup_table.clear()
        self.hide_lookup_table()
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    @staticmethod
    def _get_active_app() -> str:
        try:
            out = subprocess.check_output(
                ["xdotool", "getactivewindow", "getwindowname"],
                timeout=0.3, stderr=subprocess.DEVNULL,
            )
            return out.decode().strip()
        except Exception:
            return ""

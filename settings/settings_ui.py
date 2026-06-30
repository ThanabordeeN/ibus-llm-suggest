#!/usr/bin/env python3
"""GTK3 Settings UI for LLM IBus Engine."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from daemon.config import load as load_config, save as save_config
from daemon.memory import init_db, get_all_accepted, delete_phrase, clear_all


class SettingsWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="LLM IBus Settings")
        self.set_default_size(600, 500)
        self.set_border_width(12)
        init_db()

        notebook = Gtk.Notebook()
        self.add(notebook)

        notebook.append_page(self._build_api_tab(), Gtk.Label(label="API"))
        notebook.append_page(self._build_memory_tab(), Gtk.Label(label="Memory"))

        self.connect("destroy", Gtk.main_quit)
        self.show_all()

    # ------------------------------------------------------------------ #
    # API tab
    # ------------------------------------------------------------------ #

    def _build_api_tab(self) -> Gtk.Widget:
        cfg = load_config()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_border_width(12)

        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        box.pack_start(grid, False, False, 0)

        # Base URL
        grid.attach(Gtk.Label(label="Base URL:", xalign=1), 0, 0, 1, 1)
        self._url_entry = Gtk.Entry()
        self._url_entry.set_text(cfg.get("base_url", ""))
        self._url_entry.set_hexpand(True)
        grid.attach(self._url_entry, 1, 0, 1, 1)

        # API Key
        grid.attach(Gtk.Label(label="API Key:", xalign=1), 0, 1, 1, 1)
        self._key_entry = Gtk.Entry()
        self._key_entry.set_text(cfg.get("api_key", ""))
        self._key_entry.set_visibility(False)
        self._key_entry.set_hexpand(True)
        grid.attach(self._key_entry, 1, 1, 1, 1)

        # Model
        grid.attach(Gtk.Label(label="Model:", xalign=1), 0, 2, 1, 1)
        self._model_entry = Gtk.Entry()
        self._model_entry.set_text(cfg.get("model", ""))
        grid.attach(self._model_entry, 1, 2, 1, 1)

        # Max suggestions
        grid.attach(Gtk.Label(label="Suggestions:", xalign=1), 0, 3, 1, 1)
        self._n_spin = Gtk.SpinButton.new_with_range(1, 5, 1)
        self._n_spin.set_value(cfg.get("max_suggestions", 3))
        grid.attach(self._n_spin, 1, 3, 1, 1)

        # Timeout
        grid.attach(Gtk.Label(label="Timeout (s):", xalign=1), 0, 4, 1, 1)
        self._timeout_spin = Gtk.SpinButton.new_with_range(1, 30, 1)
        self._timeout_spin.set_value(cfg.get("timeout", 5))
        grid.attach(self._timeout_spin, 1, 4, 1, 1)

        # Enable toggle
        self._enabled_check = Gtk.CheckButton(label="Enable autocomplete")
        self._enabled_check.set_active(cfg.get("enabled", True))
        box.pack_start(self._enabled_check, False, False, 0)

        # Test + Save buttons
        btn_box = Gtk.Box(spacing=8)
        box.pack_end(btn_box, False, False, 0)

        self._status_label = Gtk.Label(label="")
        btn_box.pack_start(self._status_label, True, True, 0)

        test_btn = Gtk.Button(label="Test Connection")
        test_btn.connect("clicked", self._on_test)
        btn_box.pack_end(test_btn, False, False, 0)

        save_btn = Gtk.Button(label="Save")
        save_btn.get_style_context().add_class("suggested-action")
        save_btn.connect("clicked", self._on_save)
        btn_box.pack_end(save_btn, False, False, 0)

        return box

    def _on_save(self, _btn) -> None:
        cfg = load_config()
        cfg["base_url"] = self._url_entry.get_text().strip()
        cfg["api_key"] = self._key_entry.get_text().strip()
        cfg["model"] = self._model_entry.get_text().strip()
        cfg["max_suggestions"] = int(self._n_spin.get_value())
        cfg["timeout"] = float(self._timeout_spin.get_value())
        cfg["enabled"] = self._enabled_check.get_active()
        save_config(cfg)
        self._status_label.set_text("Saved.")
        GLib.timeout_add(2000, lambda: self._status_label.set_text("") or False)

    def _on_test(self, _btn) -> None:
        self._status_label.set_text("Testing…")

        def _do_test():
            from daemon.llm_client import get_suggestions
            self._on_save(None)
            result = get_suggestions("Hello, this is a test", n=1)
            GLib.idle_add(
                self._status_label.set_text,
                f"OK: {result[0]!r}" if result else "No response / error",
            )

        import threading
        threading.Thread(target=_do_test, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Memory tab
    # ------------------------------------------------------------------ #

    def _build_memory_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(12)

        # Phrase list
        sw = Gtk.ScrolledWindow()
        sw.set_vexpand(True)
        box.pack_start(sw, True, True, 0)

        self._store = Gtk.ListStore(str, str, int)  # phrase, app, count
        self._tree = Gtk.TreeView(model=self._store)

        for i, title in enumerate(("Phrase", "App", "Count")):
            col = Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=i)
            col.set_resizable(True)
            self._tree.append_column(col)

        sw.add(self._tree)
        self._reload_memory()

        btn_box = Gtk.Box(spacing=8)
        box.pack_end(btn_box, False, False, 0)

        del_btn = Gtk.Button(label="Delete Selected")
        del_btn.connect("clicked", self._on_delete)
        btn_box.pack_start(del_btn, False, False, 0)

        clear_btn = Gtk.Button(label="Clear All")
        clear_btn.get_style_context().add_class("destructive-action")
        clear_btn.connect("clicked", self._on_clear_all)
        btn_box.pack_start(clear_btn, False, False, 0)

        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", lambda _: self._reload_memory())
        btn_box.pack_end(refresh_btn, False, False, 0)

        return box

    def _reload_memory(self) -> None:
        self._store.clear()
        for row in get_all_accepted(200):
            self._store.append([row["phrase"], row["app_name"] or "", row["count"]])

    def _on_delete(self, _btn) -> None:
        sel = self._tree.get_selection()
        model, it = sel.get_selected()
        if it:
            phrase = model[it][0]
            delete_phrase(phrase)
            self._reload_memory()

    def _on_clear_all(self, _btn) -> None:
        dlg = Gtk.MessageDialog(
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text="Clear all learned phrases?",
        )
        if dlg.run() == Gtk.ResponseType.OK:
            clear_all()
            self._reload_memory()
        dlg.destroy()


def main():
    SettingsWindow()
    Gtk.main()


if __name__ == "__main__":
    main()

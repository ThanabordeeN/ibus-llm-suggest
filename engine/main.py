#!/usr/bin/env python3
"""IBus LLM Engine — entry point."""
import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi
gi.require_version("IBus", "1.0")
from gi.repository import IBus, GLib

from engine.llm_engine import LLMEngine

ENGINE_NAME = "llm-suggest"
EXEC_PATH = os.path.abspath(__file__)


class LLMEngineFactory(IBus.Factory):
    def __init__(self, bus: IBus.Bus) -> None:
        super().__init__(
            connection=bus.get_connection(),
            object_path=IBus.PATH_FACTORY,
        )
        self._engine_id = 0

    def do_create_engine(self, engine_name: str) -> IBus.Engine:
        self._engine_id += 1
        path = (
            "/com/llmibus/engines/"
            + re.sub(r"[^a-zA-Z0-9_/]", "_", engine_name)
            + f"/{self._engine_id}"
        )
        return LLMEngine(connection=self.get_connection(), object_path=path)


def main():
    IBus.init()
    bus = IBus.Bus()

    if not bus.is_connected():
        print(
            "Error: cannot connect to IBus daemon.\n"
            "Run:  ibus-daemon -dr --panel=disable",
            file=sys.stderr,
        )
        sys.exit(1)

    loop = GLib.MainLoop()
    bus.connect("disconnected", lambda b: loop.quit())

    _factory = LLMEngineFactory(bus)

    component = IBus.Component(
        name="org.freedesktop.IBus.LLMSuggest",
        description="LLM Autocomplete Engine",
        version="0.1.0",
        license="MIT",
        author="user",
        homepage="",
        command_line=f"python3 {EXEC_PATH}",
        textdomain="llm-ibus",
    )

    engine_desc = IBus.EngineDesc(
        name=ENGINE_NAME,
        longname="LLM Suggest",
        description="AI-powered autocomplete via OpenAI-compatible API",
        language="other",
        license="MIT",
        author="user",
        icon="",
        layout="us",
        symbol="AI",
    )
    component.add_engine(engine_desc)
    bus.register_component(component)

    print("LLM IBus engine running — select 'LLM Suggest' in IBus preferences.")
    loop.run()


if __name__ == "__main__":
    main()

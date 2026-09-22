from __future__ import annotations

from pathlib import Path

import pytest

from showco.runtime import gui_schema


def test_default_gui_declares_all_navigation_pages() -> None:
    document = gui_schema.load_gui(gui_schema.DEFAULT_GUI_PATH)

    assert document.page_at('/performance').title == 'Performance'
    assert [item.label for item in document.navigation] == [
        'Performance',
        'Set list',
        'Lighting cues',
        'Soundcheck',
        'Recovery',
        'Channels',
        'Musicians',
        'Health',
        'Playback',
        'Attributes',
        'Actions',
        'Errors',
    ]


def test_gui_rejects_unknown_navigation_page(tmp_path: Path) -> None:
    path = tmp_path / 'gui.toml'
    path.write_text(
        """\
version = 1
name = "showCo"
default_page = "home"

[[navigation]]
page = "missing"
label = "Missing"

[[pages]]
id = "home"
path = "/"
title = "Home"
renderer = "home"
"""
    )

    with pytest.raises(ValueError, match="navigation page 'missing' is not declared"):
        gui_schema.load_gui(path)

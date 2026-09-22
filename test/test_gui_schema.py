from __future__ import annotations

from pathlib import Path

import pytest

from showco.runtime import gui_schema


def test_default_gui_declares_all_navigation_pages() -> None:
    document = gui_schema.load_gui(gui_schema.DEFAULT_GUI_PATH)

    assert document.page_at('/performance').title == 'Performance'
    assert document.page('channels').renderer == 'channels'
    assert [page.name for page in document.pages] == [
        'channels',
        'musicians',
        'performance',
        'setlist',
        'lighting',
        'soundcheck',
        'recovery',
        'health',
        'playback',
        'attributes',
        'actions',
        'errors',
    ]


def test_gui_rejects_duplicate_page_name(tmp_path: Path) -> None:
    path = tmp_path / 'gui.toml'
    path.write_text(
        """\
version = 1
name = "showCo"
default_page = "home"

[[pages]]
name = "home"

[[pages]]
name = "home"
"""
    )

    message = 'page names must be unique'
    with pytest.raises(ValueError, match=message):
        gui_schema.load_gui(path)

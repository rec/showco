from __future__ import annotations

from pathlib import Path

import pytest

from showco.runtime import gui_schema, models, views


def test_default_gui_declares_all_navigation_pages() -> None:
    document = gui_schema.load_gui(gui_schema.DEFAULT_GUI_PATH)

    assert document.page_at('/performance').title == 'Performance'
    assert document.page('channels').renderer == ''
    assert [page.name for page in document.pages] == [
        'channels',
        'track-names',
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


def test_channels_file_controls_rendered_content(tmp_path: Path) -> None:
    original = gui_schema.DEFAULT_GUI_PATH.read_text()
    calibrate = """[[pages.sections.elements.children]]
name = "calibrate"
kind = "button"
label = "Calibrate"
action = "recs-calibrate"

"""
    save = """[[pages.sections.elements]]
name = "save_names"
kind = "button"
label = "Save"
operation = "save_track_names"

"""
    assert calibrate in original and save in original
    updated = original.replace(calibrate, '').replace(save, '', 1)
    updated = updated.replace(
        '[[pages]]\nname = "track-names"',
        '''[[pages.sections]]
name = "tools"
title = "Track tools"

[[pages.sections.elements]]
name = "save_names"
kind = "button"
label = "Store track names"
operation = "save_track_names"

[[pages]]
name = "track-names"''',
    )
    path = tmp_path / 'gui.toml'
    path.write_text(updated)
    status = models.ShowStatus(
        recs=models.RecsStatus(
            service=models.ServiceStatus(name='recs', state='connected'),
            channels=[models.ChannelLevel(name='1', state='healthy')],
        ),
        streamo=models.StreamoStatus(
            service=models.ServiceStatus(name='streamo', state='disabled')
        ),
    )
    try:
        gui_schema.configure_gui(path)
        html = views.configured_page(gui_schema.current_gui().page('channels'), status)
    finally:
        gui_schema.configure_gui(gui_schema.DEFAULT_GUI_PATH)

    assert 'Calibrate</button>' not in html
    assert 'Store track names</button>' in html
    assert html.index('Track tools</h2>') < html.index('Store track names</button>')
    assert html.index('Revert</button>') < html.index('Track tools</h2>')


@pytest.mark.parametrize(
    'old,new',
    [
        ('kind = "waveform"', 'kind = "mystery"'),
        ('action = "recs-calibrate"', 'action = "shutdown"'),
        ('value = "item.name"', 'value = "item.password"'),
        ('label = "Stereo"', 'label = "Stereo"\nunknown = true'),
    ],
)
def test_channels_file_rejects_unsupported_declarations(
    tmp_path: Path, old: str, new: str
) -> None:
    path = tmp_path / 'gui.toml'
    path.write_text(gui_schema.DEFAULT_GUI_PATH.read_text().replace(old, new))

    with pytest.raises(ValueError, match='invalid GUI document.*gui.toml'):
        gui_schema.load_gui(path)

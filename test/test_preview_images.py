import importlib.util
from pathlib import Path
from types import ModuleType
from unittest import mock

import pytest
import tyro
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent


@pytest.fixture
def preview_module() -> ModuleType:
    path = Path(__file__).parents[1] / 'scripts/preview-images.py'
    spec = importlib.util.spec_from_file_location('preview_images', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_arguments_and_directory_names_determine_image_order(
    tmp_path: Path, preview_module: ModuleType
) -> None:
    directory = tmp_path / 'photos'
    directory.mkdir()
    for name in ['z.png', 'a.JPG', 'notes.txt']:
        (directory / name).touch()
    (directory / 'nested.png').mkdir()
    first = tmp_path / 'first.png'
    first.touch()
    options = tyro.cli(
        preview_module.Options,
        args=[str(first), str(directory), str(first)],
    )
    assert preview_module.image_paths(options.images) == [
        first,
        directory / 'a.JPG',
        directory / 'z.png',
        first,
    ]


def test_missing_argument_path_reports_the_path(
    tmp_path: Path, preview_module: ModuleType
) -> None:
    missing = tmp_path / 'missing'
    with pytest.raises(FileNotFoundError, match='missing'):
        preview_module.image_paths([missing])


def test_forward_keys_advance_and_selection_keys_select(
    preview_module: ModuleType,
) -> None:
    window = mock.Mock()
    window.current = Path('photo.jpg')
    window.selected = []
    for key, text in [
        (Qt.Key.Key_N, 'n'),
        (Qt.Key.Key_Y, 'Y'),
        (Qt.Key.Key_Escape, '\x1b'),
    ]:
        event = QKeyEvent(
            QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, text
        )
        preview_module.Preview.keyPressEvent(window, event)
    window.move_image.assert_not_called()
    assert window.selected == []
    for key, text in [
        (Qt.Key.Key_Return, '\r'),
        (Qt.Key.Key_Enter, '\r'),
        (Qt.Key.Key_Right, ''),
        (Qt.Key.Key_Down, ''),
        (Qt.Key.Key_Space, ' '),
        (Qt.Key.Key_Y, 'y'),
    ]:
        event = QKeyEvent(
            QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, text
        )
        preview_module.Preview.keyPressEvent(window, event)
    assert window.move_image.call_count == 6
    assert window.selected == [Path('photo.jpg')]
    held = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Y,
        Qt.KeyboardModifier.NoModifier,
        'y',
        True,
    )
    preview_module.Preview.keyPressEvent(window, held)
    assert window.move_image.call_count == 6
    assert window.selected == [Path('photo.jpg')]


def test_selected_paths_are_printed_on_one_line(
    preview_module: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    with (
        mock.patch.object(preview_module.tyro, 'cli'),
        mock.patch.object(preview_module, 'image_paths', return_value=[Path('a.jpg')]),
        mock.patch.object(preview_module, 'QApplication') as application,
        mock.patch.object(preview_module, 'Preview') as preview,
    ):
        preview.return_value.selected = [Path('a.jpg'), Path('photos/b.jpg')]
        application.return_value.exec.return_value = 0
        assert preview_module.main() == 0
    assert capsys.readouterr().out == 'a.jpg photos/b.jpg\n'


def test_arrows_revisit_images_without_losing_or_duplicating_selections(
    preview_module: ModuleType,
) -> None:
    paths = [Path('a.jpg'), Path('broken.jpg'), Path('b.jpg'), Path('c.jpg')]
    window = mock.Mock(paths=paths, index=-1, current=None, selected=[])
    window.move_image.side_effect = lambda step: preview_module.Preview.move_image(
        window, step
    )

    def reader(path: str) -> mock.Mock:
        value = mock.Mock()
        value.read.return_value.isNull.return_value = path == 'broken.jpg'
        return value

    def press(key: Qt.Key, text: str = '') -> None:
        event = QKeyEvent(
            QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, text
        )
        preview_module.Preview.keyPressEvent(window, event)

    with (
        mock.patch.object(preview_module, 'QImageReader', side_effect=reader),
        mock.patch.object(preview_module.QPixmap, 'fromImage'),
    ):
        window.move_image(1)
        assert window.current == paths[0]
        press(Qt.Key.Key_Left)
        assert window.current == paths[0]
        window.close.assert_not_called()
        press(Qt.Key.Key_Right)
        assert window.current == paths[2]
        press(Qt.Key.Key_Y, 'y')
        assert window.current == paths[3]
        press(Qt.Key.Key_Up)
        assert window.current == paths[2]
        press(Qt.Key.Key_Left)
        assert window.current == paths[0]
        press(Qt.Key.Key_Y, 'y')
        assert window.current == paths[2]
        press(Qt.Key.Key_Y, 'y')
        assert window.current == paths[3]
        assert window.selected == [paths[2], paths[0]]
        press(Qt.Key.Key_Down)
        assert window.current is None
        window.close.assert_called_once_with()


def test_quit_preserves_selections_without_advancing(
    preview_module: ModuleType,
) -> None:
    window = mock.Mock(current=Path('current.jpg'), selected=[Path('chosen.jpg')])
    event = QKeyEvent(
        QKeyEvent.Type.KeyPress, Qt.Key.Key_Q, Qt.KeyboardModifier.NoModifier, 'q'
    )
    preview_module.Preview.keyPressEvent(window, event)
    window.close.assert_called_once_with()
    window.move_image.assert_not_called()
    assert window.selected == [Path('chosen.jpg')]

#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pydantic", "PySide6", "tyro"]
# ///
"""Preview images; y/Space selects, q quits. Print selected paths on exit."""

import sys
from pathlib import Path
from typing import Annotated

import tyro
from pydantic import BaseModel
from PySide6.QtCore import Qt
from PySide6.QtGui import QImageReader, QKeyEvent, QPixmap, QResizeEvent
from PySide6.QtWidgets import QApplication, QLabel, QSizePolicy, QVBoxLayout, QWidget


class Options(BaseModel, frozen=True):
    images: Annotated[list[Path], tyro.conf.Positional]
    """Image files or directories, in presentation order."""


class Preview(QWidget):
    def __init__(self, paths: list[Path]) -> None:
        super().__init__()
        self.paths = paths
        self.index = -1
        self.selected: list[Path] = []
        self.current: Path | None = None
        self.original = QPixmap()
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.caption = QLabel()
        self.caption.setTextFormat(Qt.TextFormat.PlainText)
        self.caption.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.image, 1)
        layout.addWidget(self.caption)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.resize(1000, 800)
        self.move_image(1)

    def move_image(self, step: int) -> None:
        index = self.index + step
        while 0 <= index < len(self.paths):
            path = self.paths[index]
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            image = reader.read()
            if image.isNull():
                print(f'Cannot read {path}: {reader.errorString()}', file=sys.stderr)
                index += step
                continue
            self.index = index
            self.current = path
            self.original = QPixmap.fromImage(image)
            self.setWindowTitle(str(path))
            self.caption.setText(
                f'{path}\ny/Space: select    Enter/Right/Down: skip    '
                'Left/Up: back    q: quit'
            )
            self.scale_image()
            return
        if step > 0:
            self.current = None
            self.close()

    def scale_image(self) -> None:
        if not self.original.isNull():
            self.image.setPixmap(
                self.original.scaled(
                    self.image.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.scale_image()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.isAutoRepeat() or self.current is None:
            return
        if event.text() == 'q':
            self.close()
            return
        if event.key() in {Qt.Key.Key_Left, Qt.Key.Key_Up}:
            self.move_image(-1)
            return
        if event.text() == 'y' or event.key() == Qt.Key.Key_Space:
            if self.current not in self.selected:
                self.selected.append(self.current)
        elif event.key() not in {
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Right,
            Qt.Key.Key_Down,
        }:
            return
        self.move_image(1)


def image_paths(paths: list[Path]) -> list[Path]:
    extensions = {
        '.' + bytes(f.data()).decode().lower()
        for f in QImageReader.supportedImageFormats()
    }
    images: list[Path] = []
    for path in paths:
        if path.is_dir():
            images.extend(
                p
                for p in sorted(path.iterdir())
                if p.is_file() and p.suffix.lower() in extensions
            )
        elif path.is_file():
            images.append(path)
        else:
            raise FileNotFoundError(f'Not a file or directory: {path}')
    return images


def main() -> int:
    options = tyro.cli(Options)
    try:
        paths = image_paths(options.images)
    except OSError as error:
        sys.exit(str(error))
    if not paths:
        sys.exit('No images found.')
    application = QApplication([])
    preview = Preview(paths)
    result = 0
    if preview.current is not None:
        preview.show()
        result = application.exec()
    print(' '.join(str(p) for p in preview.selected))
    return result


if __name__ == '__main__':
    sys.exit(main())

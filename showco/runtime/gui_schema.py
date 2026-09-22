from __future__ import annotations

import tomllib
from functools import cache
from pathlib import Path

from pydantic import BaseModel, model_validator

DEFAULT_GUI_PATH = Path(__file__).parent.parent / 'gui.toml'
_gui_path = DEFAULT_GUI_PATH


class Page(BaseModel, frozen=True):
    name: str
    title: str = ''
    renderer: str = ''

    @model_validator(mode='after')
    def _defaults(self) -> Page:
        return self.model_copy(
            update={
                'title': self.title or self.name.capitalize(),
                'renderer': self.renderer or self.name,
            }
        )


class Gui(BaseModel, frozen=True):
    version: int
    name: str
    default_page: str
    pages: list[Page]

    @model_validator(mode='after')
    def _validate_references(self) -> Gui:
        names = {page.name for page in self.pages}
        if self.default_page not in names:
            raise ValueError(f'default_page {self.default_page!r} is not declared')
        if len(names) != len(self.pages):
            raise ValueError('page names must be unique')
        return self

    def page(self, name: str) -> Page:
        return next(
            page
            for page in self.pages
            if page.name == name or page.title.casefold() == name.casefold()
        )

    def page_at(self, path: str) -> Page | None:
        return next((page for page in self.pages if f'/{page.name}' == path), None)


@cache
def load_gui(path: Path) -> Gui:
    try:
        value = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f'invalid GUI document {path}: {error}') from error
    return Gui.model_validate(value)


def configure_gui(path: Path) -> Gui:
    global _gui_path
    resolved = path.resolve()
    document = load_gui(resolved)
    _gui_path = resolved
    return document


def current_gui() -> Gui:
    return load_gui(_gui_path)

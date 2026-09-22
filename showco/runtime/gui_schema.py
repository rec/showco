from __future__ import annotations

import tomllib
from functools import cache
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

DEFAULT_GUI_PATH = Path(__file__).parent.parent / 'gui.toml'
_gui_path = DEFAULT_GUI_PATH


class NavigationItem(BaseModel, frozen=True):
    page: str
    label: str


class Component(BaseModel, frozen=True):
    id: str
    kind: str
    label: str = ''
    binding: str = ''
    action: str = ''
    condition: str = ''
    children: list[Component] = Field(default_factory=list)


class Page(BaseModel, frozen=True):
    id: str
    path: str
    title: str
    renderer: str
    sources: list[str] = Field(default_factory=list)
    components: list[Component] = Field(default_factory=list)


class Gui(BaseModel, frozen=True):
    version: int
    name: str
    default_page: str
    navigation: list[NavigationItem]
    pages: list[Page]

    @model_validator(mode='after')
    def _validate_references(self) -> Gui:
        pages = {page.id for page in self.pages}
        if self.default_page not in pages:
            raise ValueError(f'default_page {self.default_page!r} is not declared')
        if len(pages) != len(self.pages):
            raise ValueError('page IDs must be unique')
        paths = {page.path for page in self.pages}
        if len(paths) != len(self.pages):
            raise ValueError('page paths must be unique')
        for item in self.navigation:
            if item.page not in pages:
                raise ValueError(f'navigation page {item.page!r} is not declared')
        for page in self.pages:
            _validate_components(page.components, page.id, set())
        return self

    def page(self, page_id: str) -> Page:
        return next(
            page
            for page in self.pages
            if page.id == page_id or page.title.casefold() == page_id.casefold()
        )

    def page_at(self, path: str) -> Page | None:
        return next((page for page in self.pages if page.path == path), None)


def _validate_components(components: list[Component], page: str, ids: set[str]) -> None:
    for component in components:
        if component.id in ids:
            raise ValueError(f'page {page!r} repeats component ID {component.id!r}')
        ids.add(component.id)
        _validate_components(component.children, page, ids)


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

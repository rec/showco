from __future__ import annotations

import tomllib
from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

DEFAULT_GUI_PATH = Path(__file__).parent.parent / 'gui.toml'
_gui_path = DEFAULT_GUI_PATH


class Element(BaseModel, frozen=True):
    name: str = Field(min_length=1)
    kind: str
    label: str = ''
    source: str = ''
    value: str = ''
    empty_text: str = ''
    enabled_when: str = ''
    action: str = ''
    operation: str = ''
    layout: str = 'cards'
    limit: int = Field(default=0, ge=0)
    format: str = ''
    children: list[Element] = Field(default_factory=list)

    @model_validator(mode='after')
    def _valid_element(self) -> Element:
        allowed = {
            'repeat': {},
            'indicator': {'value': 'item.on'},
            'text': {},
            'text_field': {'value': 'item.name', 'action': 'recs-track-name'},
            'checkbox': {
                'value': 'item.channels',
                'action': 'recs-set-stereo',
                'enabled_when': 'stereo_pair_available',
            },
            'waveform': {'source': 'waveforms'},
            'button': {},
        }
        if self.kind not in allowed:
            raise ValueError(f'{self.name}: unknown element kind {self.kind!r}')
        if (
            self.kind in {'button', 'checkbox', 'text_field', 'waveform'}
            and not self.label
        ):
            raise ValueError(f'{self.name}: label is required')
        for field in ('source', 'value', 'enabled_when'):
            if self.kind == 'repeat' and field == 'source':
                if self.source not in {'show.recs.channels', 'show.recs.errors'}:
                    raise ValueError(
                        f'{self.name}: unknown repeat source {self.source!r}'
                    )
                continue
            if self.kind == 'text' and field == 'value':
                if self.value not in {'item.timestamp', 'item.message'}:
                    raise ValueError(f'{self.name}: unknown text value {self.value!r}')
                continue
            expected = allowed[self.kind].get(field, '')
            if getattr(self, field) != expected:
                raise ValueError(f'{self.name}: invalid {field} for {self.kind}')
        if self.kind == 'repeat':
            if not self.children or not self.empty_text:
                raise ValueError(f'{self.name}: repeat needs children and empty_text')
            if (self.source == 'show.recs.channels' and self.layout != 'cards') or (
                self.source == 'show.recs.errors' and self.layout != 'list'
            ):
                raise ValueError(f'{self.name}: unsupported source layout')
        elif self.children or self.empty_text:
            raise ValueError(f'{self.name}: only repeat accepts children or empty_text')
        if self.kind != 'repeat' and (self.layout != 'cards' or self.limit):
            raise ValueError(f'{self.name}: layout and limit need a repeat')
        if self.format and (
            self.kind != 'text'
            or self.value != 'item.timestamp'
            or self.format != 'time'
        ):
            raise ValueError(f'{self.name}: unsupported format')
        if self.kind == 'button':
            if (self.action, self.operation) not in {
                ('recs-calibrate', ''),
                ('', 'save_track_names'),
                ('', 'revert_track_names'),
            }:
                raise ValueError(f'{self.name}: unsupported button action')
        elif self.action != allowed[self.kind].get('action', '') or self.operation:
            raise ValueError(f'{self.name}: unsupported action or operation')
        return self

    model_config = ConfigDict(extra='forbid')


class Section(BaseModel, frozen=True):
    name: str = Field(min_length=1)
    title: str = ''
    elements: list[Element]

    model_config = ConfigDict(extra='forbid')


class Page(BaseModel, frozen=True):
    name: str = Field(pattern=r'^[a-z][a-z0-9-]*$')
    title: str = ''
    renderer: str = ''
    sections: list[Section] = Field(default_factory=list)

    @model_validator(mode='after')
    def _defaults(self) -> Page:
        if self.sections and self.renderer:
            raise ValueError(f'{self.name}: configured pages cannot select a renderer')
        return self.model_copy(
            update={
                'title': self.title or self.name.capitalize(),
                'renderer': self.renderer or ('' if self.sections else self.name),
            }
        )

    model_config = ConfigDict(extra='forbid')


class Gui(BaseModel, frozen=True):
    version: Literal[1]
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
        for page in self.pages:
            if not page.sections and page.renderer not in {
                'musicians',
                'performance',
                'workflow',
                'health',
                'playback',
                'attributes',
                'actions',
            }:
                raise ValueError(f'{page.name}: page needs sections')
            element_names: set[str] = set()
            operations: set[str] = set()
            has_track_name = False
            for section in page.sections:
                if not section.elements:
                    raise ValueError(f'{page.name}: section {section.name!r} is empty')
                if section.name in element_names:
                    raise ValueError(f'{page.name}: duplicate name {section.name!r}')
                element_names.add(section.name)
                for element in section.elements:
                    if element.kind == 'repeat':
                        if any(child.kind == 'repeat' for child in element.children):
                            raise ValueError(
                                f'{page.name}: nested repeat is unsupported'
                            )
                        if element.source == 'show.recs.errors' and any(
                            child.kind != 'text' for child in element.children
                        ):
                            raise ValueError(
                                f'{page.name}: error rows only support text'
                            )
                        if element.source == 'show.recs.channels' and any(
                            child.kind == 'text' for child in element.children
                        ):
                            raise ValueError(
                                f'{page.name}: channel rows do not support text'
                            )
                        if any(
                            child.kind == 'button' and child.action != 'recs-calibrate'
                            for child in element.children
                        ):
                            raise ValueError(
                                f'{page.name}: repeated button must calibrate'
                            )
                        has_track_name |= any(
                            child.kind == 'text_field' for child in element.children
                        )
                    elif element.kind in {
                        'indicator',
                        'text_field',
                        'checkbox',
                        'waveform',
                        'text',
                    }:
                        raise ValueError(f'{page.name}: {element.kind} needs a repeat')
                    else:
                        if element.action:
                            raise ValueError(
                                f'{page.name}: top-level button needs an operation'
                            )
                        if element.operation in operations:
                            raise ValueError(
                                f'{page.name}: button operation must be unique'
                            )
                        operations.add(element.operation)
                    _validate_element_names(element, element_names, page.name)
            if not has_track_name and operations.intersection(
                {'save_track_names', 'revert_track_names'}
            ):
                raise ValueError(f'{page.name}: name controls need a track name field')
        return self

    def page(self, name: str) -> Page:
        return next(
            page
            for page in self.pages
            if page.name == name or page.title.casefold() == name.casefold()
        )

    def page_at(self, path: str) -> Page | None:
        return next((page for page in self.pages if f'/{page.name}' == path), None)

    model_config = ConfigDict(extra='forbid')


def _validate_element_names(element: Element, names: set[str], page: str) -> None:
    if element.name in names:
        raise ValueError(f'{page}: duplicate name {element.name!r}')
    names.add(element.name)
    for child in element.children:
        _validate_element_names(child, names, page)


@cache
def load_gui(path: Path) -> Gui:
    try:
        value = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f'invalid GUI document {path}: {error}') from error
    try:
        return Gui.model_validate(value)
    except ValidationError as error:
        raise ValueError(f'invalid GUI document {path}: {error}') from error


def configure_gui(path: Path) -> Gui:
    global _gui_path
    resolved = path.resolve()
    document = load_gui(resolved)
    _gui_path = resolved
    return document


def current_gui() -> Gui:
    return load_gui(_gui_path)

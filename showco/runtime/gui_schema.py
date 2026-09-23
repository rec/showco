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
    separator: str = ''
    children: list[Element] = Field(default_factory=list)

    @model_validator(mode='after')
    def _valid_element(self) -> Element:
        allowed = {
            'repeat': {},
            'indicator': {'value': 'item.on'},
            'text': {},
            'status': {},
            'meter': {},
            'service_card': {},
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
            self.kind
            in {'button', 'checkbox', 'text_field', 'waveform', 'meter', 'service_card'}
            and not self.label
        ):
            raise ValueError(f'{self.name}: label is required')
        for field in ('source', 'value', 'enabled_when'):
            if self.kind == 'repeat' and field == 'source':
                if self.source not in REPEAT_SOURCES:
                    raise ValueError(
                        f'{self.name}: unknown repeat source {self.source!r}'
                    )
                continue
            if self.kind == 'text' and field == 'value':
                if self.value not in ITEM_VALUES:
                    raise ValueError(f'{self.name}: unknown text value {self.value!r}')
                continue
            if self.kind == 'status' and field == 'value':
                if self.value not in STATUS_FORMATS:
                    raise ValueError(
                        f'{self.name}: unknown status value {self.value!r}'
                    )
                continue
            if self.kind == 'meter' and field == 'value':
                if self.value not in {'cpu', 'memory', 'disk'}:
                    raise ValueError(f'{self.name}: unknown meter {self.value!r}')
                continue
            if self.kind == 'service_card' and field == 'value':
                if self.value not in SERVICE_FORMATS:
                    raise ValueError(f'{self.name}: unknown service {self.value!r}')
                continue
            expected = allowed[self.kind].get(field, '')
            if getattr(self, field) != expected:
                raise ValueError(f'{self.name}: invalid {field} for {self.kind}')
        if self.kind == 'repeat':
            if not self.children or not self.empty_text:
                raise ValueError(f'{self.name}: repeat needs children and empty_text')
            if self.layout != (
                'cards' if self.source == 'show.recs.channels' else 'list'
            ):
                raise ValueError(f'{self.name}: unsupported source layout')
        elif self.children or self.empty_text:
            raise ValueError(f'{self.name}: only repeat accepts children or empty_text')
        if self.kind != 'repeat' and (
            self.layout != 'cards' or self.limit or self.separator
        ):
            raise ValueError(f'{self.name}: layout, limit and separator need a repeat')
        if self.kind == 'text':
            if self.format not in TEXT_FORMATS:
                raise ValueError(f'{self.name}: unsupported text format')
            if bool(self.value) == bool(self.label):
                raise ValueError(f'{self.name}: text needs either value or label')
        elif self.kind == 'status':
            if self.format != STATUS_FORMATS[self.value]:
                raise ValueError(f'{self.name}: unsupported status format')
        elif self.kind == 'service_card':
            if self.format != SERVICE_FORMATS[self.value]:
                raise ValueError(f'{self.name}: unsupported service format')
        elif self.format:
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
    style: str = ''
    elements: list[Element]

    @model_validator(mode='after')
    def _valid_style(self) -> Section:
        if self.style not in {'', 'readiness', 'cards', 'performance'}:
            raise ValueError(f'{self.name}: unknown section style {self.style!r}')
        return self

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
                        if element.layout == 'list' and any(
                            child.kind != 'text'
                            or child.value not in REPEAT_VALUES[element.source]
                            or child.value not in TEXT_VALUES_BY_FORMAT[child.format]
                            or (
                                child.format == 'mixer_detail'
                                and element.source != 'show.mixers'
                            )
                            or (
                                child.format == 'osc_detail'
                                and element.source != 'show.recs.osc'
                            )
                            for child in element.children
                        ):
                            raise ValueError(f'{page.name}: unsupported list row field')
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
                    }:
                        raise ValueError(f'{page.name}: {element.kind} needs a repeat')
                    elif element.kind in {'text', 'status', 'meter', 'service_card'}:
                        pass
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


REPEAT_VALUES = {
    'show.recs.errors': {'item.timestamp', 'item.message'},
    'show.readiness.checks': {'item.name', 'item.message'},
    'show.input_checks': {'item.name', 'item.message'},
    'show.incidents': {'item.timestamp', 'item.message'},
    'show.mixers': {'item.name', 'item'},
    'show.recs.osc': {'item.name', 'item'},
}
REPEAT_SOURCES = {'show.recs.channels', *REPEAT_VALUES}
ITEM_VALUES = {'', 'item.name', 'item.message', 'item.timestamp', 'item'}
TEXT_FORMATS = {'', 'bold', 'time', 'mixer_detail', 'osc_detail'}
TEXT_VALUES_BY_FORMAT = {
    '': {'item.name', 'item.message', 'item.timestamp'},
    'bold': {'item.name', 'item.message'},
    'time': {'item.timestamp'},
    'mixer_detail': {'item'},
    'osc_detail': {'item'},
}
STATUS_FORMATS = {
    'show.readiness.ready': 'readiness',
    'show.recs.service': 'service',
    'show.recs.snapshot_error': 'snapshot',
    'show.recording_progress.message': 'progress',
    'show.streamo.service': 'service',
    'show.lyte': 'lyte',
    'show.system': 'temperature',
    'show.streamo': 'bitrate',
}
SERVICE_FORMATS = {
    'show.recs.service': 'recording',
    'show.streamo.service': 'streaming',
}

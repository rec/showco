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
    disabled_when: str = ''
    action: str = ''
    operation: str = ''
    parameters: dict[str, str] = Field(default_factory=dict)
    layout: str = 'cards'
    limit: int = Field(default=0, ge=0)
    format: str = ''
    separator: str = ''
    field: str = ''
    required: bool = False
    readonly: bool = False
    visible_when: str = ''
    options: dict[str, str] = Field(default_factory=dict)
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
            'mutable_attributes': {'source': 'recs.mutable_attributes'},
            'action_button': {},
            'action_result': {},
            'text_field': {'value': 'item.name', 'action': 'recs-track-name'},
            'checkbox': {
                'value': 'item.channels',
                'action': 'recs-set-stereo',
                'enabled_when': 'stereo_pair_available',
            },
            'waveform': {'source': 'waveforms'},
            'button': {},
            'form': {},
            'input': {},
            'textarea': {},
            'select': {},
            'submit': {},
            'action_history': {'source': 'show.actions'},
            'workflow_editor': {},
            'workflow_button': {},
            'workflow_status': {},
            'workflow_display': {},
            'workflow_checkbox': {},
            'details': {},
            'link': {},
        }
        if self.kind not in allowed:
            raise ValueError(f'{self.name}: unknown element kind {self.kind!r}')
        if (
            self.kind
            in {
                'button',
                'checkbox',
                'text_field',
                'waveform',
                'meter',
                'service_card',
                'action_button',
            }
            and not self.label
        ):
            raise ValueError(f'{self.name}: label is required')
        for field in ('source', 'value', 'enabled_when', 'disabled_when'):
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
            if self.kind in {'input', 'textarea'} and field == 'value':
                continue
            if self.kind == 'workflow_button' and field == 'action':
                continue
            if self.kind == 'link' and field == 'value':
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
            if self.kind == 'action_button' and field == 'disabled_when':
                if self.disabled_when not in {'', 'playback_waiting'}:
                    raise ValueError(f'{self.name}: unsupported disabled condition')
                continue
            expected = allowed[self.kind].get(field, '')
            if getattr(self, field) != expected:
                raise ValueError(f'{self.name}: invalid {field} for {self.kind}')
        if self.kind == 'repeat':
            if not self.children or not self.empty_text:
                raise ValueError(f'{self.name}: repeat needs children and empty_text')
            layouts = {
                'show.recs.channels': 'cards',
                'recs.musicians': 'forms',
            }
            if self.layout != layouts.get(self.source, 'list'):
                raise ValueError(f'{self.name}: unsupported source layout')
        elif self.kind == 'form':
            if self.action not in FORM_ACTIONS or not self.children:
                raise ValueError(f'{self.name}: form needs an action and children')
        elif self.kind in {'input', 'textarea', 'select'}:
            if self.field not in FORM_FIELDS or (
                self.field in MUSICIAN_FIELDS
                and self.value
                not in {'', *(f'item.{field}' for field in MUSICIAN_FIELDS)}
            ):
                raise ValueError(f'{self.name}: unsupported form field')
            if self.kind == 'select' and not self.options:
                raise ValueError(f'{self.name}: select needs options')
        elif self.kind == 'submit':
            if not self.label:
                raise ValueError(f'{self.name}: submit needs a label')
        elif self.kind == 'action_history':
            if not self.empty_text:
                raise ValueError(f'{self.name}: action history needs empty_text')
        elif self.kind == 'workflow_button':
            if self.action not in WORKFLOW_ACTIONS:
                raise ValueError(f'{self.name}: unsupported workflow action')
            if self.parameters not in WORKFLOW_BUTTON_PARAMETERS.get(self.action, [{}]):
                raise ValueError(f'{self.name}: unsupported workflow parameters')
        elif self.kind == 'workflow_checkbox':
            if self.name not in {
                'confirm-cue-resolution',
                'confirm-lighting',
                'confirm-observation',
                'confirm-output',
            }:
                raise ValueError(f'{self.name}: unsupported workflow checkbox')
        elif self.kind == 'workflow_editor':
            if self.name not in {
                'setlist-editor',
                'add-song',
                'reload-setlist',
                'lighting-editor',
                'lighting-looks',
                'add-lighting',
                'reload-lighting',
                'expected-inputs',
                'check-note',
                'skip-step',
                'soundcheck-results',
            }:
                raise ValueError(f'{self.name}: unsupported workflow editor')
        elif self.kind == 'workflow_display':
            if self.name not in {
                'cue-position',
                'cue-notes',
                'cue-elapsed',
                'cue-message',
                'cue-resolution',
                'cue-resolution-help',
                'lighting-position',
                'lighting-live',
                'lighting-message',
                'lighting-resolution',
                'lighting-pending',
                'lighting-resolution-help',
                'lighting-help',
                'soundcheck-scope',
            }:
                raise ValueError(f'{self.name}: unsupported workflow display')
        elif self.kind == 'workflow_status':
            if self.name not in {'workflow-result', 'lighting-result'}:
                raise ValueError(f'{self.name}: unsupported workflow status')
        elif self.kind == 'details':
            if not self.label or not self.children:
                raise ValueError(f'{self.name}: details needs a label and children')
        elif self.kind == 'link':
            if self.value != '/performance' or not self.label:
                raise ValueError(f'{self.name}: unsupported link')
        elif (
            self.children
            or (
                self.parameters
                and self.kind not in {'action_button', 'workflow_button'}
            )
            or (self.empty_text and self.kind != 'mutable_attributes')
        ):
            raise ValueError(
                f'{self.name}: only repeat and mutable attributes accept empty_text'
            )
        if self.kind not in {'repeat', 'details', 'input', 'textarea', 'select'} and (
            self.layout != 'cards' or self.limit or self.separator
        ):
            raise ValueError(f'{self.name}: layout, limit and separator need a repeat')
        if self.kind == 'text':
            if self.format not in TEXT_FORMATS:
                raise ValueError(f'{self.name}: unsupported text format')
            if bool(self.value) == bool(self.label):
                raise ValueError(f'{self.name}: text needs either value or label')
        elif self.kind == 'status':
            if self.format not in STATUS_FORMATS[self.value]:
                raise ValueError(f'{self.name}: unsupported status format')
        elif self.kind == 'service_card':
            if self.format != SERVICE_FORMATS[self.value]:
                raise ValueError(f'{self.name}: unsupported service format')
        elif self.kind == 'mutable_attributes':
            if not self.empty_text:
                raise ValueError(f'{self.name}: mutable attributes need empty_text')
        elif self.kind == 'action_button':
            if self.action not in ACTION_BUTTON_PARAMETERS:
                raise ValueError(f'{self.name}: unsupported button action')
            if self.parameters not in ACTION_BUTTON_PARAMETERS[self.action]:
                raise ValueError(f'{self.name}: unsupported button parameters')
        elif self.kind in {'input', 'textarea', 'select'}:
            if self.format not in {'', 'lines'}:
                raise ValueError(f'{self.name}: unsupported form format')
        elif self.format:
            raise ValueError(f'{self.name}: unsupported format')
        if self.kind == 'button':
            if (self.action, self.operation) not in {
                ('recs-calibrate', ''),
                ('', 'save_track_names'),
                ('', 'revert_track_names'),
            }:
                raise ValueError(f'{self.name}: unsupported button action')
        elif self.kind not in {'action_button', 'form', 'workflow_button'} and (
            self.action != allowed[self.kind].get('action', '') or self.operation
        ):
            raise ValueError(f'{self.name}: unsupported action or operation')
        if self.kind not in {'input', 'textarea', 'select', 'workflow_editor'} and (
            self.field or self.required or self.readonly or self.options
        ):
            raise ValueError(f'{self.name}: form options need an input')
        if self.visible_when and self.visible_when not in FEATURE_GATES:
            raise ValueError(f'{self.name}: unsupported visibility condition')
        return self

    model_config = ConfigDict(extra='forbid')


class Section(BaseModel, frozen=True):
    name: str = Field(min_length=1)
    title: str = ''
    style: str = ''
    elements: list[Element]

    @model_validator(mode='after')
    def _valid_style(self) -> Section:
        if self.style not in {'', 'readiness', 'cards', 'performance', 'transport'}:
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

    def uses_source(self, source: str) -> bool:
        return any(
            element.source == source
            for section in self.sections
            for element in section.elements
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
                'performance',
                'workflow',
                'playback',
                'attributes',
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
                        if element.layout == 'forms' and any(
                            child.kind != 'form' for child in element.children
                        ):
                            raise ValueError(f'{page.name}: form rows need forms')
                    elif element.kind == 'form':
                        if any(
                            child.kind not in {'input', 'textarea', 'select', 'submit'}
                            for child in element.children
                        ):
                            raise ValueError(f'{page.name}: unsupported form field')
                    elif element.kind == 'details':
                        if any(
                            child.kind
                            not in {
                                'workflow_button',
                                'workflow_checkbox',
                                'workflow_display',
                                'workflow_editor',
                            }
                            for child in element.children
                        ):
                            raise ValueError(f'{page.name}: unsupported details field')
                    elif element.kind in {
                        'indicator',
                        'text_field',
                        'checkbox',
                        'waveform',
                    }:
                        raise ValueError(f'{page.name}: {element.kind} needs a repeat')
                    elif element.kind in {
                        'text',
                        'status',
                        'meter',
                        'service_card',
                        'mutable_attributes',
                        'action_button',
                        'action_result',
                        'action_history',
                        'workflow_editor',
                        'workflow_button',
                        'workflow_status',
                        'workflow_display',
                        'workflow_checkbox',
                        'link',
                    }:
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
REPEAT_SOURCES = {'show.recs.channels', 'recs.musicians', *REPEAT_VALUES}
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
    'show.readiness.ready': {'readiness'},
    'show.recs.service': {'service'},
    'show.recs.snapshot_error': {'snapshot'},
    'show.recording_progress.message': {'progress'},
    'show.streamo.service': {'service'},
    'show.lyte': {'lyte'},
    'show.system': {'temperature'},
    'show.streamo': {'bitrate'},
    'show.recs.playback': {
        'playback_state',
        'playback_selection',
        'playback_position',
    },
    'show.music': {'music'},
}
SERVICE_FORMATS = {
    'show.recs.service': 'recording',
    'show.streamo.service': 'streaming',
}
ACTION_BUTTON_PARAMETERS = {
    'recs-playback-jump-session': [{'offset': '-1'}, {'offset': '1'}],
    'recs-playback-jump': [{'seconds': '-10'}, {'seconds': '10'}],
    'recs-playback-play': [{}],
    'recs-playback-pause': [{}],
    'recs-playback-stop': [{}],
    'recs-calibrate': [{}],
    'recs-reload-profiles': [{}],
    'recs-new-session': [{}],
    'recs-pause-recording': [{}],
    'recs-resume-recording': [{}],
    'recs-status-snapshot': [{}],
    'recs-disk-status': [{}],
    'recs-list-devices': [{}],
    'recs-capabilities': [{}],
    'lyte-test': [{}],
    'music-setup': [{}],
    'music-record': [{}],
    'music-teardown': [{}],
    'music-stop': [{}],
    'streamo-restart': [{}],
    'streamo-mute': [{}],
    'streamo-unmute': [{}],
    'streamo-stop': [{}],
    'streamo-clip': [{}],
    'recs-marker': [
        {'label': 'Show start'},
        {'label': 'Song start'},
        {'label': 'Interval'},
        {'label': 'Show end'},
    ],
}
MUSICIAN_ACTIONS = {'recs-musician-add', 'recs-musician-edit'}
MUSICIAN_FIELDS = {'nickname', 'names', 'links'}
FORM_ACTIONS = MUSICIAN_ACTIONS | {
    'recs-set-noise-floor',
    'recs-marker',
    'recs-key-label',
    'recs-shutdown',
    'cable-test',
    'streamo-title',
    'streamo-chat',
    'streamo-announce',
    'streamo-marker',
}
FORM_FIELDS = MUSICIAN_FIELDS | {
    'source',
    'channel',
    'noise_floor',
    'label',
    'key',
    'confirmation',
    'channels',
    'sends',
    'duration-seconds',
    'title',
    'category',
    'tags',
    'message',
    'description',
}
FEATURE_GATES = {'streamo_enabled', 'lyte_enabled', 'music_enabled'}
WORKFLOW_ACTIONS = {
    'setlist-save',
    'setlist-next',
    'setlist-skip',
    'setlist-repeat',
    'setlist-interval',
    'setlist-accept',
    'setlist-retry',
    'lighting-back',
    'lighting-go',
    'lighting-accept',
    'lighting-cancel',
    'lighting-retry',
    'lighting-save',
    'soundcheck-begin',
    'soundcheck-check',
    'soundcheck-start-recording',
    'soundcheck-pause-recording',
    'soundcheck-lights',
    'soundcheck-skip',
}
WORKFLOW_BUTTON_PARAMETERS = {
    'soundcheck-check': [
        {'step': 'disk'},
        {'step': 'inputs'},
        {'step': 'recording'},
        {'step': 'playback'},
        {'step': 'lights'},
        {'step': 'stream'},
    ],
}

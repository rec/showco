from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from subprocess import CompletedProcess
from typing import ClassVar

from pydantic import BaseModel
from reccy import reccy
from reccy.services import controller, paths, spec
from reccy.services.models import DaemonMetadata, ServiceSpec, StatusResult
from streamo.config import STREAMO_SERVICE

from ..deployment import machine_role
from . import gui_schema, models

PROJECT_ROOT = Path(__file__).parent.parent.parent
SHOWCO_SERVICE = spec.load(PROJECT_ROOT / 'showco/service.toml')
RECS_SERVICE = spec.load(PROJECT_ROOT.parent / 'recs/recs/daemon/service.toml')
LYTE_SERVICE = spec.load(PROJECT_ROOT.parent / 'lyte/lyte/service.toml')
SERVICES = {
    'lyte': LYTE_SERVICE,
    'recs': RECS_SERVICE,
    'showco': SHOWCO_SERVICE,
    'streamo': STREAMO_SERVICE,
}


class ShowcoDaemon(reccy.Reccy):
    name: ClassVar[str] = 'showco'
    service_spec: ClassVar[ServiceSpec] = SHOWCO_SERVICE
    daemon_module: ClassVar[str] = 'showco'


class RecsDaemonStatus(BaseModel, frozen=True):
    gui_ipc_error: str | None = None


STATUS_MODELS = {'recs': RecsDaemonStatus}
STATUS_ERROR_ATTRIBUTES = {'recs': 'gui_ipc_error'}
STATUS_ERROR_LABELS = {'recs': 'GUI IPC error'}


def install_showco_service(
    root: Path,
    host: str = '0.0.0.0',
    port: int = 17_352,
    mixers_config: Path | None = None,
    gui: Path = gui_schema.DEFAULT_GUI_PATH,
    streamo_enabled: bool = False,
    lyte_enabled: bool = False,
) -> int:
    gui_schema.load_gui(gui)
    daemon = ShowcoDaemon(platform=paths.current_platform())
    daemon.install_service(
        [
            'run',
            *showco_args(
                host,
                port,
                mixers_config,
                streamo_enabled,
                lyte_enabled,
                gui,
            ),
        ]
    )
    result = daemon.service_status()
    controller.print_service_status('showco', result)
    return 0 if result.installed else 1


def showco_args(
    host: str,
    port: int,
    mixers_config: Path | None,
    streamo_enabled: bool,
    lyte_enabled: bool,
    gui: Path = gui_schema.DEFAULT_GUI_PATH,
) -> list[str]:
    result = ['--host', host, '--port', str(port)]
    if mixers_config is not None:
        result.extend(['--mixers-config', str(mixers_config)])
    result.extend(['--gui', str(gui)])
    if streamo_enabled:
        result.append('--streamo-enabled')
    if lyte_enabled:
        result.append('--lyte-enabled')
    return result


def restart_service(name: str) -> models.ActionResult:
    if name not in {'recs', 'lyte', 'streamo'}:
        raise ValueError('Unsupported restart service')
    controller = service_registry().controller(name)
    controller.restart()
    result = controller.status()
    if result.running:
        return models.ActionResult(ok=True, message=f'{name} restart requested')
    return models.ActionResult(ok=False, message=f'{name} service did not start')


def refresh_service_definition(
    name: str,
    runner: Callable[..., CompletedProcess[str]] | None = None,
) -> StatusResult:
    controller = service_registry(runner=runner).controller(name)
    metadata = DaemonMetadata.model_validate_json(controller.paths.metadata.read_text())
    controller.install(metadata)
    return controller.status()


def service_registry(
    runner: Callable[..., CompletedProcess[str]] | None = None,
) -> controller.ServiceRegistry:
    return controller.ServiceRegistry(
        SERVICES,
        runner=runner,
        status_models=STATUS_MODELS,
        status_error_attributes=STATUS_ERROR_ATTRIBUTES,
        status_error_labels=STATUS_ERROR_LABELS,
    )


def install_main(argv: list[str] | None = None) -> int:
    machine_role.require_target_machine('showco run install-service')
    parser = argparse.ArgumentParser(
        prog='showco run install-service',
        description='Install or refresh the showCo user service',
    )
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', default=17_352, type=int)
    parser.add_argument('--mixers-config', type=Path)
    parser.add_argument('--gui', type=Path, default=gui_schema.DEFAULT_GUI_PATH)
    parser.add_argument('--streamo-enabled', action='store_true')
    parser.add_argument('--lyte-enabled', action='store_true')
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args(argv)
    return install_showco_service(
        host=args.host,
        port=args.port,
        mixers_config=args.mixers_config,
        gui=args.gui,
        streamo_enabled=args.streamo_enabled,
        lyte_enabled=args.lyte_enabled,
        root=args.root,
    )


def status_main(argv: list[str] | None = None) -> int:
    machine_role.require_target_machine('showco run service-status')
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments or arguments[:1] in (['-h'], ['--help']):
        print('Usage: showco run service-status {lyte,recs,showco,streamo} ...')
        return 0
    return service_registry().report_status(arguments)

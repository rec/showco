from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from logging import StreamHandler, getLogger
from pathlib import Path
from typing import Annotated, TextIO

import tyro
from pydantic import BaseModel, Field
from reccy import cli
from reccy.runtime import logging

from .deployment import bundle, card, deploy, logs, machine_role, panel, python
from .provision import network
from .runtime import gui_schema, rehearsal, services
from .runtime.mixer import MixersMonitor, load_mixer_specs
from .runtime.server import make_server
from .streamo import auth, client, config
from .x18 import cable_test

ERROR_LOG_PATH = Path(__file__).resolve().parents[1] / 'showco-errors.txt'


class WebUiOptions(BaseModel, frozen=True):
    host: str = '127.0.0.1'
    port: int = 17_352
    mixers_config: Path = Path()
    streamo_enabled: bool = False
    lyte_enabled: bool = False
    gui: Path = gui_schema.DEFAULT_GUI_PATH
    audio: Annotated[list[Path], tyro.conf.UseAppendAction] = Field(
        default_factory=list
    )
    setup: Annotated[list[Path], tyro.conf.UseAppendAction] = Field(
        default_factory=list
    )
    teardown: Annotated[list[Path], tyro.conf.UseAppendAction] = Field(
        default_factory=list
    )
    rehearsal_mode: Annotated[
        bool,
        tyro.conf.arg(
            name='rehearsal',
            help='run with simulated recs and streamo services',
        ),
    ] = False


def run_web_ui(options: WebUiOptions) -> int:
    gui_schema.configure_gui(options.gui)
    if not options.rehearsal_mode:
        machine_role.require_target_machine('showco run')
    if not options.audio and not (options.setup and options.teardown):
        warning = 'Incidental audio is not configured for both Setup and Tear down'
        getLogger(__name__).warning(warning)
        try:
            descriptor = os.open(
                ERROR_LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
            with os.fdopen(descriptor, 'a') as output:
                output.write(f'WARNING: {warning}\n')
        except OSError as error:
            getLogger(__name__).warning(
                'Could not save audio configuration warning: %s', error
            )
    if options.rehearsal_mode:
        server = make_server(
            options.host,
            options.port,
            recs=rehearsal.RehearsalRecsClient(),
            streamo=rehearsal.RehearsalStreamoClient(),
            system=rehearsal.RehearsalSystemMonitor(),
            mixers=rehearsal.RehearsalMixersMonitor(),
            streamo_restart=rehearsal.restart_streamo,
            lyte=rehearsal.RehearsalLyteClient(),
            streamo_enabled=True,
            setup=options.setup or options.audio,
            teardown=options.teardown or options.audio,
        )
        print(f'showco rehearsal listening on http://{options.host}:{options.port}')
    else:
        mixer_specs = load_mixer_specs(options.mixers_config)
        server = make_server(
            options.host,
            options.port,
            mixers=MixersMonitor(mixer_specs),
            mixer_specs=mixer_specs,
            streamo_enabled=options.streamo_enabled,
            lyte_enabled=options.lyte_enabled,
            performance_enabled=True,
            setup=options.setup or options.audio,
            teardown=options.teardown or options.audio,
        )
        print(f'showco listening on http://{options.host}:{options.port}')
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ERROR_LOG_PATH.unlink(missing_ok=True)
    logging.configure()
    arguments = sys.argv[1:] if argv is None else argv
    with tempfile.TemporaryFile(mode='w+t') as record:
        with (
            redirect_stdout(_Tee(sys.stdout, record)),
            redirect_stderr(_Tee(sys.stderr, record)),
        ):
            handler = StreamHandler(record)
            getLogger().addHandler(handler)
            status = 0
            try:
                if not arguments or arguments[0].startswith('-'):
                    status = deploy.main(arguments)
                else:
                    status = cli.route_command(
                        {
                            'run': run_command,
                            'bundle': bundle.main,
                            'cable-test': cable_test.main,
                            'prepare-card': card.main,
                            'deploy': deploy.main,
                            'logs': logs.main,
                            'panel': panel.main,
                            'python': python.main,
                            'streamo': streamo_command,
                        },
                        arguments,
                        prog='showco',
                    )
            except KeyboardInterrupt:
                print('Interrupted', file=sys.stderr)
                status = 130
            finally:
                getLogger().removeHandler(handler)
                handler.close()
                error = sys.exception()
                if status != 0 or error is not None:
                    _append_error_report(record, status, error)
            return status


def run_command(arguments: list[str]) -> int:
    if arguments[:1] == ['network-config']:
        return network.main(arguments[1:])
    if arguments[:1] == ['install-service']:
        return services.install_main(arguments[1:])
    if arguments[:1] == ['service-status']:
        return services.status_main(arguments[1:])
    if arguments[:1] == ['streamo-health']:
        return client.health_main(arguments[1:])
    if arguments[:1] == ['streamo-config']:
        return config.main(arguments[1:])
    options = tyro.cli(
        WebUiOptions,
        args=arguments,
        description='Run the showCo web UI',
    )
    return run_web_ui(options)


def streamo_command(arguments: list[str]) -> int:
    machine_role.require_target_machine('showco streamo')
    return auth.main(arguments)


class _Tee:
    def __init__(self, original: TextIO, record: TextIO) -> None:
        self.original = original
        self.record = record

    @property
    def encoding(self) -> str:
        return self.original.encoding

    def writable(self) -> bool:
        return True

    def write(self, value: str) -> int:
        written = self.original.write(value)
        self.record.write(value)
        return written

    def flush(self) -> None:
        self.original.flush()
        self.record.flush()

    def isatty(self) -> bool:
        return self.original.isatty()


def _append_error_report(
    record: TextIO, status: int | None, error: BaseException | None
) -> None:
    if isinstance(error, SystemExit) and error.code in (None, 0):
        return
    try:
        descriptor = os.open(
            ERROR_LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
        )
        with os.fdopen(descriptor, 'a') as output:
            output.write(f'\n--- showCo failure {datetime.now(UTC).isoformat()} ---\n')
            record.flush()
            record.seek(0)
            shutil.copyfileobj(record, output)
            if error is not None:
                output.writelines(traceback.format_exception(error))
            elif status is not None:
                output.write(f'showCo exited with status {status}\n')
    except OSError as failure:
        print(
            f'Could not save showCo diagnostics to {ERROR_LOG_PATH}: {failure}',
            file=sys.stderr,
        )

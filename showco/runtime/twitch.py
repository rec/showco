import os
import subprocess
import time
from pathlib import Path

from pydantic import BaseModel, Field
from reccy.configuration import settings
from reccy.protocol import rpc
from reccy.services.models import DaemonMetadata, Platform
from reccy.services.paths import service_paths
from recs.base.audio_stream import AudioClient
from recs.daemon.gui_protocol import AudioStatusResult
from streamo.config import STREAMO_SERVICE
from streamo.daemon import load_config
from streamo.preflight import check_encoders, check_media
from streamo.recs_input import RecsConnection

from . import models, recs_snapshot, soundcheck, workflows
from .recs import audio_capture_clocks

MAX_RECOVERIES = 3
RETRY_DELAY_SECONDS = 5
CLOCK_SYNC_MAX_AGE_SECONDS = 3600
CLOCK_SYNC_MARKER = Path('/run/systemd/timesync/synchronized')
RUNTIME_STATE_NAME = 'showco-twitch.json'


class BroadcastRun(BaseModel, frozen=True):
    attempts: int = Field(default=0, ge=0, le=MAX_RECOVERIES)
    retry_after: float = Field(ge=0, allow_inf_nan=False)
    failure: str = ''
    launch_failed: bool = False

    def needs_recovery(self, publisher: models.StreamoStatus) -> bool:
        return publisher.toggle_starts_stream and (
            publisher.stream_state == 'failed' or self.launch_failed
        )


class TwitchRecovery:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.run: BroadcastRun | None = None
        self.error = ''
        if path is not None:
            try:
                self.run = BroadcastRun.model_validate(settings.read(path))
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as error:
                self.error = f'Twitch recovery state unavailable: {error}'

    def save(self, run: BroadcastRun) -> None:
        if self.path is not None:
            try:
                settings.write_json_model(self.path, run)
            except OSError as error:
                self.error = f'Cannot save Twitch recovery state: {error}'
                raise
        self.run = run
        self.error = ''

    def cancel(self) -> None:
        self.run = None
        self.error = ''
        if self.path is not None:
            try:
                self.path.unlink(missing_ok=True)
            except OSError as error:
                self.error = f'Could not clear Twitch recovery authorization: {error}'

    def message(self, off: bool, failure: str) -> str:
        run = self.run
        if self.error or run is None:
            return self.error
        failure = failure or run.failure
        if off and run.attempts == MAX_RECOVERIES:
            return (
                f'Automatic recovery exhausted. Press Retry Twitch. {failure}'.rstrip()
            )
        if run.failure or off:
            return (
                f'Twitch recovery attempts used: {run.attempts}/{MAX_RECOVERIES}. '
                f'{failure}'
            ).rstrip()
        return ''


class TwitchReadiness:
    def __init__(self, config: Path) -> None:
        self.config = config

    def check(
        self,
        show: models.ShowStatus,
        sample: soundcheck.Soundcheck,
        revision: str | None,
    ) -> list[str]:
        recs = show.recs
        if not recs.snapshot_available or recs.service.state != 'connected':
            return ['Fresh recs status is unavailable']
        blockers = []
        if recs.paused:
            blockers.append('Resume recs recording')
        disk = recs.disk
        if (
            disk is None
            or disk.on_system_disk is not False
            or disk.free_bytes <= 0
            or disk.alert_active
            or disk.paused_for_disk_space
        ):
            blockers.append('Mount and check the intended recording disk')
        if (routing := show.music.x18_usb_routing) is not None and not routing.ok:
            blockers.append(routing.message)
        if not clock_is_synchronized():
            blockers.append('Waiting for recent Pi clock synchronization')
        try:
            scope = sample.scope(show, workflows.soundcheck_configuration(revision))
        except OSError:
            scope = ''
        evidence = sample.state
        passes = {
            step
            for step, result in evidence.results.items()
            if result.state == 'passed'
        }
        if (
            not scope
            or scope != evidence.scope
            or not recs.instance_token
            or not recs.session_directory
            or evidence.recording_session != recs.session_directory
            or not evidence.recording_clocks
            or not {'disk', 'recording', 'playback'} <= passes
        ):
            blockers.append(
                'Verify the recording disk and a saved sample in Soundcheck'
            )
        elif recs_snapshot.errors_since(
            recs.errors, evidence.results['recording'].checked_at.timestamp()
        ):
            blockers.append(
                'recs reported an error after the sample. '
                'Review Health and repeat Soundcheck'
            )
        if blockers:
            return blockers
        try:
            metadata = DaemonMetadata.model_validate(
                settings.read(service_paths(STREAMO_SERVICE, Platform.linux).metadata)
            )
        except (OSError, ValueError):
            return ['Install the managed streamO service before starting Twitch']
        if metadata.argv != [
            'daemon',
            'run',
            '--config',
            str(self.config.resolve()),
            '--managed',
        ]:
            return ['Update the managed streamO service before starting Twitch']
        try:
            config = load_config(self.config)
        except (OSError, ValueError) as error:
            return [f'Cannot load streamO configuration ({type(error).__name__})']
        if config.recs_input is None or not config.recs_input.daemon:
            return ['Configure streamO to use audio from the recs daemon']
        connection = RecsConnection(config.recs_input)
        try:
            checks = [*check_media(config), check_encoders(config)]
            failures = [c.detail for c in checks if c.status == 'fail']
            if failures:
                return failures
            inspection = connection.inspect()
            if inspection.instance.start_token != recs.instance_token:
                return ['The recs daemon changed; repeat Soundcheck']
            if not connection.private_endpoint():
                return ['The recs control endpoint is writable by other users']
            if (
                inspection.state != 'idle'
                or not inspection.enabled
                or inspection.paused
                or inspection.muted
            ):
                return ['The selected recs stereo pair is unavailable for forwarding']
            assert connection.client is not None
            audio = AudioStatusResult.model_validate(
                connection.client.call('audio_status')
            )
            recording_sources = {
                s.get('source')
                for s in audio.sources
                if isinstance(s.get('source'), str)
                and s.get('recording_enabled') is True
            }
            expected_sources = evidence.recording_clocks.keys()
            clocks = audio_capture_clocks(audio)
            if any(clocks.get(k) != v for k, v in evidence.recording_clocks.items()):
                return ['A recs capture changed; verify a new sample in Soundcheck']
            if (
                not expected_sources
                or not expected_sources <= recording_sources
                or config.recs_input.source not in recording_sources
                or recs.offline_sources
            ):
                return ['recs is unable to write every required audio source']
            offer = connection.subscribe(inspection).subscription
            try:
                with AudioClient(offer, timeout=0.5) as client:
                    header, _ = client.receive()
                if header.input_status:
                    return ['The selected recs input reported a capture fault']
            finally:
                connection.cancel(offer.subscription_id)
        except (OSError, EOFError, ValueError, rpc.RemoteError) as error:
            return [
                'Cannot verify fresh samples from the selected recs pair '
                f'({type(error).__name__})'
            ]
        return []


def clock_is_synchronized() -> bool:
    try:
        age = time.time() - CLOCK_SYNC_MARKER.stat().st_mtime
        if not 0 <= age <= CLOCK_SYNC_MAX_AGE_SECONDS:
            return False
        result = subprocess.run(
            ['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'],
            capture_output=True,
            text=True,
            timeout=1,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.stdout.strip() == 'yes'


def runtime_state_path() -> Path:
    return Path('/run/user') / str(os.getuid()) / RUNTIME_STATE_NAME


def same_recording(current: models.RecsStatus, checked: models.RecsStatus) -> bool:
    return (
        current.service.state == 'connected'
        and current.snapshot_available
        and not current.paused
        and current.instance_token == checked.instance_token
        and current.session_directory == checked.session_directory
        and current.disk is not None
        and checked.disk is not None
        and current.disk.path == checked.disk.path
        and current.disk.on_system_disk is False
        and current.disk.free_bytes > 0
        and not current.disk.alert_active
        and not current.disk.paused_for_disk_space
        and current.offline_sources == checked.offline_sources
        and current.errors == checked.errors
    )

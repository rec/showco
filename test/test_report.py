import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import numpy as np
import pytest
import soundfile
from pydantic import ValidationError
from pytest_regressions.data_regression import DataRegressionFixture
from recs.recording import session_record
from recs.recording.files import sealed_asset
from ufor import recording
from ufor.codec import score_toml
from ufor.streams import AudioType
from ufor.time import Rate, Timebase

from showco.deployment import report, report_scan

START = datetime(2026, 10, 16, 18, tzinfo=UTC)


def session(
    name: str, start: float, end: float, captured_seconds: float | None = None
) -> report_scan.Session:
    return report_scan.Session(
        path=name,
        started_at=START + timedelta(hours=start),
        ended_at=START + timedelta(hours=end),
        audio_files=1,
        total_bytes=100,
        captured_seconds=(end - start) * 3600
        if captured_seconds is None
        else captured_seconds,
        tracks={
            'X18: 1': (end - start) * 3600
            if captured_seconds is None
            else captured_seconds
        },
    )


def test_latest_show_uses_end_to_start_gap_and_overlapping_sessions() -> None:
    sessions = [
        session('old', 0, 1),
        session('long', 4, 10),
        session('overlap', 5, 6),
        session('last', 12, 13),
    ]
    assert [s.path for s in report_scan.select_sessions(sessions[::-1], 3)] == [
        'long',
        'overlap',
        'last',
    ]
    assert [s.path for s in report_scan.select_sessions(sessions, 2)] == ['last']
    assert [s.path for s in report_scan.select_sessions(sessions, 4)] == [
        'old',
        'long',
        'overlap',
        'last',
    ]


@pytest.mark.parametrize('gap', [0, -1, float('inf'), float('nan')])
def test_gap_must_be_positive_and_finite(gap: float) -> None:
    with pytest.raises(ValidationError):
        report.ReportOptions(gap_hours=gap)


def test_logs_group_losses_without_joining_unidentified_encoder_attempts(
    tmp_path: Path,
) -> None:
    directory = tmp_path / 'streamo'
    directory.mkdir()
    older = directory / 'streamo.log.1'
    older.write_text(
        '2026-10-16T17:00:00Z ERROR old failure\n'
        '2026-10-16T18:00:00Z INFO FFmpeg: out_time_us=0\n'
        '2026-10-16T18:00:10Z INFO FFmpeg: out_time_us=10000000\n'
        '2026-10-16T18:00:11Z ERROR Dropped 100 frames in processing\n'
    )
    newer = directory / 'streamo.log'
    newer.write_text(
        'bad timestamp ERROR ignored\n'
        '2026-10-16T18:00:12Z ERROR Dropped 200 frames in processing\n'
        '2026-10-16T18:01:00Z INFO FFmpeg: out_time_us=100000000\n'
        '2026-10-16T18:01:20Z INFO FFmpeg: out_time_us=120000000\n'
        '2026-10-16T20:00:00Z ERROR future failure\n'
    )
    seconds, issues = report_scan.log_evidence(
        [newer, older], START, START + timedelta(hours=1)
    )
    assert seconds is None
    assert issues == {'streamo: Dropped frames in processing': 2}
    assert report_scan.log_evidence([], START, START)[0] is None


def test_error_messages_redact_credentials_and_terminal_controls() -> None:
    result = report_scan.safe_message(
        'Send rtmp://example/live/secret stream_key=secret '
        'password=hidden\nnext\x1b[31m'
    )
    assert 'secret' not in result
    assert 'hidden' not in result
    assert '\n' not in result
    assert '\x1b' not in result


@pytest.fixture
def recording_directory(tmp_path: Path) -> Path:
    return make_recording(tmp_path)


def make_recording(tmp_path: Path, seconds: int = 1) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    frames = seconds * 48_000
    soundfile.write(tmp_path / 'take.wav', np.zeros((frames, 2)), 48_000)
    document = recording.RecordingScore(
        name='report-test',
        title='Report test',
        assets=[sealed_asset(tmp_path / 'take.wav', tmp_path, 'audio', 'wav')],
        timebases=[Timebase(name='clock', rate=Rate(numerator=48_000))],
        body=recording.Recording(
            state='sealed',
            started_at=START.isoformat(),
            ended_at=(START + timedelta(seconds=seconds + 1)).isoformat(),
            observed_duration_seconds=seconds + 1,
            streams=[
                recording.AudioStream(
                    name='stereo',
                    source_id='X18',
                    track_name='17-18',
                    stream=AudioType(timebase='clock', channels=['left', 'right']),
                    end=frames + 48_000,
                    fragments=[
                        recording.AudioFragment(asset='audio', start=0, count=frames)
                    ],
                    gaps=[
                        recording.Gap(
                            start=frames,
                            end=frames + 48_000,
                            reason=recording.GapReason.input_overflow,
                        )
                    ],
                )
            ],
        ),
    )
    (tmp_path / 'recording.toml').write_text(score_toml(document))
    return tmp_path


def test_recording_metrics_and_issues_are_read_only(
    recording_directory: Path, data_regression: DataRegressionFixture
) -> None:
    before = {p.name: p.read_bytes() for p in recording_directory.iterdir()}
    limitations: list[str] = []
    sessions = report_scan.recording_sessions(
        [recording_directory, recording_directory], limitations
    )
    assert len(sessions) == 1
    saved = sessions[0]
    assert saved.total_bytes == (recording_directory / 'take.wav').stat().st_size
    assert {p.name: p.read_bytes() for p in recording_directory.iterdir()} == before
    result = saved.model_dump(mode='json', exclude={'path', 'total_bytes'})
    data_regression.check(result)
    assert limitations == []


def test_missing_media_is_reported_and_excluded_from_size(
    recording_directory: Path,
) -> None:
    (recording_directory / 'take.wav').rename(recording_directory / 'missing.wav')
    saved = report_scan.recording_sessions([recording_directory], [])[0]
    assert saved.total_bytes == 0
    assert saved.audio_files == 0
    assert 'Media missing or unreadable: audio' in saved.issues


def test_unfinished_journal_is_not_silently_skipped(tmp_path: Path) -> None:
    writer = session_record.SessionRecordWriter(
        tmp_path / 'session-record.jsonl', START.isoformat()
    )
    writer.write(
        session_record.EventRecord(
            type='source_offline',
            timestamp=(START + timedelta(hours=1)).isoformat(),
            source='X18',
        )
    )
    writer.close()
    saved = report_scan.recording_sessions([tmp_path], [])[0]
    assert saved.open
    assert not saved.metadata_available
    assert saved.ended_at == START + timedelta(hours=1)
    assert 'Recording document unavailable; audio totals unknown' in saved.issues


def test_report_separates_elapsed_time_from_overlapping_tracks(
    data_regression: DataRegressionFixture,
) -> None:
    saved = session('recording', 0, 1).model_copy(
        update={
            'tracks': {'X18: 1': 3600.0, 'X18: 17-18': 1800.0},
            'audio_files': 2,
            'total_bytes': 1_000_000_000,
        }
    )
    result = report.render(
        report_scan.Report(sessions=[saved], issues={'input_overflow': 2})
    )
    data_regression.check({'report': result})


def test_collected_report_filters_saved_histories_to_the_show(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    import subprocess

    recording_directory = make_recording(tmp_path / 'recordings', seconds=61)
    monkeypatch.setattr(
        report_scan, 'recording_roots', lambda home, limits: [recording_directory]
    )
    monkeypatch.setattr(
        report_scan.subprocess,
        'run',
        lambda *args, **kwargs: subprocess.CompletedProcess([], 1, '', 'unavailable'),
    )
    monitoring = tmp_path / '.local/state/showco/monitoring'
    monitoring.mkdir(parents=True)
    (monitoring / '2026-10-16.jsonl').write_text(
        '\n'.join(
            json.dumps(r)
            for r in [
                {
                    'started_at': (START - timedelta(hours=1)).isoformat(),
                    'ended_at': (START - timedelta(minutes=1)).isoformat(),
                    'cpu_peak_percent': 99,
                    'disk_pause_occurred': True,
                },
                {
                    'started_at': START.isoformat(),
                    'ended_at': (START + timedelta(seconds=1)).isoformat(),
                    'cpu_peak_percent': 42,
                    'disk_minimum_free_bytes': 1234,
                },
            ]
        )
    )
    (monitoring.parent / 'incidents.json').write_text(
        json.dumps(
            {
                'incidents': [
                    {
                        'timestamp': (START - timedelta(hours=1)).isoformat(),
                        'message': 'old error',
                    },
                    {'timestamp': START.isoformat(), 'message': 'X18 offline'},
                ]
            }
        )
    )
    saved = report_scan.collect(3, tmp_path)
    assert saved.cpu_peak_percent == 42
    assert saved.minimum_free_bytes == 1234
    assert 'showCo: X18 offline' in saved.issues
    assert 'showCo: old error' not in saved.issues
    assert 'Recording paused because disk space was low' not in saved.issues
    assert 'User service journal unavailable.' in saved.limitations


def test_open_recording_uses_saved_evidence_not_report_time(
    recording_directory: Path,
) -> None:
    from recs.recording.read import read_recording

    journal_path = recording_directory / 'session-record.jsonl'
    writer = session_record.SessionRecordWriter(journal_path, START.isoformat())
    writer.write(
        session_record.EventRecord(
            type='source_offline',
            timestamp=(START + timedelta(hours=1)).isoformat(),
        )
    )
    writer.close()
    document_path = recording_directory / 'recording.toml'
    document = read_recording(document_path)
    document = document.model_copy(
        update={
            'assets': [
                *document.assets,
                sealed_asset(
                    journal_path, recording_directory, 'journal', 'recs-session-v5'
                ),
            ],
            'body': document.body.model_copy(
                update={
                    'state': 'open',
                    'ended_at': None,
                    'observed_duration_seconds': None,
                    'journal': 'journal',
                }
            ),
        }
    )
    document_path.write_text(score_toml(document))
    saved = report_scan.recording_sessions([recording_directory], [])[0]
    assert saved.ended_at == START + timedelta(hours=1)
    journal_path.rename(recording_directory / 'missing-journal.jsonl')
    saved = report_scan.recording_sessions([recording_directory], [])[0]
    assert saved.audio_files == 1
    assert 'Open session end unknown: journal unreadable' in saved.issues


def test_show_selection_skips_empty_and_trivial_groups_but_keeps_interruptions() -> (
    None
):
    first = session('first', 0, 0.1, 30)
    interruption = session('interruption', 0.5, 0.6, 1)
    resumed = session('resumed', 1, 1.1, 30)
    exactly_one_minute = session('one-minute', 24, 25, 60)
    idle = session('idle', 48, 49, 0)
    midi_only = session('midi-only', 72, 73, 3600).model_copy(
        update={'audio_files': 0, 'midi_messages': 500}
    )
    no_media = session('no-media', 96, 97, 3600).model_copy(update={'total_bytes': 0})
    selected = report_scan.select_sessions(
        [first, interruption, resumed, exactly_one_minute, idle, midi_only, no_media], 3
    )
    assert [s.path for s in selected] == ['first', 'interruption', 'resumed']


def test_parallel_tracks_do_not_inflate_show_duration() -> None:
    parallel = session('parallel', 24, 25, 40).model_copy(
        update={'tracks': {'X18: 1': 40, 'X18: 2': 40}}
    )
    selected = report_scan.select_sessions([session('older', 0, 1), parallel], 3)
    assert [s.path for s in selected] == ['older']


@pytest.mark.parametrize('older', range(10))
def test_history_offsets_count_only_qualifying_shows(older: int) -> None:
    sessions = [
        session(str(n), n * 24, n * 24 + 1, 61 if n % 2 == 0 else 60) for n in range(20)
    ]
    selected = report_scan.select_sessions(sessions[::-1], 3, older)
    assert [s.path for s in selected] == [str(18 - older * 2)]
    assert report_scan.select_sessions(sessions, 3, 10) == []


@pytest.mark.parametrize(
    'arguments,older',
    [
        ([], 0),
        *[([f'-{n}'], n) for n in range(1, 10)],
        (['--gap-hours', '2.5', '-1'], 1),
        (['-9', '--gap-hours', '2.5'], 9),
    ],
)
def test_report_cli_passes_history_choice_to_collector(
    tmp_path: Path, arguments: list[str], older: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = mock.Mock()
    cfg.paths.root = tmp_path
    cfg.ssh_target = 'test-target'
    monkeypatch.setattr(
        report.machine_role, 'require_provisioning_machine', mock.Mock()
    )
    monkeypatch.setattr(report.update, 'provisioning_config', lambda: cfg)
    ssh_command = mock.Mock(return_value=['fake-ssh'])
    monkeypatch.setattr(report.ssh, 'ssh_command', ssh_command)
    monkeypatch.setattr(
        report.subprocess,
        'run',
        mock.Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, report_scan.Report(sessions=[]).model_dump_json(), ''
            )
        ),
    )
    assert report.main(arguments) == 1
    command = ssh_command.call_args.args[2]
    assert command.endswith(f' {older}')
    assert '.venv/bin/python - ' in command


@pytest.mark.parametrize(
    'arguments', [['-1', '-2'], ['-1', '-1'], ['-0'], ['-10'], ['--unknown']]
)
def test_report_rejects_invalid_or_combined_history_flags_before_connecting(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    require_role = mock.Mock()
    monkeypatch.setattr(
        report.machine_role, 'require_provisioning_machine', require_role
    )
    with pytest.raises(SystemExit):
        report.main(arguments)
    require_role.assert_not_called()


def test_same_named_parallel_tracks_do_not_inflate_captured_duration(
    recording_directory: Path,
) -> None:
    from recs.recording.read import read_recording

    path = recording_directory / 'recording.toml'
    document = read_recording(path)
    streams = document.body.streams
    duplicate = streams[0].model_copy(update={'name': 'duplicate'})
    document = document.model_copy(
        update={
            'body': document.body.model_copy(update={'streams': [*streams, duplicate]})
        }
    )
    path.write_text(score_toml(document))
    saved = report_scan.recording_sessions([recording_directory], [])[0]
    assert saved.tracks['X18: 17-18'] == 2
    assert saved.captured_seconds == 1


def test_unavailable_history_does_not_fall_back_to_latest_show(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report_scan, 'recording_roots', lambda home, limits: [])
    monkeypatch.setattr(
        report_scan,
        'recording_sessions',
        lambda roots, limits: [session('latest', 0, 1)],
    )
    run = mock.Mock()
    monkeypatch.setattr(report_scan.subprocess, 'run', run)
    saved = report_scan.collect(3, tmp_path, older=1)
    assert saved.sessions == []
    assert 'No qualifying show at history offset 1.' in saved.limitations
    run.assert_not_called()

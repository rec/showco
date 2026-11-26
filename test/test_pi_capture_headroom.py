from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture(scope='module')
def monitor_module() -> ModuleType:
    path = Path(__file__).parents[1] / 'scripts/pi-capture-headroom.py'
    spec = spec_from_file_location('pi_capture_headroom', path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ('speed', 'last_crossing', 'action'),
    [
        (0.65, 2.0, 'throttle'),
        (1.0, 2.0, None),
        (0.65, 3.0, None),
    ],
)
def test_throttle_requires_repeated_pressure_and_fresh_encoder_lag(
    monitor_module: ModuleType, speed: float, last_crossing: float, action: str | None
) -> None:
    guard = monitor_module.CaptureGuard()
    for now in (0.0, 0.5, 1.0, 1.5):
        guard.record_progress(now, round(now * speed * 1_000_000))
    assert guard.sample(1.6, 0.51) is None
    assert guard.sample(1.7, 0.30) is None
    assert guard.sample(1.8, 0.55) is None
    assert guard.sample(1.9, 0.30) is None
    assert guard.sample(last_crossing, 0.50) == action


def test_throttled_encoder_can_keep_running_when_capture_recovers(
    monitor_module: ModuleType,
) -> None:
    guard = monitor_module.CaptureGuard()
    guard.mark_throttled()
    assert guard.sample(1.0, 0.55) is None
    assert guard.sample(1.01, 0.35) is None
    assert guard.sample(10.0, 0.35) is None


@pytest.mark.parametrize(
    'samples',
    [
        [(1.0, 0.75)],
        [(1.0, 0.55), (1.1, 0.55)],
        [(1.0, 0.55), (1.01, 0.30), (1.2, 0.55), (1.21, 0.30), (1.3, 0.55)],
    ],
)
def test_stop_when_capture_pressure_persists_after_throttling(
    monitor_module: ModuleType, samples: list[tuple[float, float]]
) -> None:
    guard = monitor_module.CaptureGuard()
    guard.mark_throttled()
    for now, fill in samples[:-1]:
        assert guard.sample(now, fill) is None
    assert guard.sample(*samples[-1]) == 'stop'

from pathlib import Path
from typing import TYPE_CHECKING

from showco.provision.config import Config
from showco.runtime.music import MusicConfig

if TYPE_CHECKING:
    from reccy.pytest_plugin import SampleConfigRegression


def test_sample_config(sample_config_regression: 'SampleConfigRegression') -> None:
    sample_config_regression(Config)


def test_sample_music_config(
    sample_config_regression: 'SampleConfigRegression',
) -> None:
    sample_config_regression(
        MusicConfig, path=Path(__file__).parents[1] / 'doc' / 'sample-music-config.toml'
    )

"""Run the configured streamO encoder against a private RTMP sink on the Pi."""

import logging
from pathlib import Path
from subprocess import Popen

import tyro
from pydantic import BaseModel, SecretStr
from streamo.daemon import load_config
from streamo.provider_config import CustomService, RtmpIngest


class Options(BaseModel, frozen=True):
    config: Path = Path('~/.config/streamo/config.toml')


def run(config_path: Path) -> int:
    config = load_config(config_path)
    address = 'rtmp://127.0.0.1:19350/app'
    loopback = CustomService(
        service='custom',
        encoding=config.streaming_service.encoding,
        ingest=RtmpIngest(
            protocol='rtmp', server_url=address, stream_key=SecretStr('capture-test')
        ),
    )
    config = config.model_copy(
        update={'streaming_service': loopback, 'recover_publish': False}
    )
    with Popen(
        [
            'ffmpeg',
            '-nostdin',
            '-hide_banner',
            '-loglevel',
            'error',
            '-timeout',
            '15',
            '-i',
            f'{address}/capture-test',
            '-map',
            '0',
            '-c',
            'copy',
            '-f',
            'null',
            '-',
        ]
    ) as receiver:
        try:
            return config.run()
        finally:
            if receiver.poll() is None:
                receiver.terminate()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(run(tyro.cli(Options).config))

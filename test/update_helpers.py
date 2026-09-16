from pathlib import Path


def make_config(lyte_enabled: bool = False) -> object:
    class Network:
        user = 'tom'
        host = 'bertrand.local'
        ssh_port = 22
        web_port = 17_352

    class Config:
        network = Network()
        mixers = []

        class Stream:
            enabled = False

        stream = Stream()

        class Lyte:
            enabled = lyte_enabled
            installation_config = Path('patches/showco-installation.toml')

        lyte = Lyte()

        class Paths:
            root = Path('/home/tom/code')

        paths = Paths()

    return Config()

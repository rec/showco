from __future__ import annotations

from showco.provision import config


def make_config(values: dict[str, object], *, port: int | None = None) -> config.Config:
    return config.config_from_values(values, port=port)


def values(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        'network': {
            'host': 'recs-stage.local',
            'user': 'tom',
            'web_port': 17352,
        },
        'paths': {'root': '/srv/show-projects'},
        'networks': networks(),
        'mixers': [
            {
                'name': 'X18',
                'audio_device_names': ['X18', 'XR18'],
                'port': 10024,
                'ip_address': 18,
                'probe': {'protocol': 'udp'},
                'osc': {
                    'subscription_path': '/xremote',
                    'resubscribe_period': 10,
                },
            },
            {
                'name': 'Flow 8',
                'audio_device_names': ['FLOW 8'],
                'midi_input_names': ['FLOW 8'],
            },
        ],
        'git': {
            'reccy': {'url': 'https://github.com/rec/reccy.git'},
            'recs': {'url': 'https://github.com/rec/recs.git'},
            'streamo': {'url': 'https://github.com/rec/streamo.git'},
            'lyte': {'url': 'https://github.com/rec/lyte.git'},
            'showco': {'url': 'https://github.com/rec/showco.git'},
        },
    }
    for k, v in overrides.items():
        if k == 'networks':
            result[k] = v
        elif isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = config.merge_values(result[k], v)
        else:
            result[k] = v
    internal = result['networks']['internal']
    if not internal.pop('_x18', True):
        result['mixers'] = result['mixers'][1:]
    return result


def networks(
    *,
    x18: bool = True,
    internal_wifi: dict[str, object] | None = None,
    external_wifi: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        'internal': {
            '_x18': x18,
            'subnet': '10.0.0.0/24',
            'wifi': {
                **{'name': 'showbox', 'ip_address': 1},
                **(internal_wifi or {}),
            },
        },
        'external': {
            'wifi': external_wifi or {},
        },
    }

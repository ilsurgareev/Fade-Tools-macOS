"""Shared versioned settings. Only the settings application writes this file."""
import copy
import json
import math
import os
import tempfile
import sys
from pathlib import Path

KEYS = ('video_in', 'video_out', 'audio_in', 'audio_out')
CODECS = ('H.264', 'H.265', 'ProRes', 'DNxHR')
DEFAULT = {
    'version': 2,
    'fades': {
        'video_in': {'enabled': True, 'value': 0, 'unit': 'frames'},
        'video_out': {'enabled': True, 'value': 40, 'unit': 'frames'},
        'audio_in': {'enabled': True, 'value': 14, 'unit': 'frames'},
        'audio_out': {'enabled': True, 'value': 53, 'unit': 'frames'},
    },
    'codec': 'H.265', 'engine': 'Apple VideoToolbox' if sys.platform == 'darwin' else 'NVENC', 'quality': 'master',
    'video_bitrate_mbps': 30,
    'output_dir': '', 'suffix': '_fade', 'notifications': False,
    'presets': {}, 'macos_engine_policy': 1,
}

# Keep a named copy alongside the reset action so users can see and restore
# the transferred frame-based workflow immediately.
_DEFAULT_PRESET = {key: copy.deepcopy(value) for key, value in DEFAULT.items() if key != 'presets'}
DEFAULT['presets']['Стандартный'] = _DEFAULT_PRESET


def app_dir():
    # Override is used by isolated acceptance tests; never needed by users.
    if os.environ.get('FADE_TOOLS_HOME'):
        return Path(os.environ['FADE_TOOLS_HOME'])
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'FadeTools'
    return Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'FadeTools'


def validate(data):
    if not isinstance(data, dict) or data.get('version') != 2:
        raise ValueError('Неподдерживаемая версия настроек. Откройте Fade Settings.')
    if data.get('codec') not in CODECS:
        raise ValueError('Неизвестный кодек')
    if data.get('engine') not in ('CPU', 'NVENC', 'Apple VideoToolbox'):
        raise ValueError('Неизвестный кодировщик')
    if sys.platform == 'darwin' and data['engine'] == 'NVENC':
        raise ValueError('NVIDIA NVENC недоступен на macOS; выберите CPU')
    if data.get('quality') not in ('master', 'high', 'compact'):
        raise ValueError('Неизвестное качество')
    bitrate = data.get('video_bitrate_mbps')
    if isinstance(bitrate, bool) or not isinstance(bitrate, (int, float)) or not math.isfinite(bitrate) or not 30 <= bitrate <= 1000:
        raise ValueError('Целевой битрейт должен быть от 30 до 1000 Мбит/с')
    suffix = data.get('suffix')
    if (not isinstance(suffix, str) or any(c in suffix for c in '<>:"/\\|?*')
            or (suffix and suffix[-1] in ' .')):
        raise ValueError('Суффикс не должен содержать символы пути или заканчиваться точкой/пробелом')
    if not isinstance(data.get('output_dir'), str):
        raise ValueError('Некорректная папка')
    if not isinstance(data.get('notifications'), bool):
        raise ValueError('Некорректная настройка уведомлений')
    for key in KEYS:
        f = data.get('fades', {}).get(key, {})
        v = f.get('value')
        if (not isinstance(v, (int, float)) or isinstance(v, bool) or
                not math.isfinite(v) or not 0 <= v <= 100000):
            raise ValueError(f'Некорректная длительность: {key}')
        if f.get('unit') not in ('seconds', 'frames') or not isinstance(f.get('enabled'), bool):
            raise ValueError(f'Некорректные единицы: {key}')
        if f['unit'] == 'frames' and int(v) != v:
            raise ValueError('Количество кадров должно быть целым')
    presets = data.get('presets', {})
    if not isinstance(presets, dict) or len(presets) > 100:
        raise ValueError('Некорректные пресеты')
    for name, preset in presets.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(preset, dict):
            raise ValueError('Некорректный пресет')
        candidate = copy.deepcopy(DEFAULT)
        candidate.update(preset)
        candidate['presets'] = {}
        validate(candidate)
    if not isinstance(data.get('macos_engine_policy'), int) or isinstance(data.get('macos_engine_policy'), bool):
        raise ValueError('Некорректная политика кодирования macOS')
    return data


def load():
    path = app_dir() / 'settings.json'
    if not path.exists():
        return copy.deepcopy(DEFAULT)
    data = json.loads(path.read_text(encoding='utf-8'))
    return validate(migrate(data))


def migrate(data):
    """Upgrade old settings in memory; Droplet still never writes the JSON."""
    data = copy.deepcopy(data)
    if isinstance(data, dict) and data.get('version') == 1:
        data.update(version=2, codec='H.265', engine=DEFAULT['engine'], video_bitrate_mbps=30)
        presets = data.get('presets', {})
        if isinstance(presets, dict):
            data['presets'] = {name: migrate(preset) for name, preset in presets.items()}
    if sys.platform == 'darwin' and isinstance(data, dict) and 'macos_engine_policy' not in data:
        data['engine'] = 'Apple VideoToolbox'
        data['macos_engine_policy'] = 1
        data['presets'] = {name: migrate(preset) for name, preset in data.get('presets', {}).items()}
    # Settings created by earlier macOS builds used one second for every fade.
    # Upgrade that untouched stock configuration to the frame-based default,
    # while preserving any values the user has already customized.
    if isinstance(data, dict):
        old_stock_fades = {
            key: {'enabled': True, 'value': 1.0, 'unit': 'seconds'} for key in KEYS
        }
        if data.get('fades') == old_stock_fades or data.get('fades') == {
                key: {'enabled': True, 'value': 1, 'unit': 'frames'} for key in KEYS}:
            data['fades'] = copy.deepcopy(DEFAULT['fades'])
        data.setdefault('presets', {})
        if isinstance(data['presets'], dict):
            data['presets'].setdefault('Стандартный', copy.deepcopy(_DEFAULT_PRESET))
    return data


def save(data):
    validate(data)
    root = app_dir()
    root.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='settings-', suffix='.tmp', dir=root)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, root / 'settings.json')
    finally:
        Path(tmp).unlink(missing_ok=True)


def seconds(fade, fps):
    return (fade['value'] / fps if fade['unit'] == 'frames' else fade['value']) if fade['enabled'] else 0


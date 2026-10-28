import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from updater import parse_latest_release, validate_archive, version_tuple


def test_versions_accept_release_tags_and_reject_other_formats():
    assert version_tuple('macos-v0.2.10') == (0, 2, 10)
    assert version_tuple('0.2.1') == (0, 2, 1)
    with pytest.raises(ValueError):
        version_tuple('macos-latest')


def test_latest_release_requires_the_expected_asset_and_sha256():
    payload = {
        'tag_name': 'macos-v0.3.0',
        'draft': False,
        'prerelease': False,
        'html_url': 'https://github.com/ilsurgareev/Fade-Tools-macOS/releases/tag/macos-v0.3.0',
        'assets': [{
            'name': 'Fade-Tools-macOS-AppleSilicon.zip',
            'browser_download_url': 'https://github.com/ilsurgareev/Fade-Tools-macOS/releases/download/macos-v0.3.0/Fade-Tools-macOS-AppleSilicon.zip',
            'digest': 'sha256:' + 'a' * 64,
            'size': 1234,
        }],
    }
    release = parse_latest_release(json.dumps(payload).encode())
    assert release['version'] == '0.3.0'
    assert release['digest'] == 'a' * 64
    assert release['size'] == 1234
    payload['prerelease'] = True
    with pytest.raises(ValueError, match='не стабильный'):
        parse_latest_release(json.dumps(payload).encode())


def test_archive_requires_matching_digest_and_fade_tools_bundle(tmp_path):
    archive = tmp_path / 'release.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('Fade Tools macOS/Fade Tools.app/Contents/Info.plist', b'plist')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    validate_archive(archive, digest)
    with pytest.raises(ValueError, match='Контрольная сумма'):
        validate_archive(archive, '0' * 64)


def test_archive_rejects_path_traversal(tmp_path):
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('../outside.txt', b'bad')
        output.writestr('Fade Tools.app/Contents/Info.plist', b'plist')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='недопустимые пути'):
        validate_archive(archive, digest)

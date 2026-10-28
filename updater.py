"""Public GitHub release checks and verified, deferred macOS app replacement."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


APP_VERSION = '0.2.7'
REPOSITORY = 'ilsurgareev/Fade-Tools-macOS'
RELEASE_API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
ASSET_NAME = 'Fade-Tools-macOS-AppleSilicon.zip'
USER_AGENT = f'FadeTools/{APP_VERSION} macOS updater'
MAX_RELEASE_JSON = 1_000_000
MAX_ARCHIVE_BYTES = 1_000_000_000


def version_tuple(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r'(?:macos-v|v)?(\d+)\.(\d+)\.(\d+)', value.strip())
    if not match:
        raise ValueError('Неверный номер версии релиза')
    return tuple(map(int, match.groups()))


def parse_latest_release(raw: bytes) -> dict:
    """Read and validate a GitHub stable release API response."""
    if len(raw) > MAX_RELEASE_JSON:
        raise ValueError('Ответ сервера обновлений слишком велик')
    data = json.loads(raw.decode('utf-8'))
    if data.get('draft') or data.get('prerelease'):
        raise ValueError('GitHub вернул не стабильный релиз')
    tag = data.get('tag_name', '')
    version = version_tuple(tag)
    assets = data.get('assets')
    if not isinstance(assets, list):
        raise ValueError('В релизе не найден список файлов')
    asset = next((item for item in assets if item.get('name') == ASSET_NAME), None)
    if asset is None:
        raise ValueError(f'В релизе отсутствует {ASSET_NAME}')
    digest = asset.get('digest', '')
    match = re.fullmatch(r'sha256:([0-9a-fA-F]{64})', digest)
    url = asset.get('browser_download_url', '')
    if not match or not url.startswith('https://github.com/'):
        raise ValueError('В релизе отсутствует безопасная SHA-256 ссылка на архив')
    size = asset.get('size')
    if not isinstance(size, int) or not (0 < size <= MAX_ARCHIVE_BYTES):
        raise ValueError('Недопустимый размер архива обновления')
    return {
        'tag': tag,
        'version': '.'.join(map(str, version)),
        'version_tuple': version,
        'url': url,
        'digest': match.group(1).lower(),
        'size': size,
        'page': data.get('html_url', f'https://github.com/{REPOSITORY}/releases'),
    }


def trusted_asset_redirect(target: QUrl) -> bool:
    return (target.isValid() and target.scheme() == 'https'
            and target.host().lower() == 'release-assets.githubusercontent.com'
            and not target.userName() and not target.password()
            and target.port(-1) in (-1, 443))


def validate_archive(path: Path, expected_digest: str) -> None:
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            size += len(block)
            if size > MAX_ARCHIVE_BYTES:
                raise ValueError('Архив превышает допустимый размер')
            digest.update(block)
    if not hmac.compare_digest(digest.hexdigest(), expected_digest.lower()):
        raise ValueError('Контрольная сумма архива не совпадает с GitHub')
    if not zipfile.is_zipfile(path):
        raise ValueError('Файл обновления не является ZIP-архивом')
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) > 200_000:
            raise ValueError('В архиве слишком много файлов')
        safe_members = [Path(name) for name in names]
        if any('\\' in name or member.is_absolute() or '..' in member.parts
               for name, member in zip(names, safe_members)):
            raise ValueError('Архив содержит недопустимые пути')
        if sum(member.file_size for member in archive.infolist()) > 4_000_000_000:
            raise ValueError('Распакованный архив превышает допустимый размер')
        if not any(name.endswith('/Fade Tools.app/Contents/Info.plist')
                   for name in names):
            raise ValueError('В архиве не найдено приложение Fade Tools.app')


def installed_app_path() -> Path | None:
    if not getattr(sys, 'frozen', False):
        return None
    executable = Path(sys.executable).resolve()
    for parent in executable.parents:
        if parent.suffix == '.app':
            return parent
    return None


def start_installer(archive: Path, app_path: Path, version: str) -> None:
    """Start a small external helper, then let the caller exit the app."""
    if not app_path.is_dir() or not os.access(app_path.parent, os.W_OK):
        raise PermissionError(
            'Нет права записи рядом с приложением. Переместите его в папку '
            '«Программы» в своём домашнем каталоге и повторите обновление.'
        )
    helper = Path(tempfile.gettempdir()) / f'fade-tools-update-{os.getpid()}.sh'
    script = r'''#!/bin/bash
set -euo pipefail
APP_PID="$1"
CURRENT_APP="$2"
ARCHIVE="$3"
EXPECTED_VERSION="$4"
BASE="$(dirname "$CURRENT_APP")"
STAGE="$(/usr/bin/mktemp -d "$BASE/.fade-tools-update.XXXXXX")"
NEW_APP="$STAGE/Fade Tools macOS/Fade Tools.app"
BACKUP="$(/usr/bin/mktemp -d "$BASE/.fade-tools-backup.XXXXXX")/Fade Tools.app"
TEMP_APP="$(/usr/bin/mktemp -d "$BASE/.fade-tools-new.XXXXXX")/Fade Tools.app"
cleanup() {
  /bin/rm -rf "$STAGE" "$(dirname "$TEMP_APP")"
  if [[ -d "$CURRENT_APP" && -d "$BACKUP" ]]; then /bin/rm -rf "$(dirname "$BACKUP")"; fi
}
trap cleanup EXIT
while /bin/kill -0 "$APP_PID" 2>/dev/null; do /bin/sleep 1; done
/usr/bin/ditto -x -k "$ARCHIVE" "$STAGE"
[[ -d "$NEW_APP" ]]
ACTUAL_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$NEW_APP/Contents/Info.plist")"
[[ "$ACTUAL_VERSION" == "$EXPECTED_VERSION" ]]
/usr/bin/codesign --verify --deep --strict "$NEW_APP"
/usr/bin/ditto "$NEW_APP" "$TEMP_APP"
/bin/mv "$CURRENT_APP" "$BACKUP"
if ! /bin/mv "$TEMP_APP" "$CURRENT_APP"; then
  /bin/mv "$BACKUP" "$CURRENT_APP"
  exit 1
fi
/usr/bin/open "$CURRENT_APP"
/bin/rm -f "$ARCHIVE"
'''
    helper.write_text(script, encoding='utf-8')
    helper.chmod(0o700)
    subprocess.Popen(
        ['/bin/bash', str(helper), str(os.getpid()), str(app_path), str(archive), version],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


class UpdateService(QObject):
    check_finished = Signal(object, str)
    download_progress = Signal(int)
    download_finished = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self._reply = None
        self._release = None
        self._destination = None
        self._output = None
        self._digest = None
        self._downloaded = 0
        self._redirect_count = 0
        self._download_error = ''

    @staticmethod
    def _request(url: str, accept: str) -> QNetworkRequest:
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b'Accept', accept.encode('ascii'))
        request.setRawHeader(b'User-Agent', USER_AGENT.encode('ascii'))
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
        )
        return request

    def check(self):
        if self._reply is not None:
            return
        self._reply = self.manager.get(self._request(RELEASE_API, 'application/vnd.github+json'))
        self._reply.finished.connect(self._check_finished)

    def _check_finished(self):
        reply = self._reply
        self._reply = None
        if reply is None:
            return
        raw = bytes(reply.readAll())
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if reply.error() != QNetworkReply.NetworkError.NoError:
            message = ('Стабильный релиз обновления ещё не опубликован (HTTP 404)'
                       if status == 404 else reply.errorString())
            result = None
        else:
            try:
                result, message = parse_latest_release(raw), ''
            except Exception as exc:
                result, message = None, str(exc)
        reply.deleteLater()
        self.check_finished.emit(result, message)

    def download(self, release: dict, destination: Path):
        if self._reply is not None:
            return
        self._release = release
        self._destination = destination
        self._downloaded = 0
        self._redirect_count = 0
        self._download_error = ''
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._output = destination.open('wb')
            self._digest = hashlib.sha256()
        except Exception as exc:
            self._download_finished(None, str(exc))
            return
        self._reply = self.manager.get(self._request(release['url'], 'application/octet-stream'))
        self._reply.readyRead.connect(self._read_download_chunk)
        self._reply.downloadProgress.connect(self._progress)
        self._reply.finished.connect(self._download_reply_finished)

    def _attach_download_reply(self, reply):
        reply.readyRead.connect(self._read_download_chunk)
        reply.downloadProgress.connect(self._progress)
        reply.finished.connect(self._download_reply_finished)

    def _read_download_chunk(self):
        if self._reply is None or self._output is None:
            return
        block = bytes(self._reply.readAll())
        if not block:
            return
        self._downloaded += len(block)
        if (self._downloaded > self._release['size'] or
                self._downloaded > MAX_ARCHIVE_BYTES):
            self._download_error = 'Размер загруженного архива не совпадает с релизом'
            self._reply.abort()
            return
        try:
            self._digest.update(block)
            self._output.write(block)
        except OSError as exc:
            self._download_error = f'Не удалось сохранить архив: {exc}'
            self._reply.abort()

    def _progress(self, received: int, total: int):
        expected = self._release['size'] if self._release else total
        if expected:
            self.download_progress.emit(min(99, received * 100 // expected))

    def _download_reply_finished(self):
        reply = self._reply
        self._read_download_chunk()
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) if reply else None
        redirect = reply.attribute(QNetworkRequest.Attribute.RedirectionTargetAttribute) if reply else None
        if reply and status in (301, 302, 303, 307, 308) and redirect:
            target = reply.url().resolved(QUrl(redirect))
            self._redirect_count += 1
            if self._redirect_count <= 5 and trusted_asset_redirect(target):
                reply.deleteLater()
                self._reply = self.manager.get(self._request(
                    target.toString(), 'application/octet-stream'))
                self._attach_download_reply(self._reply)
                return
            self._download_error = 'Перенаправление загрузки ведёт на недоверенный сервер'
        network_error = reply.errorString() if reply and reply.error() != QNetworkReply.NetworkError.NoError else ''
        if reply:
            reply.deleteLater()
        self._reply = None
        error = self._download_error or network_error
        try:
            if self._output:
                self._output.close()
                self._output = None
            if not error and status is not None and not 200 <= int(status) < 300:
                error = f'Сервер вернул HTTP {status}'
            if not error and self._downloaded != self._release['size']:
                error = 'Загрузка архива завершилась не полностью'
            if not error and not hmac.compare_digest(self._digest.hexdigest(), self._release['digest']):
                error = 'Контрольная сумма загруженного файла не совпадает'
            if not error:
                validate_archive(self._destination, self._release['digest'])
        except Exception as exc:
            error = str(exc)
        if error:
            if self._destination:
                self._destination.unlink(missing_ok=True)
            self._download_finished(None, error)
        else:
            self.download_progress.emit(100)
            self._download_finished(self._destination, '')

    def _download_finished(self, path, error):
        if self._output:
            self._output.close()
            self._output = None
        self._reply = None
        self._release = None
        self._destination = None
        self._digest = None
        self._downloaded = 0
        self._download_error = ''
        self._redirect_count = 0
        self.download_finished.emit(path, error)

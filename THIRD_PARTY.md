# Components included in the macOS distribution

Python 3.12, PySide6/Qt 6.8.3, Shiboken6 and the PyInstaller bootloader are bundled in the applications. Qt/PySide6 are dynamically linked. Their license notices are included with the installed libraries and can be replaced with compatible builds in the application bundle.

FFmpeg and FFprobe are static Apple Silicon builds of FFmpeg 9.0 from https://www.osxexperts.net/ . The binaries match the SHA256 hashes published by the provider:

- ffmpeg: 591260c945d0eef150e3bf82b0ef988bd36a9cecc18ff05d6679617159f0a95e
- ffprobe: e11c17e8200b3ee4c4c186d245e2b4053f01d56957336c1817fca0b997469106

Their exact version/configuration output is included in licenses/ffmpeg-version.txt and licenses/ffprobe-version.txt. This build enables GPL components including libx264 and libx265; license texts are supplied alongside this notice. The macOS distribution contains no Windows binaries.

Component source and license locations:

- FFmpeg source: https://ffmpeg.org/releases/ffmpeg-9.0.tar.xz
- Provider build notes and dependency source locations: https://www.osxexperts.net/
- FFmpeg license details: https://ffmpeg.org/legal.html
- Qt and PySide source: https://code.qt.io/qt/ and https://code.qt.io/pyside/pyside-setup.git/
- Python: https://www.python.org/psf/license/
- PyInstaller bootloader license exception: https://pyinstaller.org/en/stable/license.html

This is a local handoff build. A public redistribution must also supply the complete corresponding source and build recipes for the exact GPL components and their dependencies; links to upstream alone are not a complete source offer.

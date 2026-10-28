# Fade Tools macOS

This folder contains the standalone macOS application. Keep its imports and build independent of the Windows and Resolve sources in the repository root.

Read README-macOS.md, PROMPT-macOS-port.md and VERIFICATION-macOS.md before changes. Preserve frame alignment, metadata checks, full output decoding, source protection, cancellation ownership and queue persistence.

Use Apple VideoToolbox by default, with one explicit CPU retry after an encoding failure. Never retry cancellation. Both applications must include verified tools/ffmpeg and tools/ffprobe, with no Homebrew dependencies at runtime.

Build using build_macos.py; generated *.spec files contain local paths and are not source artifacts. Use requirements-build-macos.txt for development. The command examples in this folder are relative to this macOS directory so the repository can be cloned into any location.

On restricted macOS automation environments, Qt 6.8 may abort when CPU feature queries are blocked. Verify with normal system access rather than disabling CPU checks. Run the source test suite and unpacked application startup/render checks before publishing a release.

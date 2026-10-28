"""Small macOS checks that can run without the optional UI test dependencies."""
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clip_engine import export_signature, matching_export, publish
from config import DEFAULT, app_dir, validate
from media import probe, run, tool


class MacOSPortTests(unittest.TestCase):
    def test_macos_settings_and_tools(self):
        self.assertEqual(DEFAULT['engine'], 'Apple VideoToolbox')
        self.assertEqual(app_dir(), Path.home() / 'Library/Application Support/FadeTools')
        invalid = copy.deepcopy(DEFAULT)
        invalid['engine'] = 'NVENC'
        with self.assertRaisesRegex(ValueError, 'NVENC'):
            validate(invalid)
        with patch.dict(os.environ, {'PATH': ''}):
            self.assertTrue(Path(tool('ffmpeg')).is_file())
            self.assertTrue(Path(tool('ffprobe')).is_file())

    def test_publication_replaces_only_matching_range(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            source = root / 'source.mp4'
            candidate = root / 'candidate.mp4'
            other = root / 'other.mp4'
            def video(path, color, signature=None):
                args = [tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
                        f'color=c={color}:s=160x90:r=25:d=1', '-c:v', 'libx264']
                if signature:
                    args += ['-metadata', 'fade_tools_range=' + signature,
                             '-movflags', '+use_metadata_tags']
                run(args + [path])
            video(source, 'black')
            signature = export_signature(source, 0, 1)
            video(candidate, 'red', signature)
            video(other, 'blue')
            cfg = copy.deepcopy(DEFAULT)
            cfg['output_dir'] = str(root)
            target = publish(candidate, source, 0, cfg)
            self.assertTrue(matching_export(target, source, 0, 1))
            self.assertFalse(matching_export(target, source, 0, 2))
            self.assertEqual(probe(target)['format']['tags']['fade_tools_range'], signature)
            first_inode = target.stat().st_ino
            publish(candidate, source, 0, cfg)
            self.assertNotEqual(target.stat().st_ino, first_inode)
            target.write_bytes(other.read_bytes())
            before = target.read_bytes()
            with self.assertRaises(FileExistsError):
                publish(candidate, source, 0, cfg)
            self.assertEqual(target.read_bytes(), before)
            self.assertTrue(source.is_file())


if __name__ == '__main__':
    unittest.main()

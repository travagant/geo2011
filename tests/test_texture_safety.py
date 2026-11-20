"""Safety of the add-on's texture output; run with python tests/test_texture_safety.py."""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from d3tool import cli, texture


class TextureSafetyTest(unittest.TestCase):
    def test_existing_different_texture_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            source = root / 'source'; source.mkdir()
            out = root / 'out'; out.mkdir()
            # The native container is arbitrary here: copying .t is byte preserving.
            (source / 'map.t').write_bytes(b'new texture')
            gltf = source / 'unit.gltf'
            gltf.write_text(json.dumps({'images': [{'uri': 'map.t'}]}))
            existing = out / 'map.t'
            existing.write_bytes(b'keep existing')
            with self.assertRaisesRegex(ValueError, 'refusing to overwrite'):
                cli._export_textures(str(gltf), str(out), 'unit', True,
                                     protect_existing=True)
            self.assertEqual(existing.read_bytes(), b'keep existing')
            existing.write_bytes(b'new texture')
            cli._export_textures(str(gltf), str(out), 'unit', True,
                                 protect_existing=True)
            self.assertEqual(existing.read_bytes(), b'new texture')

    def test_case_collision_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            source = root / 'source'; source.mkdir()
            out = root / 'out'; out.mkdir()
            (source / 'map.t').write_bytes(b'texture')
            gltf = source / 'unit.gltf'
            gltf.write_text(json.dumps({'images': [{'uri': 'map.t'}]}))
            (out / 'MAP.t').write_bytes(b'texture')
            with self.assertRaisesRegex(ValueError, 'collision'):
                cli._export_textures(str(gltf), str(out), 'unit', True,
                                     protect_existing=True)
            self.assertEqual((out / 'MAP.t').read_bytes(), b'texture')

if __name__ == '__main__':
    unittest.main()

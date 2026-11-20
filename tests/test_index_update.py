"""Run with python tests/test_index_update.py."""
import hashlib, os, pathlib, sys, tempfile, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from d3tool import vdpack, index_update

class IndexUpdateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.folder = self.root / 'resources/Characters/Empire/Inquisitor'
        self.folder.mkdir(parents=True)
        self.index = self.root / 'index'
        files = [('resources\\Characters\\Empire\\Inquisitor\\unit.g', b'old'), ('resources\\other.g', b'untouched')]
        _, self.original = vdpack.build_index(files, name='test', uuid='a', seed=77)
        self.index.write_bytes(self.original)
        (self.folder / 'unit.g').write_bytes(b'new')
        (self.folder / 'new.t').write_bytes(b'not indexed')
    def tearDown(self): self.tmp.cleanup()
    def test_update_and_backup(self):
        result = index_update.update_folder(self.index, self.folder)
        self.assertEqual(result['changed'], ['unit.g'])
        self.assertEqual(result['skipped'], ['new.t'])
        self.assertEqual(pathlib.Path(result['backup']).read_bytes(), self.original)
        before = vdpack.parse_index(vdpack.decode_index(self.original))
        after = vdpack.parse_index(vdpack.decode_index(self.index.read_bytes()))
        self.assertEqual(after.files[0].md5, hashlib.md5(b'new').hexdigest())
        self.assertEqual(after.files[1], before.files[1])
        self.assertEqual(self.index.read_bytes()[:8], self.original[:8])
        r = index_update.update_folder(self.index, self.folder)
        self.assertEqual(r['changed'], [])
        self.assertIsNone(r['backup'])
    def test_optional_add_referenced_auto_only_and_idempotent(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        name = 'd3unit_' + 'a' * 24 + '.t'
        unused = 'd3unit_' + 'b' * 24 + '.t'
        (self.folder / name).write_bytes(b'texture')
        (self.folder / unused).write_bytes(b'unused')
        fake = SimpleNamespace(parse_error=None, attrs={'material0_diffuse': name[:-2] + '.tga'}, parts=[])
        with patch.object(index_update.gfile, 'parse_geometry_file', return_value=fake):
            result = index_update.update_folder(self.index, self.folder, add_auto_textures=True)
            self.assertEqual(result['added'], [name])
            self.assertIn(unused, result['skipped'])
            doc = vdpack.parse_index(vdpack.decode_index(self.index.read_bytes()))
            record = next(x for x in doc.files if x.path.endswith(name))
            self.assertEqual(record.sha256, hashlib.sha256(b'texture').hexdigest())
            self.assertEqual(pathlib.Path(result['backup']).read_bytes(), self.original)
            before = self.index.read_bytes()
            result = index_update.update_folder(self.index, self.folder, add_auto_textures=True)
            self.assertEqual(result['added'], [])
            self.assertEqual(self.index.read_bytes(), before)

    def test_wrong_folder_no_changes(self):
        with self.assertRaises(ValueError): index_update.update_folder(self.index, self.root)
        self.assertEqual(self.index.read_bytes(), self.original)
    def test_unmatched_no_changes(self):
        folder = self.root / 'resources/Characters/Other'; folder.mkdir(parents=True)
        (folder / 'unit.g').write_bytes(b'new')
        with self.assertRaises(ValueError): index_update.update_folder(self.index, folder)
        self.assertEqual(self.index.read_bytes(), self.original)
    def test_malformed_index_no_changes(self):
        self.index.write_bytes(b'invalid')
        with self.assertRaises(ValueError): index_update.update_folder(self.index, self.folder)
        self.assertEqual(self.index.read_bytes(), b'invalid')
    def test_duplicate_record_rejected(self):
        text = vdpack.decode_index(self.original)
        text += next(l for l in text.splitlines() if 'unit.g' in l) + '\n'
        data = vdpack.encode_index(text)
        self.index.write_bytes(data)
        with self.assertRaises(ValueError): index_update.update_folder(self.index, self.folder)
        self.assertEqual(self.index.read_bytes(), data)

if __name__ == '__main__': unittest.main()

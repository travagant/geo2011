"""Safe assembly against staged and deliberately corrupted inputs."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from d3tool import char, new_unit, new_leader_bundle, vdpack


class BundleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profiles = self.root / 'source' / 'Profiles'
        self.profiles.mkdir(parents=True)
        donor = 'empire_leader-warrior'
        for name in [donor + '.char'] + [f'{k}/{donor}_{k}.char' for k in ('desc', 'stats', 'sound')] + [f'scripts/{k}_empire.s' for k in ('unit', 'town')]:
            p = self.profiles / name
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / 'Profiles' / name, p)
        self.project = self.root / 'arbitr'
        new_unit.create_profile_project(self.profiles, self.profiles / (donor + '.char'), 'arbitr', self.project, warrior_visual_no_sets=True)
        new_unit.stage_empire_recruitment(self.profiles, self.project, 'arbitr', kind='leader', donor_id=donor)
        self.visual = self.root / 'visual'
        directory = self.visual / 'resources/characters/empire/arbitr'
        directory.mkdir(parents=True)
        payload = {'arbitr_model.g': (ROOT / 'Empire/Leader-Warrior/character_empire_leader-warrior.g').read_bytes(),
                   'arbitr_model.scene': b'test scene',
                   'atlas.t': (ROOT / 'Empire/Leader-Warrior/icon_leader-warrior.t').read_bytes()}
        hashes = {}
        for name, data in payload.items():
            (directory / name).write_bytes(data)
            hashes['resources/characters/empire/arbitr/' + name] = hashlib.sha256(data).hexdigest()
        (self.visual / 'EXPERIMENT.json').write_text(json.dumps({'leader_id': 'arbitr', 'files_sha256': hashes}))
        self.dest = self.root / 'bundle'

    def build(self):
        return new_leader_bundle.build_test_bundle(self.project, self.visual, self.profiles, 'arbitr', self.dest)

    def test_pack_and_no_overwrite(self):
        result = self.build()
        pack = vdpack.read_vdpack(str(self.dest / result['archive']))
        self.assertFalse(pack.parse_error)
        report = vdpack.index_report(pack.files())
        self.assertTrue(report['decoded'])
        self.assertFalse(report['missing'] or report['mismatched'] or report['unlisted'])
        self.assertEqual(len(pack.entries), 10)
        self.assertFalse(char.parse_char(pack.payload('resources/Profiles/arbitr.char')).blocks)
        original = (self.dest / result['archive']).read_bytes()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.build()
        self.assertEqual((self.dest / result['archive']).read_bytes(), original)

    def test_optional_png_icons(self):
        from test_leader_icon_image import png
        from d3tool import leader_icon_probe, texture
        folder = self.root / 'icons'
        folder.mkdir()
        for key, (_, w, h, _) in leader_icon_probe._SLOTS.items():
            (folder / (key + '.png')).write_bytes(png(w, h, (255, 0, 0, 255)))
        result = new_leader_bundle.build_test_bundle(self.project, self.visual, self.profiles, 'arbitr', self.dest, icon_png_dir=folder)
        self.assertTrue(result['custom_icons'])
        pack = vdpack.parse_vdpack((self.dest / result['archive']).read_bytes())
        self.assertEqual(len(pack.entries), 16)
        profile = char.parse_char(pack.payload('resources/Profiles/arbitr.char'))
        for key, (suffix, w, h, fmt) in leader_icon_probe._SLOTS.items():
            path = 'resources/characters/empire/arbitr/icon_arbitr' + suffix + '.t'
            self.assertEqual(char.normalize_reference(profile.get(key)).replace('\\', '/'), path)
            img = texture.parse_t(pack.payload(path))
            self.assertEqual((img.width, img.height, img.gm_format), (w, h, fmt))
        self.assertEqual(len(list(folder.iterdir())), 6)

    def test_bad_icon_folder_does_not_create_output(self):
        folder = self.root / 'icons'
        folder.mkdir()
        with self.assertRaisesRegex(ValueError, 'exactly'):
            new_leader_bundle.build_test_bundle(self.project, self.visual, self.profiles, 'arbitr', self.dest, icon_png_dir=folder)
        self.assertFalse(self.dest.exists())

    def test_corrupt_inputs_fail_before_writing(self):
        scene = self.visual / 'resources/characters/empire/arbitr/arbitr_model.scene'
        scene.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'differs from manifest'):
            self.build()
        self.assertFalse(self.dest.exists())
        scene.write_bytes(b'test scene')
        script = self.project / 'resources/Profiles/scripts/unit_empire.s'
        script.write_bytes(script.read_bytes() + b'\n//changed')
        with self.assertRaisesRegex(ValueError, 'list differs'):
            self.build()
        self.assertFalse(self.dest.exists())

    def test_symlink_rejected(self):
        (self.project / 'extra').symlink_to(self.profiles / 'empire_leader-warrior.char')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.build()
        self.assertFalse(self.dest.exists())


if __name__ == '__main__':
    unittest.main()

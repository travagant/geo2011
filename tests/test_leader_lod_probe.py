import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from d3tool import char, leader_lod_probe, new_leader_profile


class LodProbeTest(unittest.TestCase):
    def setUp(self):
        profile = char.parse_char((ROOT / 'Profiles/empire_leader-warrior.char').read_bytes())
        profile.set_id('arbitr')
        self.src, _ = new_leader_profile.preview_visual_profile_without_warrior_sets(char.encode_char(profile), 'arbitr')

    def test_only_lod_path_changes(self):
        result = leader_lod_probe.use_main_scene_at_lod(self.src, 'arbitr')
        old = self.src.splitlines(keepends=True)
        new = result.splitlines(keepends=True)
        changes = [(a, b) for a, b in zip(old, new) if a != b]
        self.assertEqual(len(old), len(new))
        self.assertEqual(len(changes), 1)
        self.assertTrue(changes[0][0].lstrip().startswith(b'unit_scene_lod'))
        self.assertTrue(changes[0][1].endswith(b',10;\n') or changes[0][1].endswith(b',10;\r\n'))
        profile = char.parse_char(result)
        self.assertEqual(char.normalize_reference(profile.unit_scene), char.normalize_reference(profile.unit_scene_lod))
        self.assertEqual(profile.statement('unit_scene_lod').values[1:], ['10'])

    def test_rejects_unknown_input(self):
        variants = [self.src.replace(b',10;', b',20;'),
                    self.src + b'\nunit_scene_lod "other",10;\n',
                    self.src.replace(b'character_empire_leader-warrior_lod.scene', b'other.scene'),
                    self.src.replace(b'arbitr_model.scene', b'other.scene', 1),
                    self.src + b'\nset 1 {\n}\n']
        for variant in variants:
            with self.subTest(variant=variant[-80:]), self.assertRaises(ValueError):
                leader_lod_probe.use_main_scene_at_lod(variant, 'arbitr')
        with self.assertRaises(ValueError):
            leader_lod_probe.use_main_scene_at_lod(self.src, '../bad')

if __name__ == '__main__':
    unittest.main()

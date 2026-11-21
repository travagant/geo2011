import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from d3tool import char, leader_set_probe


class SetProbeTest(unittest.TestCase):
    def setUp(self):
        p = char.parse_char((ROOT / 'Profiles/empire_leader-warrior.char').read_bytes())
        p.set_id('arena_leader_probe_2026')
        self.src = char.encode_char(p)
        self.id = 'arena_leader_probe_2026'

    def test_remove_only_sets(self):
        before = char.parse_char(self.src)
        data = leader_set_probe.without_donor_sets(self.src, self.id)
        lines = self.src.decode('latin-1').splitlines(keepends=True)
        for block in reversed(before.blocks):
            del lines[block.open_line:block.close_line + 1]
        self.assertEqual(data, ''.join(lines).encode('latin-1'))
        after = char.parse_char(data)
        self.assertFalse(after.blocks)
        self.assertEqual(after.unit_scene, before.unit_scene)
        self.assertEqual(after.unit_scene_lod, before.unit_scene_lod)

    def test_refuses_unknown_or_malformed_data(self):
        variants = [self.src.replace(b'set 3 {', b'set 4 {'),
                    self.src.replace(b'    weapon ', b'    unknown ', 1),
                    self.src.replace(b'leader-warrior\\weapon', b'other\\weapon', 1),
                    self.src.replace(b'    feet ', b'    weapon ', 1)]
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                leader_set_probe.without_donor_sets(variant, self.id)
        with self.assertRaises(ValueError):
            leader_set_probe.without_donor_sets(self.src, 'wrong')


if __name__ == '__main__':
    unittest.main()

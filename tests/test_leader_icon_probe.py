import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from d3tool import char, new_leader_profile, leader_icon_probe


class IconProbeTest(unittest.TestCase):
    def setUp(self):
        p = char.parse_char((ROOT / 'Profiles/empire_leader-warrior.char').read_bytes())
        p.set_id('arbitr')
        self.profile, _ = new_leader_profile.preview_visual_profile_without_warrior_sets(char.encode_char(p), 'arbitr')
        self.icons = {key: (ROOT / 'Empire/Leader-Archmage' / ('icon_leader-archmage' + item[0] + '.t')).read_bytes()
                      for key, item in leader_icon_probe._SLOTS.items()}

    def test_six_references_only(self):
        out, resources = leader_icon_probe.prepare_icon_probe(self.profile, 'arbitr', self.icons)
        self.assertEqual(len(resources), 6)
        self.assertEqual(sum(a != b for a, b in zip(self.profile.splitlines(), out.splitlines())), 6)
        self.assertEqual(char.parse_char(out).unit_scene, char.parse_char(self.profile).unit_scene)
        for key, (suffix, *_rest) in leader_icon_probe._SLOTS.items():
            self.assertEqual(resources['resources/characters/empire/arbitr/icon_arbitr' + suffix + '.t'], self.icons[key])

    def test_bad_input_fails(self):
        for changed in ({**self.icons, 'icon': self.icons['icon'][:-1]},
                        {**self.icons, 'icon': self.icons['subicon']},
                        {k: v for k, v in self.icons.items() if k != 'icon'}):
            with self.subTest(changed=len(changed)), self.assertRaises(ValueError):
                leader_icon_probe.prepare_icon_probe(self.profile, 'arbitr', changed)
        for data in (self.profile + b'\nicon "other.t";\n',
                     self.profile.replace(b'icon_leader-warrior.t', b'other.t'),
                     self.profile + b'\nset 1 {\n}\n'):
            with self.assertRaises(ValueError):
                leader_icon_probe.prepare_icon_probe(data, 'arbitr', self.icons)

if __name__ == '__main__':
    unittest.main()

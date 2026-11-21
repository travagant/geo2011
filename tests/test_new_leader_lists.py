"""Experimental leader list staging: real donor files, no game writes."""
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from d3tool import char, new_unit


class LeaderListsTest(unittest.TestCase):
    def test_real_lists_and_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            profiles = root / 'source' / 'Profiles'
            profiles.mkdir(parents=True)
            name = 'empire_leader-warrior'
            for path in [name + '.char'] + [f'{kind}/{name}_{kind}.char' for kind in ('desc', 'stats', 'sound')] + [f'scripts/{x}_empire.s' for x in ('unit', 'town')]:
                target = profiles / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / 'Profiles' / path, target)
            original = {p: p.read_bytes() for p in profiles.rglob('*') if p.is_file()}
            project = root / 'project'
            result = new_unit.create_profile_project(profiles, profiles / (name + '.char'), 'arbitr', project)
            self.assertEqual(char.parse_char(pathlib.Path(result['profile']).read_bytes()).id, 'arbitr')
            files = new_unit.stage_empire_recruitment(profiles, project, 'arbitr', kind='leader', donor_id=name)
            for file in files:
                output = pathlib.Path(file).read_bytes()
                input_data = (profiles / 'scripts' / pathlib.Path(file).name).read_bytes()
                self.assertEqual(output, input_data.replace(b'leader "empire_leader-warrior";',
                                 b'leader "arbitr";\nleader "empire_leader-warrior";', 1))
            self.assertEqual(original, {p: p.read_bytes() for p in original})
            with self.assertRaisesRegex(ValueError, 'already exists'):
                new_unit.stage_empire_recruitment(profiles, project, 'arbitr', kind='leader', donor_id=name)

    def test_refuse_duplicates_comments_nested_and_wrong_donor(self):
        src = b'// leader "other";\nleader "warrior";\nunit "existing";\n'
        result = new_unit._unit_list_copy(src, 'arbitr', kind='leader', donor_id='warrior')
        self.assertIn(b'leader "arbitr";\nleader "warrior";', result)
        for text, donor in ((src, 'missing'), (b'{\nleader "warrior";\n}\n', 'warrior'),
                            (b'leader "warrior";\nleader "warrior";\n', 'warrior')):
            with self.assertRaises(ValueError):
                new_unit._unit_list_copy(text, 'arbitr', kind='leader', donor_id=donor)
        with self.assertRaisesRegex(ValueError, 'already appears'):
            new_unit._unit_list_copy(src, 'existing', kind='leader', donor_id='warrior')
        with self.assertRaises(ValueError):
            new_unit._unit_list_copy(src, '../bad', kind='leader', donor_id='warrior')

    def test_preflight_does_not_create_scripts_if_town_has_no_donor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            profiles = root / 'source' / 'Profiles'
            scripts = profiles / 'scripts'
            scripts.mkdir(parents=True)
            (scripts / 'unit_empire.s').write_bytes(b'leader "warrior";\n')
            (scripts / 'town_empire.s').write_bytes(b'leader "other";\n')
            project = root / 'project'
            target = project / 'resources/Profiles'
            target.mkdir(parents=True)
            (target / 'arbitr.char').write_bytes(b'id "arbitr";\n')
            with self.assertRaises(ValueError):
                new_unit.stage_empire_recruitment(profiles, project, 'arbitr', kind='leader', donor_id='warrior')
            self.assertFalse((target / 'scripts').exists())


if __name__ == '__main__':
    unittest.main()

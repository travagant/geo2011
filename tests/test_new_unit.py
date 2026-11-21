"""Run with python tests/test_new_unit.py; all operations use temporary copies."""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from d3tool import char, new_unit


class NewUnitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.profiles = self.root / 'source' / 'Profiles'
        self.profiles.mkdir(parents=True)
        self.donor = self.profiles / 'empire_inquisitor.char'
        shutil.copy2(REPO / 'Profiles' / self.donor.name, self.donor)
        for kind in ('desc', 'stats', 'sound'):
            (self.profiles / kind).mkdir()
            src = REPO / 'Profiles' / kind / ('empire_inquisitor_' + kind + '.char')
            shutil.copy2(src, self.profiles / kind / src.name)
        self.dest = self.root / 'workspace' / 'empire_test_knight'
        self.dest.parent.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, name='empire_test_knight', dest=None):
        return new_unit.create_profile_project(self.profiles, self.donor, name, dest or self.dest)

    def test_warrior_option_rejects_other_donor_without_writes(self):
        with self.assertRaisesRegex(ValueError, 'Warrior leader donor'):
            new_unit.create_profile_project(self.profiles, self.donor, 'empire_test_knight',
                                            self.dest, warrior_visual_no_sets=True)
        self.assertFalse(self.dest.exists())

    def test_real_profile_and_shared_dependencies(self):
        before = self.donor.read_bytes()
        original = char.parse_char(before)
        result = self.create()
        self.assertEqual(result['copied'], ['desc', 'sound', 'stats'])
        clone = char.parse_char(pathlib.Path(result['profile']).read_bytes())
        self.assertEqual(clone.id, 'empire_test_knight')
        self.assertEqual(clone.unit_scene, original.unit_scene)
        self.assertEqual(clone.unit_scene_lod, original.unit_scene_lod)
        self.assertEqual(self.donor.read_bytes(), before)
        for kind in ('desc', 'stats', 'sound'):
            name = 'empire_test_knight_' + kind + '.char'
            self.assertEqual((self.dest / 'resources/Profiles' / kind / name).read_bytes(),
                             (self.profiles / kind / ('empire_inquisitor_' + kind + '.char')).read_bytes())
            self.assertIn(kind + '\\\\' + name, clone.includes)
        self.assertEqual(len(list(self.dest.rglob('*.char'))), 4)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.create()
        self.assertEqual(pathlib.Path(result['profile']).read_bytes(), char.encode_char(clone))

    def test_optional_skill_grid_with_real_inquisitor(self):
        skills = self.profiles / 'skills'
        skills.mkdir()
        donor = skills / 'empire_inquisitor.grid'
        donor.write_bytes((REPO / 'Profiles/skills/empire_inquisitor.grid').read_bytes())
        original_grid = donor.read_bytes()
        original_stats = (self.profiles / 'stats/empire_inquisitor_stats.char').read_bytes()
        result = new_unit.create_profile_project(self.profiles, self.donor, 'empire_test_knight',
                                                 self.dest, copy_skill_grid=True)
        self.assertTrue(result['skill_grid_copied'])
        grid = self.dest / 'resources/Profiles/skills/empire_test_knight.grid'
        self.assertEqual(grid.read_bytes(), original_grid.replace(b'grid "empire_inquisitor"',
                                                                  b'grid "empire_test_knight"', 1))
        stats = char.parse_char((self.dest / 'resources/Profiles/stats/empire_test_knight_stats.char').read_bytes())
        self.assertEqual(stats.get('skill_grid'), 'empire_test_knight')
        self.assertEqual(donor.read_bytes(), original_grid)
        self.assertEqual((self.profiles / 'stats/empire_inquisitor_stats.char').read_bytes(), original_stats)
        pack = self.root / 'grid.vdpack'
        subprocess.run([sys.executable, '-m', 'd3tool', 'vdpack', 'pack', str(self.dest), '-o', str(pack), '--reindex'],
                       cwd=REPO, check=True, capture_output=True)
        result = subprocess.run([sys.executable, '-m', 'd3tool', 'vdpack', 'index', str(pack), '--strict'],
                                cwd=REPO, check=True, capture_output=True, text=True)
        self.assertIn('5 file record(s)', result.stdout)

    def test_skill_grid_preflight_rejects_collisions_and_bad_source(self):
        skills = self.profiles / 'skills'
        skills.mkdir()
        donor = skills / 'empire_inquisitor.grid'
        donor.write_bytes(b'grid "empire_inquisitor"\n{\n}\n')
        colliding = skills / 'different.grid'
        colliding.write_bytes(b'grid "empire_test_knight"\n{\n}\n')
        def stage():
            return new_unit.create_profile_project(self.profiles, self.donor, 'empire_test_knight', self.dest, copy_skill_grid=True)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            stage()
        self.assertFalse(self.dest.exists())
        colliding.unlink()
        donor.write_bytes(b'grid "wrong_id"\n{\n}\n')
        with self.assertRaisesRegex(ValueError, 'does not match'):
            stage()
        self.assertFalse(self.dest.exists())
        donor.unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            stage()
        self.assertFalse(self.dest.exists())

    def test_staged_profile_pack_manifest_and_extract(self):
        # Local structural check only: this is not a registered or playable unit.
        self.create()
        pack = self.root / 'experiment.vdpack'
        extracted = self.root / 'extracted'
        def cli(*args):
            return subprocess.run([sys.executable, '-m', 'd3tool', 'vdpack', *map(str, args)],
                                  cwd=REPO, check=True, text=True, capture_output=True).stdout
        result = cli('pack', self.dest, '-o', pack, '--reindex')
        self.assertNotIn('includes empire_test_knight_', result)
        self.assertIn('4 file record(s)', cli('index', pack, '--strict'))
        cli('extract', pack, '-o', extracted)
        for original in self.dest.rglob('*'):
            if original.is_file():
                self.assertEqual(original.read_bytes(),
                                 (extracted / original.relative_to(self.dest)).read_bytes())
        profile = char.parse_char((extracted / 'resources/Profiles/empire_test_knight.char').read_bytes())
        self.assertEqual(profile.id, 'empire_test_knight')
        self.assertEqual(char.parse_char(self.donor.read_bytes()).id, 'empire_inquisitor')

    def test_pack_include_resolution_is_relative_not_just_basename(self):
        from d3tool import vdpack
        self.create()
        files = {p.relative_to(self.dest).as_posix(): p.read_bytes()
                 for p in self.dest.rglob('*') if p.is_file()}
        self.assertFalse(any(' includes ' in w for w in vdpack.reference_findings(files).warnings))
        key = 'resources/Profiles/desc/empire_test_knight_desc.char'
        files['resources/elsewhere/empire_test_knight_desc.char'] = files.pop(key)
        report = vdpack.reference_findings(files)
        self.assertTrue(any('empire_test_knight_desc.char' in w for w in report.warnings))

    def test_recruitment_stage_on_real_scripts(self):
        (self.profiles / 'scripts').mkdir()
        sources = {}
        for name in ('unit_empire.s', 'town_empire.s'):
            src = REPO / 'Profiles' / 'scripts' / name
            sources[name] = src.read_bytes()
            (self.profiles / 'scripts' / name).write_bytes(sources[name])
        self.create()
        result = new_unit.stage_empire_recruitment(self.profiles, self.dest, 'empire_test_knight')
        self.assertEqual(len(result), 2)
        for name, before in sources.items():
            after = (self.dest / 'resources/Profiles/scripts' / name).read_bytes()
            added = b'unit "empire_test_knight";' + (b'\r\n' if b'\r\n' in before else b'\n')
            self.assertEqual(after.count(added), 1)
            self.assertEqual(after.replace(added, b'', 1), before)
            self.assertEqual((self.profiles / 'scripts' / name).read_bytes(), before)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            new_unit.stage_empire_recruitment(self.profiles, self.dest, 'empire_test_knight')
        pack = self.root / 'recruitment.vdpack'
        subprocess.run([sys.executable, '-m', 'd3tool', 'vdpack', 'pack', str(self.dest),
                        '-o', str(pack), '--reindex'], cwd=REPO, check=True, capture_output=True)
        check = subprocess.run([sys.executable, '-m', 'd3tool', 'vdpack', 'index', str(pack), '--strict'],
                               cwd=REPO, check=True, capture_output=True, text=True)
        self.assertIn('6 file record(s)', check.stdout)

    def test_recruitment_list_parser_preserves_bytes_and_ignores_comments(self):
        source = (b'/* unit "ignored"; { */\r\n// unit "commented";\r\n'
                  b'unit "first";\r\nbuildtree "fighters"\r\n{\r\n'
                  b' unit "nested";\r\n}\r\n// \xcf\xf0\xe8\xe2\xe5\xf2\r\n')
        out = new_unit._unit_list_copy(source, 'empire_new')
        self.assertEqual(out, source.replace(b'unit "first";',
                         b'unit "empire_new";\r\nunit "first";', 1))
        with self.assertRaisesRegex(ValueError, 'already appears'):
            new_unit._unit_list_copy(source, 'nested')
        for malformed in (b'/* unclosed', b'}\nunit "a";\n', b'group\n{\nunit "nested";\n}\n'):
            with self.assertRaises(ValueError):
                new_unit._unit_list_copy(malformed, 'empire_new')

    def test_recruitment_preflight_failure_creates_no_output(self):
        scripts = self.profiles / 'scripts'
        scripts.mkdir()
        (scripts / 'unit_empire.s').write_bytes(b'unit "other";\n')
        (scripts / 'town_empire.s').write_bytes(b'unit "empire_test_knight";\n')
        self.create()
        with self.assertRaisesRegex(ValueError, 'already appears'):
            new_unit.stage_empire_recruitment(self.profiles, self.dest, 'empire_test_knight')
        self.assertFalse((self.dest / 'resources/Profiles/scripts').exists())

    def test_duplicate_id_and_existing_filename(self):
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.create('empire_inquisitor', self.root / 'workspace' / 'duplicate')
        (self.profiles / 'other.char').write_text('id "empire_test_knight";\n')
        with self.assertRaisesRegex(ValueError, 'ID already exists'):
            self.create()
        self.assertFalse(self.dest.exists())
        (self.profiles / 'other.char').unlink()
        (self.profiles / 'empire_test_knight.char').write_text('// existing\n')
        with self.assertRaisesRegex(ValueError, 'filename already exists'):
            self.create()
        self.assertFalse(self.dest.exists())

    def test_missing_include_no_partial_project(self):
        (self.profiles / 'sound' / 'empire_inquisitor_sound.char').unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            self.create()
        self.assertFalse(self.dest.exists())

    def test_refuses_game_resources_or_source_folder(self):
        game = self.root / 'game' / 'resources' / 'Profiles'
        game.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.create(dest=game / 'project')
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.create(dest=self.profiles / 'project')
        self.assertEqual(list(game.iterdir()), [])

    def test_rejects_unsafe_ids_and_include_traversal(self):
        for name in ('../evil', 'BadName', 'a/../../evil', 'a', 'x' * 65):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.create(name)
        text = self.donor.read_text().replace('desc\\\\empire_inquisitor_desc.char', '..\\desc\\evil.char')
        self.donor.write_text(text)
        with self.assertRaisesRegex(ValueError, 'traversal'):
            self.create()
        self.assertFalse(self.dest.exists())

    def test_preserves_donor_encoding_and_line_endings(self):
        data = self.donor.read_bytes().replace(b'// Unit Profile', '// Профиль'.encode('cp1251')).replace(b'\n', b'\r\n')
        self.donor.write_bytes(data)
        result = self.create()
        clone = pathlib.Path(result['profile']).read_bytes()
        self.assertIn('// Профиль'.encode('cp1251'), clone)
        self.assertEqual(clone.count(b'\n'), clone.count(b'\r\n'))
        self.assertEqual(char.parse_char(clone).id, 'empire_test_knight')

    def test_symlink_include_rejected(self):
        path = self.profiles / 'sound' / 'empire_inquisitor_sound.char'
        other = self.root / 'other.char'
        other.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(other)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.create()
        self.assertFalse(self.dest.exists())

    def test_include_name_collision_does_not_modify_files(self):
        collision = self.profiles / 'desc' / 'empire_test_knight_desc.char'
        collision.write_bytes(b'existing')
        with self.assertRaisesRegex(ValueError, 'include filename already exists'):
            self.create()
        self.assertEqual(collision.read_bytes(), b'existing')
        self.assertFalse(self.dest.exists())

if __name__ == '__main__':
    unittest.main()

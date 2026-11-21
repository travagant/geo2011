#!/usr/bin/env python3
"""Exercise the sidebar draw paths with real bpy data and a recording layout.

Run: PYTHONPATH=/tmp/d3_bpy LD_LIBRARY_PATH=/tmp/blender-libs python tests/blender_ui_test.py
This tests panel structure and registered RNA, not pixel rendering in Blender.
"""
import os
import sys
import traceback
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'blender'))
import bpy
import io_scene_d3unit as addon


class Layout:
    def __init__(self, records):
        self.records = records
        self.enabled = True
        self.scale_y = 1

    def box(self):
        return Layout(self.records)

    def row(self, **kwargs):
        return Layout(self.records)

    def column(self, **kwargs):
        return Layout(self.records)

    def separator(self):
        pass

    def label(self, **kwargs):
        self.check_icon(kwargs)
        self.records.append(('label', kwargs.get('text', '')))

    @staticmethod
    def check_icon(kwargs):
        icon = kwargs.get('icon')
        if icon:
            valid = bpy.types.UILayout.bl_rna.functions['label'].parameters['icon'].enum_items.keys()
            assert icon in valid, 'invalid Blender icon: ' + icon

    def prop(self, obj, prop, **kwargs):
        self.check_icon(kwargs)
        if prop.startswith('["'):
            assert prop[2:-2] in obj
        else:
            getattr(obj, prop)
        self.records.append(('prop', prop))

    def operator(self, name, **kwargs):
        self.check_icon(kwargs)
        group, operator = name.split('.')
        getattr(getattr(bpy.ops, group), operator).get_rna_type()
        self.records.append(('operator', name))
        return SimpleNamespace()


def render():
    records = []
    addon.D3UNIT_PT_panel.draw(SimpleNamespace(layout=Layout(records)), bpy.context)
    return records


def main():
    addon.register()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    assert scene.d3unit_show_placement and scene.d3unit_show_texture
    assert not scene.d3unit_show_weapon and not scene.d3unit_show_index
    collapsed = render()
    assert ('operator', 'd3unit.import_unit') in collapsed
    assert ('operator', 'd3unit.export_unit') not in collapsed
    assert ('operator', 'd3unit.update_index') not in collapsed
    for name in ('d3unit_show_placement', 'd3unit_show_texture', 'd3unit_show_weapon',
                 'd3unit_show_new_profile', 'd3unit_show_index'):
        setattr(scene, name, True)
    bpy.ops.mesh.primitive_cube_add()
    expanded = render()
    assert ('label', 'Выберите профиль-донор (.char)') in expanded
    scene.d3unit_profile_donor = '/tmp/donor.char'
    scene.d3unit_profile_workspace = '/tmp/workspace'
    for invalid in ('a', 'A1', 'a-b', 'аb', 'a' * 65):
        scene.d3unit_profile_id = invalid
        assert addon.new_unit_ui.form_hints(scene), invalid
    for valid in ('ab', 'a_1', 'a' * 64):
        scene.d3unit_profile_id = valid
        assert not addon.new_unit_ui.form_hints(scene), valid
    assert ('label', 'Будет создана новая папка:') in render()
    for name in ('d3unit.align_model', 'd3unit.work_placement', 'd3unit.name_texture',
                 'd3unit.add_texture', 'd3unit.place_weapon', 'd3unit.stage_profile',
                 'd3unit.stage_empire_lists', 'd3unit.update_index'):
        assert ('operator', name) in expanded, name
    scene['d3unit_profile_result'] = '/test/project'
    bpy.data.texts.new(addon.new_unit_ui.REPORT)
    scene['d3unit_last_export'] = 'очень длинное предупреждение ' * 20
    bpy.data.texts.new(addon.EXPORT_LOG)
    old_find, old_info = addon.unitprops.find_unit_root, addon.unitprops.get_info
    addon.unitprops.find_unit_root = lambda context: bpy.context.object
    addon.unitprops.get_info = lambda root: {'base': 'test', 'g': '/test/unit.g', 'anims': []}
    try:
        populated = render()
    finally:
        addon.unitprops.find_unit_root, addon.unitprops.get_info = old_find, old_info
    for name in ('d3unit.export_unit', 'd3unit.open_log', 'd3unit.open_profile_report'):
        assert ('operator', name) in populated, name
    assert ('label', 'Остальные сообщения — в журнале') in populated

    # Audit findings must all reach the report, not just the first eight.
    old_module = addon.exporter.bridge.module
    fake_mesh = SimpleNamespace(parts=[], vertex_count=0, tri_count=0, bones=[],
                                unit_name='unit', name='unit', parse_error=None)
    fake_gfile = SimpleNamespace(parse_geometry_file=lambda data: fake_mesh,
                                 count_mismatches=lambda mesh: [])
    fake_audit = SimpleNamespace(audit=lambda folder: ({}, ['finding %d' % i for i in range(12)]))
    addon.exporter.bridge.module = lambda name: {'gfile': fake_gfile, 'audit': fake_audit}[name]
    try:
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, 'unit.g'), 'wb') as fh:
                fh.write(b'test')
            warnings = addon.exporter._verify(folder, 'unit')['warnings']
            assert len(warnings) == 12 and 'finding 11' in warnings[-1]
    finally:
        addon.exporter.bridge.module = old_module
    # A background context has no screen. Both report buttons must handle it.
    messages = []
    reporter = SimpleNamespace(log_name=addon.EXPORT_LOG,
                               report=lambda levels, message: messages.append((levels, message)))
    no_screen = SimpleNamespace(screen=None)
    block = bpy.data.texts.get(addon.EXPORT_LOG) or bpy.data.texts.new(addon.EXPORT_LOG)
    assert addon.D3UNIT_OT_open_log.execute(reporter, no_screen) == {'FINISHED'}
    block = bpy.data.texts.get('d3unit new profile') or bpy.data.texts.new('d3unit new profile')
    assert addon.new_unit_ui.D3UNIT_OT_open_profile_report.execute(reporter, no_screen) == {'FINISHED'}
    assert len(messages) == 2

    # The file dialog options are registered and reach the exporter unchanged.
    props = bpy.ops.d3unit.export_unit.get_rna_type().properties
    assert props['use_anim'].default is True
    assert props['weights'].default == 'AUTO'
    assert {'AUTO', '2', '3', '4'} <= set(props['weights'].enum_items.keys())
    calls = []
    reports = []
    fake_op = SimpleNamespace(weights='3', use_anim=False, filepath='/tmp/example.g',
                              report=lambda levels, message: reports.append((levels, message)))
    fake_context = SimpleNamespace(scene=SimpleNamespace(d3unit_index_auto=False),
                                   preferences=SimpleNamespace(addons={}))
    old_export = addon.exporter.export_unit
    try:
        def fake_export(context, path, **kwargs):
            calls.append((path, kwargs))
            return {'base': 'example', 'out_dir': '/tmp/example-output', 'stats': {}, 'warnings': []}
        addon.exporter.export_unit = fake_export
        assert addon.exporter.D3UNIT_OT_export.execute(fake_op, fake_context) == {'FINISHED'}
        assert calls[0][1]['weights'] == 3 and calls[0][1]['use_anim'] is False
        assert '.a' not in reports[-1][1]  # no claim of animation files being written
        # Export can succeed while the subsequent automatic index update fails.
        fake_context.scene = scene
        scene.d3unit_index_auto = True
        scene.d3unit_index_path = ''
        scene.d3unit_index_folder = ''
        assert addon.exporter.D3UNIT_OT_export.execute(fake_op, fake_context) == {'FINISHED'}
        assert 'индекс НЕ обновлён' in scene['d3unit_index_result']
        assert 'автоматически после экспорта' in bpy.data.texts[addon.index_ui.REPORT].as_string()
        scene.d3unit_index_auto = False
        def failing_export(context, path, **kwargs):
            raise ValueError('test export error')
        addon.exporter.export_unit = failing_export
        fake_context.scene = scene
        assert addon.exporter.D3UNIT_OT_export.execute(fake_op, fake_context) == {'CANCELLED'}
        assert 'test export error' in scene['d3unit_last_export']
        log = bpy.data.texts[addon.EXPORT_LOG].as_string()
        assert 'ОШИБКА ЭКСПОРТА' in log and 'test export error' in log
        assert 'Запрошенный путь: /tmp/example.g' in log
    finally:
        addon.exporter.export_unit = old_export

    # A donor check has a separate log and never overwrites the export failure.
    old_find, old_info, old_require, old_module = (addon.unitprops.find_unit_root,
                                                   addon.unitprops.get_info,
                                                   addon.bridge.require, addon.bridge.module)
    old_log = bpy.data.texts[addon.EXPORT_LOG].as_string()
    with tempfile.TemporaryDirectory() as folder:
        with open(os.path.join(folder, 'unit.g'), 'wb') as fh:
            fh.write(b'test')
        addon.unitprops.find_unit_root = lambda context: object()
        addon.unitprops.get_info = lambda root: {'dir': folder, 'base': 'unit'}
        addon.bridge.require = lambda *args: None
        addon.bridge.module = lambda name: SimpleNamespace(
            _find_animation_for_geometry=lambda path: None,
            _export_gl=lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('test donor check')))
        try:
            assert addon.D3UNIT_OT_self_test.execute(reporter, fake_context) == {'FINISHED'}
            assert 'test donor check' in bpy.data.texts[addon.SELF_TEST_LOG].as_string()
            assert bpy.data.texts[addon.EXPORT_LOG].as_string() == old_log
        finally:
            addon.unitprops.find_unit_root, addon.unitprops.get_info = old_find, old_info
            addon.bridge.require, addon.bridge.module = old_require, old_module
    addon.unregister()
    for prop in ('d3unit_show_placement', 'd3unit_show_texture', 'd3unit_show_weapon',
                 'd3unit_show_new_profile', 'd3unit_show_index'):
        assert not hasattr(bpy.types.Scene, prop), prop
    print('PASS: sidebar empty, expanded, selected mesh, imported unit, unregister', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)

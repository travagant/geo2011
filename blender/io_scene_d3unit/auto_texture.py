"""Extract an unambiguous diffuse image into a safely named GM texture."""
import hashlib
import os
import bpy

from . import bridge, unitprops


def material_diffuse_image(mat):
    """Select a direct Diffuse or Base Color image, without guessing maps."""
    images = {}
    if mat and mat.use_nodes and mat.node_tree:
        for link in mat.node_tree.links:
            node = link.from_node
            if (node.type == 'TEX_IMAGE' and node.image
                    and link.from_socket.name.lower() == 'color'
                    and link.to_socket.name.lower() in ('diffuse', 'base color')):
                images[node.image.as_pointer()] = node.image
    if len(images) > 1:
        raise ValueError('multiple diffuse images in one material')
    return next(iter(images.values())) if images else None


def diffuse_image(obj):
    images = {im.as_pointer(): im for slot in obj.material_slots
              for im in [material_diffuse_image(slot.material)] if im}
    if len(images) > 1:
        raise ValueError('multiple diffuse images on one mesh')
    return next(iter(images.values())) if images else None


def source_bytes(image):
    if image.is_dirty:
        raise ValueError('image has unsaved edits; save or pack it before export')
    packed = image.packed_file
    if packed:
        data = bytes(packed.data)
        name = image.filepath or image.name
    else:
        name = bpy.path.abspath(image.filepath, library=image.library)
        if not name or not os.path.isfile(name):
            raise ValueError('diffuse image has no readable file; save or pack it')
        with open(name, 'rb') as f:
            data = f.read()
    if not data:
        raise ValueError('diffuse image is empty')
    return data, name


def prepare(meshes):
    """Return derived atlas names and payloads. Explicit names always win."""
    texture = bridge.module('texture')
    derived, payloads, warnings, cached, failed, per_slot = {}, {}, [], {}, set(), {}
    known = {}
    for ob in meshes:
        atlas = str(ob.get(unitprops.DIFFUSE_PROP) or '').strip()
        if atlas and len(ob.material_slots) == 1 and ob.material_slots[0].material:
            known.setdefault(ob.material_slots[0].material.as_pointer(), set()).add(atlas)

    def image_atlas(image):
        data, name = source_bytes(image)
        digest = hashlib.sha256(data).hexdigest()
        stem = 'd3unit_' + digest[:24]
        if digest not in cached:
            payload = texture.image_to_t(data, name)
            smaller = texture.compress_t(payload, name)
            if smaller is not None and len(smaller) < len(payload):
                payload = smaller
            texture.parse_t(payload)
            cached[digest] = payload
        payloads[stem + '.t'] = cached[digest]
        return stem + '.tga'

    for ob in meshes:
        if str(ob.get(unitprops.DIFFUSE_PROP) or '').strip():
            continue
        try:
            slots = [slot.material for slot in ob.material_slots if slot.material]
            if len(slots) > 1:
                # Map each glTF primitive by its material name, not slot index:
                # Blender may omit unused slots or reorder primitives.
                names = {}
                for mat in slots:
                    atlas_set = known.get(mat.as_pointer(), set())
                    if len(atlas_set) > 1:
                        raise ValueError('ambiguous existing atlas for material ' + mat.name)
                    image = material_diffuse_image(mat)
                    atlas = next(iter(atlas_set)) if atlas_set else (image_atlas(image) if image else '')
                    if not atlas:
                        raise ValueError('no diffuse image for material ' + mat.name)
                    names[mat.name] = atlas
                per_slot[ob.name] = names
            elif slots:
                atlas_set = known.get(slots[0].as_pointer(), set())
                if len(atlas_set) == 1:
                    derived[ob.name] = next(iter(atlas_set))
                else:
                    image = material_diffuse_image(slots[0])
                    if image:
                        derived[ob.name] = image_atlas(image)
        except Exception as exc:
            failed.add(ob.name)
            warnings.append('automatic texture for %s: %s' % (ob.name, exc))
    return derived, payloads, warnings, failed, per_slot


def write_textures(out_dir, payloads):
    """Only create new files or reuse identical ones; never replace a texture."""
    for name, payload in payloads.items():
        target = os.path.join(out_dir, name)
        if os.path.islink(target):
            raise ValueError('automatic texture path is a symlink: ' + target)
        if os.path.exists(target):
            with open(target, 'rb') as f:
                if f.read() != payload:
                    raise ValueError('automatic texture name already exists with different bytes: ' + target)
            continue
        # Detect case-insensitive collisions even on a case-sensitive host.
        if any(n.lower() == name.lower() for n in os.listdir(out_dir)):
            raise ValueError('automatic texture filename conflicts: ' + target)
        created = False
        try:
            with open(target, 'xb') as f:
                created = True
                f.write(payload)
        except Exception:
            if created:
                try:
                    os.unlink(target)
                except OSError:
                    pass
            raise

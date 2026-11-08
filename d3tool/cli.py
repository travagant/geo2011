"""Command line interface for the d3tool reverse-engineering toolkit.

Provides a friendly, colour-aware interface on top of the format readers and
writers:

* ``analyze <folder>``  — inspect a unit folder (.g/.a/.ac/.scene/.gltf).
* ``export <gltf>``     — reverse-export a dis3tool glTF back to the original
  Disciples 3 files (.g / .scene / .ac / .a).
* ``export-gl <g>``     — forward-export `.g`/(`.a`) into a viewable glTF.
* ``export-all <folder>`` — recursively export every `.g` into glTF.
* ``bundle <folder>``   — run the full pipeline over a unit folder (both ways).
* ``validate <gltf>``   — structural self-check of a glTF.
* ``import <g>``        — dump the parsed `.g` as JSON.
* ``texture convert|info`` — native `.t` <-> `.dds`.
* ``vdpack list|extract|validate|manifest|pack`` — the one-file form of a unit bundle
  (what a mod ships; see ``d3tool/vdpack.py``).
* ``char show|list|find|validate|set-id`` — the ``.char`` unit profile that
  names a unit's scene, icons and sounds (see ``d3tool/char.py``).
* ``weights <g> [--against <g>]`` — what a `.g` carries weight-wise, and
  whether an export still holds the weights you painted (see
  ``d3tool/weights.py``).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import struct
import sys
import textwrap
import traceback
from typing import Dict, List, Optional, Sequence, Tuple

from . import __version__
from . import ac as acmod
from . import alias as aliasmod
from . import char as charmod
from . import anim as animmod
from . import audit as auditmod
from . import gfile
from . import gltf as gltfmod
from . import gltf_out as gltfout
from . import parts as partsmod
from . import scene as scenemod
from . import texture as texmod
from . import ui as ui
from . import vdpack as vdmod
from . import weights as weightsmod


# --------------------------------------------------------------------------- #
#  analyze
# --------------------------------------------------------------------------- #
def _analyze_unit(path: str) -> int:
    """Print a human-readable analysis of one unit folder.

    Returns 0 on success, 1 when nothing usable was found or a file failed to
    parse (so the exit code can be used by scripts).
    """
    ui.banner()
    ui.section(f"Analysis  {path}")

    g_files = sorted(glob.glob(os.path.join(path, "*.g")))
    ac_files = sorted(glob.glob(os.path.join(path, "*.ac")))
    a_files = sorted(glob.glob(os.path.join(path, "*.a")))
    gltf_files = sorted(glob.glob(os.path.join(path, "*.gltf")))
    if not (g_files or ac_files or a_files or gltf_files):
        ui.fail(f"no Disciples 3 assets found in {path}")
        return 1
    had_error = 0

    g_rows, a_rows, ac_rows, gltf_rows = [], [], [], []
    count_warnings: List[str] = []

    for g in g_files:
        try:
            data = open(g, "rb").read()
            mesh = gfile.parse_geometry_file(data)
            if mesh.parse_error:
                # A file we could not structure at all is an error, not a
                # compound container: `analyze`'s exit code is documented as
                # script-usable, so it must not pass corrupt input silently.
                ui.fail(f"{os.path.basename(g)}: unparsed ({mesh.parse_error})")
                had_error = 1
                continue
            # `raw` is set by compound containers too, so it is not on its
            # own evidence of a compound mesh — only `parts` is.
            flag = " (compound)" if mesh.parts else ""
            if mesh.form == "stub":
                flag = " (node helper)"
            stale = gfile.count_mismatches(mesh)
            for line in stale:
                count_warnings.append(f"{os.path.basename(g)}: {line} "
                                      f"(the engine reads these, `d3tool "
                                      f"repair` fixes it)")
            g_rows.append((os.path.basename(g),
                           str(mesh.vertex_count),
                           str(mesh.tri_count),
                           str(len(mesh.bones)),
                           f"w={mesh.weights_on_vertex}",
                           mesh.material_diffuse + flag))
        except Exception as exc:  # noqa: BLE001
            ui.fail(f"{os.path.basename(g)}: parse error {exc}")
            had_error = 1

    for line in count_warnings:
        ui.warn(line)

    for a in a_files:
        try:
            an = animmod.parse_anim(open(a, "rb").read())
            a_rows.append((os.path.basename(a),
                           str(len(an.bones)),
                           str(an.frame_count),
                           an.bones[0].name if an.bones else "-"))
        except Exception as exc:  # noqa: BLE001
            ui.fail(f"{os.path.basename(a)}: parse error {exc}")
            had_error = 1

    for ac in ac_files:
        try:
            cfg = acmod.parse_ac(open(ac, "r", encoding="utf-8",
                                      errors="replace").read())
            for st in cfg.states:
                ac_rows.append((os.path.basename(ac), st.name,
                                f"{st.frame0}–{st.frame1}", st.file))
        except Exception as exc:  # noqa: BLE001
            ui.fail(f"{os.path.basename(ac)}: parse error {exc}")
            had_error = 1

    for gltf in gltf_files:
        try:
            m = gltfmod.load_gltf(gltf)
            gltf_rows.append((os.path.basename(gltf),
                              str(m.vertex_count),
                              str(m.tri_count),
                              str(len(m.bones)),
                              str(len(m.frames))))
        except Exception as exc:  # noqa: BLE001
            ui.fail(f"{os.path.basename(gltf)}: parse error {exc}")
            had_error = 1

    scene_files = sorted(glob.glob(os.path.join(path, "*.scene")))
    scene_rows = []
    for sc in scene_files:
        try:
            doc = scenemod.parse_scene(
                open(sc, "rb").read().decode("latin-1"))
            kinds = {k: len(doc.find_all(k))
                     for k in ("gobj", "bones", "goclass", "particles")
                     if doc.find_all(k)}
            desc = " ".join(f"{k}:{v}" for k, v in kinds.items()) or "empty"
            scene_rows.append((os.path.basename(sc), desc,
                               str(len(doc.find_all("bones")[0].files()))
                               if doc.find_all("bones") else "0"))
        except Exception as exc:  # noqa: BLE001
            ui.fail(f"{os.path.basename(sc)}: parse error {exc}")
            had_error = 1
    if scene_rows:
        ui.table(scene_rows,
                 headers=[".scene scene", "nodes", "file refs"])

    alias_dir = os.path.join(path, "Aliases")
    if os.path.isdir(alias_dir):
        n_alias = n_entries = n_bad = 0
        empty = 0
        for cur, _dirs, files in os.walk(alias_dir):
            for fn in sorted(files):
                if not fn.lower().endswith(".alias"):
                    continue
                ap = os.path.join(cur, fn)
                try:
                    adoc = aliasmod.parse_alias_bytes(open(ap, "rb").read())
                    n_alias += 1
                    n_entries += len(adoc.sounds)
                    if not adoc.sounds:
                        empty += 1
                except Exception as exc:  # noqa: BLE001
                    n_bad += 1
                    ui.fail(f"{os.path.relpath(ap, path)}: "
                            f"parse error {exc}")
                    had_error = 1
        if n_alias or n_bad:
            summary = (f"Aliases: {n_alias} files, {n_entries} sound "
                       f"entries ({empty} muted)")
            if n_bad:
                summary += f", {n_bad} unparsable"
            ui.info(summary)
    if g_rows:
        ui.table(g_rows, headers=[".g geometry", "verts", "tris", "bones",
                                  "slots", "diffuse"])
    if a_rows:
        ui.table(a_rows, headers=[".a animation", "bones", "frames", "root"])
    if ac_rows:
        ui.table(ac_rows, headers=[".ac state", "state", "range", "file"])
    if gltf_rows:
        ui.table(gltf_rows, headers=[".gltf export", "verts", "tris", "bones",
                                     "frames"])
    # the retail unit profile that points at this folder, when one is around
    for folder in ("Profiles", "profiles"):
        if not os.path.isdir(folder):
            continue
        hit = charmod.profile_for_scene(charmod.find_profiles(folder), path)
        if hit:
            profile_path, profile = hit
            ui.info(f"profile: {profile.id}  ({profile_path})")
            ui.info("  unit_scene: "
                    + charmod.normalize_reference(profile.unit_scene or ""))
        break
    return had_error


# --------------------------------------------------------------------------- #
#  reverse export: glTF -> original D3
# --------------------------------------------------------------------------- #
def _default_attrs(mesh, geometry_base: str, res: str) -> dict:
    return {
        "dwNode": "375048704",
        "dwParent": "55867360",
        "name": mesh.name,
        "groupname": "Scene Root",
        "materials_num": "1",
        "material0_diffuse": f"{geometry_base}.tga",
        "material0_triangles_num": str(mesh.tri_count),
        "new_vertex_weights_format": "1",
        "vertexs_weights_num": str(mesh.vertex_count),
        "weights_on_vertex": str(mesh.weights_on_vertex),
        "bones_num": str(len(mesh.bones)),
    }


def _main_image_uri(gltf_path: str) -> str:
    """Basename of the image URI of the *main mesh's* material.

    The first image of the file is not necessarily the main mesh's texture
    (compound units list one image per sub-mesh), so resolve the material
    chain of ``meshes[0].primitives[0]``: material -> baseColorTexture ->
    texture -> image.  Falls back to the first real image URI.
    """
    try:
        with open(gltf_path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return ""
    images = doc.get("images") or []

    def _clean(img_index):
        if 0 <= img_index < len(images):
            uri = images[img_index].get("uri") or ""
            if uri and not uri.startswith("data:"):
                return os.path.basename(uri)
        return ""

    mesh0 = (doc.get("meshes") or [{}])[0]
    prim0 = (mesh0.get("primitives") or [{}])[0]
    mi = prim0.get("material")
    if mi is not None:
        try:
            mat = doc["materials"][mi]
            ti = mat["pbrMetallicRoughness"]["baseColorTexture"]["index"]
            si = doc["textures"][ti].get("source")
            uri = _clean(si)
            if uri:
                return uri
        except (KeyError, IndexError, TypeError):
            pass
    for img_index in range(len(images)):
        uri = _clean(img_index)
        if uri:
            return uri
    return ""


def _resource_root(gltf_path: str, base: str,
                   search_dirs: Sequence[str] = ()) -> str:
    """Derive the game resource directory (backslash path) for a glTF.

    The engine resolves character assets via a lowercase resource directory
    (e.g. ``resources\\characters\\neutrals\\airelemental``).  That name does
    *not* always match the on-disk unit folder (``Wildboar`` -> ``werewolf``),
    so we prefer the directory referenced by the sibling ``.scene``/``.ac``
    across the actual geometry files; only then fall back to a lowercase guess.

    ``search_dirs`` are extra places to look for those siblings — the donor
    folder of an export, since the glTF itself usually lives in a temp dir.
    A ``*_lod`` stem has no ``.ac``/``.scene`` of its own (the games that ship
    both models keep one folder, so only the main stem has them), and the
    main stem is the one that names the real resource folder.  Without this
    the guess below invents a directory that does not exist
    (``resources\\characters\\character_empire_angel_lod``), which leaves a
    generated ``_lod.ac`` pointing at files the engine can never find.
    """
    gltf_dir = os.path.dirname(os.path.abspath(gltf_path))
    stems = [base]
    if base.lower().endswith("_lod"):
        stems.append(base[:-4])
    for folder in [gltf_dir] + [d for d in search_dirs if d]:
        for stem in stems:
            for ext in (".scene", ".ac"):
                found = _resource_dir_from_file(
                    os.path.join(folder, stem + ext), base)
                if found:
                    return found
    rel = gltf_path.replace("\\", "/")
    parts = rel.split("/")
    if "Neutrals" in parts and len(parts) >= 2:
        res = "\\".join(["resources", "characters", "neutrals",
                          parts[-2].lower()])
    else:
        res = f"resources\\characters\\{base.lower()}"
    return res.replace("/", "\\")


def _resource_dir_from_file(path: str, base: str) -> str:
    """The ``resources\\characters\\...`` directory a `.scene`/`.ac` names.

    Empty when the file is absent or names no geometry at all.  The path that
    mentions ``base`` wins, so a scene mentioning several meshes resolves to
    the one this export is about.
    """
    import re
    if not os.path.exists(path):
        return ""
    try:
        text = open(path, "r", encoding="utf-8", errors="replace").read()
    except OSError:
        return ""
    refs = [m.group(0).strip()
            for m in re.finditer(r'resources[^"]*', text, re.IGNORECASE)]
    refs = [r for r in refs if r.lower().endswith((".g", ".ac", ".a"))]
    best = None
    for cand in refs:
        if base.lower() in cand.lower():
            best = cand
            break
    if best is None and refs:
        best = refs[0]
    if not best:
        return ""
    directory = best.rsplit("\\", 1)[0].replace("/", "\\")
    return directory if "characters" in directory.lower() else ""


def _export_textures(gltf_path: str, out_dir: str, base: str,
                     quiet: bool, src_dir: Optional[str] = None) -> Optional[str]:
    """Emit the GM ``.t`` textures referenced by a glTF's images.

    dis3tool's glTF references its diffuse texture as a ``.dds``.  The GM
    engine instead wants the native ``.t`` container, so if the referenced
    ``.dds`` exists on disk (next to the glTF) we convert it to a ``.t`` and
    write it into the output directory.

    Returns the basename of the first emitted texture (the diffuse), or
    ``None`` if none was written, so the caller can point ``material0_diffuse``
    at the actual file.
    """
    try:
        with open(gltf_path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    gltf_dir = os.path.dirname(os.path.abspath(gltf_path))
    images = doc.get("images") or []
    first = None
    for img in images:
        uri = img.get("uri")
        if not uri or uri.startswith("data:"):
            if not quiet:
                ui.warn("texture image without a file URI "
                        "(embedded bufferView) — no .t written for it")
            continue
        src = os.path.join(gltf_dir, os.path.basename(uri))
        # strip every image extension: a Blender re-save of an imported
        # ``x.dds`` writes ``x.dds.png``, and keeping the ``.dds`` in the stem
        # produced ``x.dds.t`` — a texture the engine (which looks for
        # ``x.t``) and the forward export both fail to find
        stem = texmod.clean_stem(src)

        def _find_orig(ext: str) -> Optional[str]:
            # the original `.dds`/`.t` ships next to the glTF when assets
            # were imported in place, or in the donor folder when the
            # Blender re-save lives elsewhere (weight-paint workflow)
            for d in (gltf_dir, src_dir):
                if not d:
                    continue
                p = os.path.join(d, stem + ext)
                if os.path.exists(p):
                    return p
            # same texture under a doubled extension (an export from before
            # the stem fix) or a different case
            for d in (gltf_dir, src_dir):
                if not d or not os.path.isdir(d):
                    continue
                try:
                    for cand in os.listdir(d):
                        if (texmod.clean_stem(cand).lower() == stem.lower()
                                and cand.lower().endswith(ext)):
                            return os.path.join(d, cand)
                except OSError:
                    continue
            return None

        if not src.lower().endswith(".dds"):
            # Blender re-saves textures as .png/.jpg next to the glTF.  The
            # engine wants the native `.t`, and a re-encoded DXT of a
            # re-saved PNG would not be byte-faithful anyway — so resolve
            # the ORIGINAL by stem: the sibling `.dds` (convert) or the
            # shipped `.t` (pass through verbatim).
            alt_dds = _find_orig(".dds")
            alt_t = _find_orig(".t")
            if alt_dds is not None:
                src = alt_dds
                if not quiet:
                    ui.info(f"{os.path.basename(uri)}: not a .dds — using "
                            f"the original {os.path.basename(alt_dds)}")
            elif alt_t is not None:
                out_t = os.path.join(out_dir, stem + ".t")
                with open(alt_t, "rb") as fh:
                    payload = fh.read()
                with open(out_t, "wb") as fh:
                    fh.write(payload)
                if not quiet:
                    ui.wrote(out_t, "copied the shipped .t "
                             "(the glTF image is a re-saved PNG)")
                if first is None:
                    first = os.path.basename(out_t)
                continue
            elif src.lower().endswith(".png") and os.path.exists(src):
                # Last resort: re-encode the PNG into an uncompressed .t.
                # Not byte-faithful to any original, but the unit ships
                # with a loadable texture instead of a missing one.
                try:
                    payload = texmod.png_to_t(open(src, "rb").read())
                except ValueError as exc:
                    if not quiet:
                        ui.warn(f"{os.path.basename(uri)}: PNG re-encode "
                                f"failed ({exc}) — no .t written")
                    continue
                out_t = os.path.join(out_dir, stem + ".t")
                with open(out_t, "wb") as fh:
                    fh.write(payload)
                if not quiet:
                    ui.wrote(out_t, "re-encoded from PNG as uncompressed "
                             "32-bit (place the original .dds next to the "
                             "glTF for a byte-faithful .t)")
                if first is None:
                    first = os.path.basename(out_t)
                continue
            else:
                if not quiet:
                    ui.warn(f"{os.path.basename(uri)}: no {stem}.dds/.t "
                            f"next to the glTF — no .t written; the engine "
                            f"will miss this texture")
                continue
        if not os.path.exists(src):
            if not quiet:
                ui.warn(f"{os.path.basename(uri)}: file not found — "
                        f"no .t written")
            continue
        out_t = os.path.join(out_dir, stem + ".t")
        with open(src, "rb") as fh:
            data = fh.read()
        # If the source asset ships a native .t next to the .dds, reuse its
        # header so the emitted .t round-trips byte-identically (the @24/@52
        # flags are not stored in a .dds and cannot be reconstructed).
        src_t = _find_orig(".t")
        orig_header = None
        if src_t is not None:
            with open(src_t, "rb") as fh:
                orig_header = fh.read()[:59]
        try:
            payload = texmod.dds_to_t(data, orig_header, os.path.basename(src))
        except ValueError as exc:
            # An unreadable source texture must not sink the whole unit export
            # (e.g. OrcKing's sword ships a .dds whose header declares 24-bit
            # RGB while the payload is 32-bit — dis3tool wrote it that way).
            # The native `.t` next to it is authoritative, so pass it through.
            if orig_header is not None and os.path.exists(src_t):
                with open(src_t, "rb") as fh:
                    payload = fh.read()
                if not quiet:
                    ui.warn(f"{os.path.basename(src)}: {exc} — reusing the "
                            f"shipped {stem}.t verbatim")
            else:
                if not quiet:
                    ui.warn(f"{os.path.basename(src)}: not converted — {exc}")
                continue
        with open(out_t, "wb") as fh:
            fh.write(payload)
        if not quiet:
            ui.wrote(out_t, "converted from .dds")
        if first is None:
            first = os.path.basename(out_t)
    return first




def _donor_dir(gltf_path: str, base: str) -> str:
    """Locate the folder holding the unit's original game assets.

    A weight-paint workflow keeps the Blender re-save away from the game
    tree (``Blender/<base>.gltf`` next to ``Empire/<Unit>/<base>.g``), and
    without the originals next to the glTF the export loses the donor
    `.g`/`.a`/`.ac` scaffolding — the exact files whose loss crashes the
    engine.  Walk up from the glTF folder and scan each ancestor's
    sub-tree (bounded depth) for ``<base>.g``; fall back to the glTF's own
    folder (the historical behaviour: assets imported in place).
    """
    gltf_dir = os.path.dirname(os.path.abspath(gltf_path)) or "."
    for cand in (os.path.join(gltf_dir, base + ext)
                 for ext in (".g", ".ac")):
        if os.path.isfile(cand):
            return gltf_dir
    up = gltf_dir
    for _ in range(4):
        parent = os.path.dirname(up) or "/"
        if parent == up:
            break
        up = parent
        for cur, dirs, files in os.walk(up):
            depth = os.path.relpath(cur, up).count(os.sep)
            if depth >= 3:
                dirs[:] = []
            dirs[:] = [d for d in dirs if not d.startswith(".")
                       and d not in ("__pycache__", "node_modules")]
            if base + ".g" in files and os.path.realpath(cur) \
                    != os.path.realpath(gltf_dir):
                return cur
    return gltf_dir


def _load_stream_donors(gltf_path: str, src_dir: str, base: str,
                        idle_name: str):
    """Load the unit's `.a` donor streams next to the source glTF.

    Returns [(file name, AnimFile)] -- the originals the `.ac` names, in
    `.ac` order, parsed and non-raw.  They donate the bone order/parents/
    record preambles and the trailing morph-stream scaffolding a glTF
    animation cannot carry, and they calibrate the Blender->GM frame
    conversion (see :mod:`d3tool.frame`); everything is adopted only when
    the rebuilt data verifies against them.
    """
    folder_a = src_dir
    stream_names: List[str] = []
    try:
        for nm in acmod.detect_anim_files(folder_a, base).values():
            if nm and nm not in stream_names \
                    and os.path.isfile(os.path.join(folder_a, nm)):
                stream_names.append(nm)
    except Exception:  # noqa: BLE001 - never break an export
        stream_names = []
    if not stream_names:
        # Variant stems (the leader set1/2/3 exports) carry no `.ac` of
        # their own, yet their animation came from this very folder — the
        # sibling `.ac`'s stream is the donor candidate.
        try:
            for ac_path in sorted(glob.glob(os.path.join(folder_a, "*.ac"))):
                cfg = acmod.parse_ac(
                    open(ac_path, "r", encoding="utf-8-sig",
                         errors="replace").read())
                for st in cfg.states:
                    if not st.file:
                        continue
                    nm = os.path.basename(
                        st.file.replace("\\", "/").rsplit("/", 1)[-1])
                    if nm not in stream_names and os.path.isfile(
                            os.path.join(folder_a, nm)):
                        stream_names.append(nm)
        except Exception:  # noqa: BLE001
            stream_names = []
    idle_base = os.path.basename(idle_name)
    if idle_base not in stream_names:
        stream_names = ([idle_base] + [n for n in stream_names
                                       if n != idle_base]
                        if stream_names else [])
    streams = []
    for nm in stream_names:
        try:
            acand = animmod.parse_anim(
                open(os.path.join(folder_a, nm), "rb").read())
            if not acand.raw:
                streams.append((nm, acand))
        except Exception:  # noqa: BLE001
            pass
    return streams


def _anim_bytes(animfile, donor) -> bytes:
    """The `.a` bytes to write: the donor's own when nothing moved.

    Rebuilding a stream from the glTF re-quantises every keyframe, so an export
    that changed no pose still writes a file that differs from the shipped one
    in the last bits of 300 frames.  Byte-identical output is worth more than a
    near-copy: compare the motion and keep the donor's bytes.
    """
    if donor is not None:
        try:
            if animmod.same_motion(donor, animfile) and donor.raw:
                return donor.raw
        except Exception:  # noqa: BLE001 - a comparison must never fail a write
            pass
    return animmod.write_anim(animfile)


def _export(gltf_path: str, out_dir: str, weights_on_vertex: int,
            anim: bool = True, quiet: bool = False,
            donor_dir: Optional[str] = None) -> None:
    """Reverse export: glTF -> GM geometry (.g), scene, animation config, .a.

    ``donor_dir`` names the folder holding the unit's ORIGINAL game assets
    when the exported glTF lives elsewhere (a Blender scene saved into a
    scratch folder, a `.blend` next to nothing).  The Blender add-on passes
    it explicitly instead of relying on the ancestor walk in
    :func:`_donor_dir`, which cannot see an unrelated scratch path.
    """
    if not quiet:
        ui.section("Reverse export  glTF → Disciples 3")

    m = gltfmod.load_gltf(gltf_path, weights_on_vertex=weights_on_vertex)
    # Derive the unit base with splitext, not by chopping a fixed 5 chars:
    # `d3tool export unit.glb` used to produce "unit.g" AND fail to locate the
    # sibling unit.scene/unit.ac (the lookups key off this base), silently
    # dropping every particle emitter and every event2 entry.
    base = os.path.splitext(os.path.basename(gltf_path))[0]
    src_dir = donor_dir or _donor_dir(gltf_path, base)
    if donor_dir and not os.path.isdir(donor_dir):
        if not quiet:
            ui.warn(f"donor folder '{donor_dir}' does not exist — falling "
                    f"back to the folders around the glTF")
        src_dir = _donor_dir(gltf_path, base)
    # Donate the original `.g` sitting next to the source glTF (same reuse
    # rule as the `.scene`/`.ac`): it carries the authoring data a glTF
    # cannot express -- per-vertex diffuse, container header/prelude/vertex
    # magic, light-map UVs, the per-part attribute blocks and scaffolding,
    # and the original weight/bone split (adopted only when it re-packs
    # bit-exactly to the glTF lanes, so user edits always win).
    donor = None
    donor_path = os.path.join(src_dir, base + ".g")
    if os.path.isfile(donor_path):
        try:
            candidate = gfile.parse_geometry_file(
                open(donor_path, "rb").read())
            donor = None if candidate.parse_error else candidate
        except Exception:  # noqa: BLE001 - a broken donor must not stop us
            donor = None
    # the `.a` donors must be loaded before the geometry rebuild: a
    # Blender re-save needs them to calibrate the frame conversion
    anim_files = acmod.detect_anim_files(src_dir, base)
    idle_name0 = anim_files.get("Idle") or (base + "_iadd.a")
    stream_donors = _load_stream_donors(gltf_path, src_dir, base, idle_name0)
    sm = gltfmod.mesh_to_skinned(m, weights_on_vertex=weights_on_vertex,
                                 donor=donor, anim_donors=stream_donors)
    for _w in getattr(sm, "blender_warnings", []) or []:
        if not quiet:
            ui.warn(_w)
    if not (donor is not None and donor.geometry_file):
        # the donated name2 (the `.g` header's geometry-file string) is the
        # original asset's own label and may differ from the file base
        sm.geometry_file = base
    os.makedirs(out_dir, exist_ok=True)
    res = _resource_root(gltf_path, base, (src_dir,))
    if donor is not None and donor.attrs:
        # donated attribute block: keeps the historical `.tga` material
        # name, dwNode/dwParent, tech/vdshader and any extra keys
        attrs = dict(donor.attrs)
    else:
        attrs = _default_attrs(sm, base, res)
        # without a donor the texture name is unknowable from the GM side;
        # the glTF image URI is the only source of truth (it may differ
        # from the file base: Wolfsnow ships `character_neutral_*.dds`
        # for `character_neutrals_*`), and the engine convention is the
        # `.tga` spelling the forward export renames back to `.dds`
        uri0 = _main_image_uri(gltf_path)
        if uri0:
            attrs["material0_diffuse"] = \
                os.path.splitext(uri0)[0] + ".tga"
    # textures (glTF .dds -> native .t), and point the .g at the emitted file
    emitted = _export_textures(gltf_path, out_dir, base, quiet,
                               src_dir=src_dir)
    if emitted and "material0_diffuse" not in attrs:
        attrs["material0_diffuse"] = emitted
    elif emitted and attrs.get("material0_diffuse", "").endswith(".tga"):
        pass  # the donated `.tga` name is the convention the engine expects
    else:
        # No texture was emitted; fall back to the unit base if a .t/.dds
        # happened to be written next to the output (or keep the .tga name).
        for cand in (base + ".t", base + ".dds"):
            if os.path.exists(os.path.join(out_dir, cand)):
                attrs["material0_diffuse"] = cand
                break

    # every block of a compound mesh must hang off the file's own parent node
    # (retail: 376/376 sub-meshes) -- a block the glTF lane synthesized gets
    # the placeholder ids of `_default_attrs` otherwise, and a part whose
    # parent names no node in the file is drawn as a loose object instead of
    # following the bone it is bound to
    for line in gfile.normalize_part_identity(sm, attrs.get("dwParent")):
        if not quiet:
            ui.info(f"{base}.g: {line}")

    # geometry
    g_bytes = gfile.write_geometry_file(sm, attrs)
    out_g = os.path.join(out_dir, base + ".g")
    with open(out_g, "wb") as fh:
        fh.write(g_bytes)
    if not quiet:
        ui.wrote(out_g, f"{len(g_bytes)} bytes")
    written = None
    try:
        written = gfile.parse_geometry_file(g_bytes)
        for line in gfile.count_mismatches(written):
            ui.warn(f"{base}.g: {line} — the engine will crash loading it")
    except Exception:  # noqa: BLE001 - the re-read is a diagnostic only
        written = None

    # What the export wrote about every part's size and placement, against the
    # donor the game ships.  A `.g` carries no transform, so a part that was
    # scaled/moved in Blender without applying the transform exports the way it
    # looks — 1.8x too big, a hand's width off the grip — and nothing in the
    # file looks wrong.
    if written is not None and donor is not None:
        try:
            for line in partsmod.donor_mismatches(written, donor):
                ui.warn(f"{base}.g: {line}")
        except Exception as exc:  # noqa: BLE001 - never fail an export over it
            ui.warn(f"{base}.g: not compared with the donor ({exc})")

    # scene (reuse the original if present to keep particle emitters)
    out_scene = os.path.join(out_dir, base + ".scene")
    src_scene = os.path.join(src_dir, base + ".scene")
    if os.path.exists(src_scene):
        # Some shipped `.scene` files are Latin-1, not UTF-8 (the two large
        # Neutrals/Ship scenes), and `_export` must copy them verbatim.
        with open(src_scene, "rb") as fh:
            scene_bytes = fh.read()
        note = "reused (particle emitters preserved)"
        try:
            scenemod.parse_scene(scene_bytes.decode("latin-1"))
        except Exception as exc:  # noqa: BLE001
            ui.warn(f"{base}.scene copied but does not parse: {exc}")
    else:
        scene_bytes = scenemod.write_scene(sm.name, base, res, attrs,
                                           gobj_name=sm.name).encode("latin-1")
        note = "generated (no particles)"
    # A `.g` can carry parts the scene never names — anything the donor's
    # scene had no `gobj` for, e.g. a weapon added in Blender.  The engine
    # one `gobj` per mesh part, in order — a part past the last block sits in
    # the geometry with no node to draw it; give it one, cloned from a sibling.
    if written is not None:
        try:
            patched, added = scenemod.ensure_part_gobjs(
                scene_bytes.decode("latin-1"), gfile.iter_parts(written))
        except Exception as exc:  # noqa: BLE001 - a scene we cannot patch
            patched, added = None, []
            ui.warn(f"{base}.scene: could not add the parts it does not name "
                    f"({exc}) — they are in the .g and will not be drawn")
        if added and patched is not None:
            scene_bytes = patched.encode("latin-1")
            note += f"; +{len(added)} gobj ({', '.join(added)})"
            ui.warn(f"{base}.scene did not name {', '.join(added)}: added a "
                    f"`gobj` for each, without which the engine never draws "
                    f"that part")
        try:
            fresh, restated = scenemod.refresh_part_gobjs(
                scene_bytes.decode("latin-1"), gfile.iter_parts(written))
            if restated:
                scene_bytes = fresh.encode("latin-1")
                note += f"; {len(restated)} gobj restated"
                ui.info(f"{base}.scene: restated {', '.join(restated)} from "
                        f"the mesh parts (material, counts, weights, bones)")
        except Exception as exc:  # noqa: BLE001 - never fail an export over it
            ui.warn(f"{base}.scene: gobj attributes were left as the donor "
                    f"had them ({exc})")
    with open(out_scene, "wb") as fh:
        fh.write(scene_bytes)
    if not quiet:
        ui.wrote(out_scene, note)

    # animation config — reuse the original when it ships next to the glTF,
    # exactly like the `.scene`.  Regenerating it from the template would drop
    # every `event2` entry (the attack/damage/death sound aliases and the
    # FxStrike/fxcast cues): the AirElemental alone carries seven of them.
    anim_files = acmod.detect_anim_files(src_dir, base)
    out_ac = os.path.join(out_dir, base + ".ac")
    src_ac = os.path.join(src_dir, base + ".ac")
    if os.path.exists(src_ac):
        with open(src_ac, "rb") as fh:
            ac_bytes = fh.read()
        with open(out_ac, "wb") as fh:
            fh.write(ac_bytes)
        if not quiet:
            ui.wrote(out_ac, "reused (event2 sound/FX entries preserved)")
    else:
        # Real frame counts so every generated state stays inside the file
        # it points at (a fixed 1..345 template overruns a shorter .a and
        # the engine reads past the stream's end in battle).
        counts: Dict[str, int] = {}
        for fname in set(anim_files.values()):
            p = os.path.join(src_dir, fname)
            if os.path.isfile(p):
                try:
                    counts[os.path.basename(fname)] = animmod.parse_anim(
                        open(p, "rb").read()).frame_count
                except Exception:  # noqa: BLE001
                    pass
        idle_guess = os.path.basename(anim_files.get("Idle")
                                      or f"{base}_iadd.a")
        if idle_guess not in counts and getattr(m, "frames", None):
            # the single .a this export is about to write
            counts[idle_guess] = len(m.frames)
        cfg = acmod.default_ac(f'{res}\\{base}.g', f'{res}\\{base}',
                               anim_files, frame_counts=counts)
        with open(out_ac, "w", encoding="utf-8") as fh:
            fh.write(acmod.write_ac(cfg))
        if not quiet:
            ui.wrote(out_ac, "generated (no source .ac to preserve)")

    # Sound/FX aliases.  The reused `.ac` above references them by resource
    # path (``...\\Aliases\\Attack00.alias``) but nothing copies that folder,
    # so the exported unit would ship with dangling event2 references — 2065
    # of them across the 80 bundled `.ac` files that carry any.
    src_alias_dir = os.path.join(src_dir, "Aliases")
    if os.path.isdir(src_alias_dir):
        n_alias = 0
        for cur, _dirs, files in os.walk(src_alias_dir):
            rel = os.path.relpath(cur, src_alias_dir)
            dst_dir = os.path.join(out_dir, "Aliases")
            if rel != ".":
                dst_dir = os.path.join(dst_dir, rel)
            os.makedirs(dst_dir, exist_ok=True)
            for fn in files:
                with open(os.path.join(cur, fn), "rb") as fh:
                    payload = fh.read()
                with open(os.path.join(dst_dir, fn), "wb") as fh:
                    fh.write(payload)
                n_alias += 1
                try:
                    aliasmod.parse_alias_bytes(payload)
                except Exception as exc:  # noqa: BLE001
                    ui.warn(f"Aliases/{fn}: copied but does not parse "
                            f"({exc})")
        if not quiet:
            ui.wrote(os.path.join(out_dir, "Aliases"),
                     f"{n_alias} sound/FX alias files (referenced by the .ac)")

    # animation binary
    if anim:
        try:
            # Name the rebuilt .a exactly as the .ac references it (the
            # Idle/combined file from detect_anim_files), so the engine can
            # resolve the path.  Typically <base>_iadd.a but some assets
            # use <base>.a or <base>_baseanims.a.
            idle_name = anim_files.get("Idle") or (base + "_iadd.a")
            idle_base = os.path.basename(idle_name)
            # Donate the original `.a` files (same reuse rule as the `.g`
            # donor): they supply the bone order/parents/record preambles
            # and the trailing morph-stream scaffolding a glTF animation
            # cannot carry (record tag, foreign units' streams), adopted
            # only when the rebuilt data verifies against them.  A unit
            # whose `.ac` names several `.a` files was exported as ONE
            # concatenated glTF animation — the named stream is sliced
            # back out of it at the donor frame-count boundaries.
            streams = stream_donors
            if len(streams) >= 2:
                # A multi-stream unit: dis3tool exported the `.ac`'s streams
                # as ONE concatenated glTF animation, and the reused `.ac`
                # still references every one of them — writing only the
                # Idle file would leave `run.a` (and friends) as dangling
                # engine references.  Slice each named stream back out of
                # the concat at the donor frame-count boundaries, each with
                # its own donor for the scaffolding a glTF cannot carry.
                for nm, stream_donor in streams:
                    animfile = gltfmod.animation_from_gltf(
                        m, donor=stream_donor, streams=streams, out_name=nm)
                    if not (animfile.bones or animfile.morphs):
                        continue
                    out_a = os.path.join(out_dir, nm)
                    with open(out_a, "wb") as fh:
                        fh.write(_anim_bytes(animfile, stream_donor))
                    if not quiet:
                        ui.wrote(out_a, f"{os.path.getsize(out_a)} bytes, "
                                        f"{len(animfile.bones)} bones")
            else:
                donor_a = next((a for n, a in streams if n == idle_base), None)
                if donor_a is None and len(streams) == 1:
                    # variant stems name a stream that does not exist next
                    # to the glTF; the one verifiable sibling stream is the
                    # donor
                    donor_a = streams[0][1]
                animfile = gltfmod.animation_from_gltf(m, donor=donor_a,
                                                       streams=streams,
                                                       out_name=idle_base)
                if animfile.bones or animfile.morphs:
                    a_bytes = _anim_bytes(animfile, donor_a)
                    out_a = os.path.join(out_dir, os.path.basename(idle_name))
                    with open(out_a, "wb") as fh:
                        fh.write(a_bytes)
                    if not quiet:
                        ui.wrote(out_a, f"{len(a_bytes)} bytes, "
                                        f"{len(animfile.bones)} bones")
        except Exception as exc:  # noqa: BLE001
            if not quiet:
                ui.skipped(base + " <anim>", str(exc))
    # The LOD model is a separate `.g` pair: a part this export added (a
    # weapon) stays invisible on the battlefield — where the profile swaps to
    # `unit_scene_lod` — until the LOD mesh carries it and the LOD scene has
    # its `gobj`.  The LOD is not part of the export (it is the shipped
    # low-detail copy), so it is taken from the donor folder when the output
    # folder has none, then synced.
    if donor_dir and os.path.abspath(donor_dir) != os.path.abspath(out_dir):
        for ext in ("_lod.g", "_lod.scene"):
            src = os.path.join(donor_dir, base + ext)
            dst = os.path.join(out_dir, base + ext)
            if os.path.isfile(src) and not os.path.isfile(dst):
                shutil.copyfile(src, dst)
                if not quiet:
                    ui.wrote(dst, "copied from the donor (the LOD model)")
    try:
        if any(fn.endswith("_lod.g") for fn in os.listdir(out_dir)):
            _lod_sync(out_dir, quiet=quiet)
    except Exception as exc:  # noqa: BLE001 - never fail an export over it
        ui.warn(f"{base}: the LOD model was not synced ({exc}) — an added "
                f"weapon will not show on the battlefield")

    _carry_material_textures(out_dir, base, src_dir, quiet)
    _battle_readiness_check(out_dir, base, quiet)
    if not quiet:
        print("")


def _shrink_texture(path: str, quiet: bool) -> bool:
    """Block-compress a texture in place when it is stored as raw pixels.

    A `.t` can hold A8R8G8B8, and everything that came out of Blender used to
    be written that way: 683 KiB for a 128x1024 blade whose shipped copy is
    86 KiB.  The pixels do not change, the format does, so this is done on
    every export — including on a texture the artist dropped in himself.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        small = texmod.compress_t(data, path)
    except Exception:  # noqa: BLE001 - an odd texture must not fail an export
        return False
    if small is None or len(small) >= len(data):
        return False
    try:
        with open(path, "wb") as fh:
            fh.write(small)
    except OSError:
        return False
    if not quiet:
        ui.wrote(path, f"re-encoded {len(data) // 1024} KB raw pixels -> "
                       f"{len(small) // 1024} KB {texmod.describe(path, True)}")
    return True


def _write_texture(src: str, dst: str, quiet: bool, why: str) -> None:
    """Copy a texture into the export folder, block-compressing it on the way."""
    try:
        with open(src, "rb") as fh:
            data = fh.read()
        small = texmod.compress_t(data, src)
    except Exception:  # noqa: BLE001
        small = None
    if small is not None and len(small) < len(data):
        with open(dst, "wb") as fh:
            fh.write(small)
        if not quiet:
            ui.wrote(dst, f"{why}; re-encoded {len(data) // 1024} KB raw "
                          f"pixels -> {len(small) // 1024} KB "
                          f"{texmod.describe(dst, True)}")
        return
    shutil.copyfile(src, dst)
    if not quiet:
        ui.wrote(dst, why)


def _carry_material_textures(out_dir: str, base: str,
                             src_dir: Optional[str], quiet: bool) -> None:
    """Make the output folder carry every texture its own `.g` names.

    A compound unit names one diffuse per part, and a part can be re-textured
    from a texture that lives in *another* unit's folder (a sword wearing the
    Knight's blade diffuse).  The glTF only carries the file while the scene
    stays open: the export writes the `.g` with the material name and nothing
    else, so the exported folder ended up naming a texture it did not ship.
    In game that is invisible — the retail copy is still in the game data — but
    the moment the export is read back (the add-on's own import, or anyone
    else opening the folder) the part has no texture at all.  Copy the file in
    from the donor folder (or the glTF's own folder), converting a `.dds`
    when that is all there is, so an export is self-contained.
    """
    g_path = os.path.join(out_dir, base + ".g")
    if not os.path.isfile(g_path):
        return
    try:
        mesh = gfile.parse_geometry_file(open(g_path, "rb").read())
    except Exception:  # noqa: BLE001 - a bad .g is reported by the checker
        return
    if getattr(mesh, "parse_error", None):
        return
    dirs = [out_dir] + ([src_dir] if src_dir else [])
    wanted: List[str] = []
    for blk in [mesh] + list(getattr(mesh, "parts", ()) or ()):
        for key, value in (getattr(blk, "attrs", None) or {}).items():
            low = key.lower()
            if value and (low.endswith("_diffuse") or low.endswith("_lightmap")):
                wanted.append(value)
    for mat in dict.fromkeys(wanted):
        stem = texmod.clean_stem(mat)
        if not stem:
            continue
        existing = os.path.join(out_dir, stem + ".t")
        if os.path.isfile(existing):
            _shrink_texture(existing, quiet)
            continue
        src_t = src_dds = None
        for d in dirs:
            if not d or not os.path.isdir(d):
                continue
            if src_t is None and os.path.isfile(os.path.join(d, stem + ".t")):
                src_t = os.path.join(d, stem + ".t")
            if src_dds is None and os.path.isfile(os.path.join(d, stem + ".dds")):
                src_dds = os.path.join(d, stem + ".dds")
            if src_t is None:
                # the folder may ship the texture under a doubled extension
                # (``x.dds.t``, written by an export from before the stem fix)
                try:
                    for cand in os.listdir(d):
                        if (cand.lower().endswith(".t")
                                and texmod.clean_stem(cand).lower()
                                == stem.lower()):
                            src_t = os.path.join(d, cand)
                            break
                except OSError:
                    pass
            if src_t is not None:
                break
        dst = os.path.join(out_dir, stem + ".t")
        try:
            if src_t is not None:
                if os.path.abspath(src_t) == os.path.abspath(dst):
                    _shrink_texture(dst, quiet)
                    continue
                _write_texture(src_t, dst, quiet,
                               "carried with the unit (its .g names it)")
            elif src_dds is not None:
                texmod.convert_file(src_dds, dst)
                if not quiet:
                    ui.wrote(dst, "converted from the unit's .dds")
            elif not quiet:
                ui.warn(f"{stem}.t: no such texture next to the export or in "
                        f"the donor folder — '{mat}' will load untextured "
                        f"(drop {stem}.t into the output folder to fix it)")
        except Exception as exc:  # noqa: BLE001 - never fail an export over it
            if not quiet:
                ui.warn(f"{stem}.t: could not be carried — {exc}")


def _battle_readiness_check(out_dir: str, base: str, quiet: bool) -> None:
    """Verify what the battle loader needs, right after writing a unit.

    Catches the failure modes that only appear in game — when the unit walks
    into battle and the engine starts reading the produced files: a `.ac`
    state naming an `.a` that was never written, a frame range running past
    the end of its stream, a `meshfile`/`.scene` reference that dangles, or
    a diffuse material whose `.t` is missing.  Each finding is a loud
    warning, not a crash: partial exports stay inspectable.
    """
    try:
        findings: List[str] = []
        skeleton: List[str] = []          # every bone name the .g declares
        out_g = os.path.join(out_dir, base + ".g")
        if os.path.isfile(out_g):
            g_data = open(out_g, "rb").read()
            attrs, _ = gfile.parse_attributes(g_data)
            mat = attrs.get("material0_diffuse", "")
            if mat:
                want = os.path.splitext(mat)[0] + ".t"
                if not os.path.isfile(os.path.join(out_dir, want)):
                    findings.append(
                        f"material0_diffuse '{mat}' has no {want} in the "
                        f"output — the unit will load without its texture")
            # the geometry itself must survive its own round-trip: a `.g`
            # whose attribute block lies about its counts (or whose vertex
            # bone indices run past the part's bone table) desynchronises
            # the loader halfway through and the game dies on unit spawn
            try:
                sm_g = gfile.parse_geometry_file(g_data)
                if sm_g.parse_error:
                    findings.append(f"{base}.g does not re-parse cleanly: "
                                    f"{sm_g.parse_error}")
                blocks = [("root", sm_g.vertices, sm_g.bones)]
                blocks += [(p.name, p.vertices, p.bones)
                           for p in sm_g.parts]
                for nm, verts, bones in blocks:
                    n_b = len(bones)
                    have_w = any(any(w for w in (v.stored_weights or ()))
                                 for v in verts)
                    if verts and have_w and not n_b:
                        findings.append(
                            f"{base}.g part {nm}: skinned vertices but an "
                            f"empty bone table — the engine resolves them "
                            f"to garbage")
                    for v in verts:
                        if any(int(b) >= max(n_b, 1)
                               for b in (v.bones or ()) if b):
                            findings.append(
                                f"{base}.g part {nm}: vertex bone index "
                                f"past the {n_b}-bone table")
                            break
                # a compound part carries its own material: a part whose
                # atlas never made it into the output renders untextured (or
                # crashes the loader on some engines) even when the body's
                # texture is present
                for prt in [sm_g] + list(sm_g.parts):
                    pmat = (prt.attrs or {}).get("material0_diffuse", "")
                    if not pmat:
                        continue
                    pwant = os.path.splitext(pmat)[0] + ".t"
                    if not os.path.isfile(os.path.join(out_dir, pwant)):
                        findings.append(
                            f"{base}.g part {prt.name}: material0_diffuse "
                            f"'{pmat}' has no {pwant} in the output")
                # skinning that came out of the round-trip rigid or missing:
                # the unit folds around one bone (or hangs loose) in game and
                # nothing in the file looks broken enough to notice
                findings.extend(weightsmod.skinning_problems(sm_g))
                for blk in [sm_g] + list(sm_g.parts):
                    skeleton.extend(b.name for b in (blk.bones or ()))
                names = [p.name for p in sm_g.parts]
                if len(set(names)) != len(names):
                    dupes = sorted({n for n in names if names.count(n) > 1})
                    findings.append(
                        f"{base}.g has duplicate part names "
                        f"({', '.join(dupes)}) — the engine keeps only one")
            except Exception as exc:  # noqa: BLE001
                findings.append(f"{base}.g failed to re-parse: {exc}")
        # the LOD model is a separate mesh and the profile swaps to it past a
        # draw distance: a part the main `.g` gained (a weapon) is invisible
        # on the battlefield until the LOD carries it as well
        lod_g = os.path.join(out_dir, base + "_lod.g")
        if os.path.isfile(lod_g) and os.path.isfile(out_g):
            try:
                main_parts = gfile.iter_parts(
                    gfile.parse_geometry_file(open(out_g, "rb").read()))
                lod_parts = gfile.iter_parts(
                    gfile.parse_geometry_file(open(lod_g, "rb").read()))
                if len(lod_parts) < len(main_parts):
                    findings.append(
                        f"{base}_lod.g has {len(lod_parts)} mesh part(s), "
                        f"the main .g has {len(main_parts)} — the battle "
                        f"camera swaps to the LOD at distance and the extra "
                        f"part(s) will not be drawn (run `d3tool lod`)")
            except Exception as exc:  # noqa: BLE001
                findings.append(f"{base}_lod.g failed to re-parse: {exc}")
        for ac_path in glob.glob(os.path.join(out_dir, "*.ac")):
            cfg = acmod.parse_ac(open(ac_path, "r", encoding="utf-8-sig",
                                      errors="replace").read())
            for st in cfg.states:
                if not st.file:
                    continue
                fn = os.path.basename(st.file.replace("\\", "/")
                                      .rsplit("/", 1)[-1])
                p = os.path.join(out_dir, fn)
                if not os.path.isfile(p):
                    findings.append(f".ac state {st.name} -> {fn}: "
                                    f"file was not written")
                    continue
                if fn.endswith(".a") and st.frame1:
                    a = animmod.parse_anim(open(p, "rb").read())
                    # frames are 0-based inclusive indices, so the last one a
                    # stream has is `frame_count - 1` (no bundled state asks
                    # for more; see `ac.default_ac`)
                    if st.frame1 >= a.frame_count:
                        findings.append(
                            f".ac state {st.name}: frames go to "
                            f"{st.frame1} but {fn} has {a.frame_count} "
                            f"(last index {a.frame_count - 1})")
                    # A stream that carries one track named after the object
                    # instead of the armature is what a Blender export leaves
                    # behind when the meshes are not skinned to the armature:
                    # the unit then stands in its rest pose (or falls apart)
                    # while the skeleton it belongs to is 20+ bones.
                    if skeleton and len(a.bones) <= 1:
                        only = a.bones[0].name if a.bones else "-"
                        findings.append(
                            f"{fn}: {len(a.bones)} bone track ({only!r}) for "
                            f"a {len(set(skeleton))}-bone skeleton — the "
                            f"armature was not exported, the unit cannot "
                            f"animate")
                if st.meshfile:
                    mf = os.path.basename(st.meshfile.replace("\\", "/")
                                          .rsplit("/", 1)[-1])
                    if not os.path.isfile(os.path.join(out_dir, mf)):
                        findings.append(f".ac state {st.name}: meshfile "
                                        f"{mf} was not written")
        for sc in glob.glob(os.path.join(out_dir, "*.scene")):
            try:
                txt = open(sc, "rb").read().decode("latin-1")
                scenemod.parse_scene(txt)
            except Exception as exc:  # noqa: BLE001
                findings.append(f"{os.path.basename(sc)}: does not parse "
                                f"({exc})")
                continue
            import re as _re
            for mm in _re.finditer(r'file\s+"([^"]+)"', txt):
                fn = os.path.basename(mm.group(1).replace("\\", "/"))
                if not os.path.isfile(os.path.join(out_dir, fn)):
                    findings.append(f"{os.path.basename(sc)} references "
                                    f"{fn}, which was not written")
        # one line per distinct problem: a unit whose `.ac` names the same
        # stream in five states must not repeat the finding five times
        findings = list(dict.fromkeys(findings))
        if findings and not quiet:
            ui.warn("battle-readiness check FAILED — fix before shipping:")
            for f in findings:
                ui.warn("  " + f)
    except Exception as exc:  # noqa: BLE001 - diagnostics must never break
        if not quiet:
            ui.warn(f"battle-readiness check skipped: {exc}")


# --------------------------------------------------------------------------- #
#  forward export: original D3 -> glTF
# --------------------------------------------------------------------------- #
def _resolve_texture(g_path: str, attrs, texture: Optional[str],
                     out_dir: str, quiet: bool) -> Optional[str]:
    """Resolve a texture to hand to the forward-export glTF writer.

    Returns the image *uri* (a bare filename) to reference in the glTF, or
    ``None`` if no texture could be used.  If the source texture is a ``.t``
    container it is converted to a ``.dds`` next to the glTF (matching what
    dis3tool references); the URI is then the ``.dds`` basename so the file
    resolves relative to the output glTF.
    """
    src = None
    if texture:
        # explicit -t may be a path, or just a bare name to copy alongside
        cand = os.path.join(os.path.dirname(g_path), texture) \
            if not os.path.isabs(texture) else texture
        if os.path.exists(cand):
            src = cand
        elif os.path.exists(texture):
            src = texture
        else:
            # treat as a filename to reference as-is (no file yet)
            return texture
    else:
        src = texmod.find_diffuse_texture(g_path, attrs)

    if not src:
        return None

    # Emit the texture as a .dds (converting a .t source) so it sits next to
    # the glTF output and the URI resolves.
    base_src = os.path.splitext(os.path.basename(src))[0]
    out_dds = os.path.join(out_dir, base_src + ".dds")
    if src.lower().endswith(".t"):
        with open(src, "rb") as fh:
            data = fh.read()
        with open(out_dds, "wb") as fh:
            fh.write(texmod.t_to_dds(data, os.path.basename(src)))
        if not quiet:
            ui.wrote(out_dds, "converted from .t")
    elif src.lower().endswith(".dds"):
        # copy the .dds so the glTF can be rooted in any output directory
        with open(src, "rb") as fh:
            data = fh.read()
        with open(out_dds, "wb") as fh:
            fh.write(data)
        if not quiet:
            ui.wrote(out_dds, "texture")
    else:
        # .tga or other: reference the basename but do not mangle it
        return os.path.basename(src)

    return os.path.basename(out_dds)


def _emit_compound_textures(g_path: str, mesh, out_dir: str,
                            quiet: bool) -> None:
    """Sit every sub-mesh material texture next to a compound glTF export.

    The sub-mesh ``material0_diffuse`` / ``material0_lightmap`` attributes
    name a historical ``.tga``; dis3tool references the same stem as ``.dds``
    in the glTF.  The glTF writer derives those URIs itself — here we only
    make the file exist: copy an already-converted ``.dds`` or convert the
    shipped ``.t`` container.
    """
    import shutil

    def _names():
        yield mesh.material_diffuse, False
        yield mesh.lightmap, True
        for part in mesh.parts:
            yield part.material_diffuse, False
            yield part.lightmap, True

    src_dir = os.path.dirname(os.path.abspath(g_path))
    for value, _is_lm in _names():
        if not value:
            continue
        stem = texmod.clean_stem(value)
        dds = stem + ".dds"
        dst = os.path.join(out_dir, dds)
        if os.path.exists(dst):
            continue
        src_dds = os.path.join(src_dir, dds)
        src_t = os.path.join(src_dir, stem + ".t")
        try:
            if os.path.exists(src_dds):
                shutil.copy2(src_dds, dst)
            elif os.path.exists(src_t):
                texmod.convert_file(src_t, dst)
            else:
                continue
            if not quiet:
                ui.wrote(dst)
        except Exception as exc:  # noqa: BLE001
            if not quiet:
                ui.fail(f"{dds}: texture conversion failed — {exc}")


def _load_anim_stream(g_path: str, anim_path: str, quiet: bool):
    """Load the animation for a forward export, concatenating if needed.

    When the unit's `.ac` names several `.a` files dis3tool exports them as one
    animation spanning every stream, so a single file would come out short
    (Angel: 64 of 263 frames).  Concatenate them in `.ac` order instead;
    :func:`d3tool.anim.concat_anims` passes a lone stream through untouched.
    """
    folder = os.path.dirname(os.path.abspath(g_path))
    stem = os.path.splitext(os.path.basename(g_path))[0]
    # The concat set must come from the SAME `.ac` that chose the stream —
    # the `.g`'s own stem first (`<mesh>_lod.ac` names the *lod* stream pair,
    # e.g. cleric_lod.ac -> iadd_lod.a + run_lod.a), falling back to the
    # de-lodded main config exactly like `_find_animation_for_geometry`.
    # Resolving the set from the main stem alone handed a lod export the
    # lod stream's Idle stretch and silently dropped its Run (cleric_lod:
    # 356 of 381 frames).
    streams: List[str] = []
    try:
        for name in acmod.detect_anim_files(folder, stem).values():
            if name and name not in streams \
                    and os.path.isfile(os.path.join(folder, name)):
                streams.append(name)
    except Exception:  # noqa: BLE001 - never let this break an export
        streams = []
    chosen = os.path.basename(anim_path)
    if len(streams) < 2 or chosen not in streams:
        return animmod.parse_anim(open(anim_path, "rb").read())
    # Always concat in `.ac` order — dis3tool does, whichever stream the
    # caller named (the `-a` selects the unit, not the layout).  Starting
    # from the requested stream re-ordered the frames and moved the morph
    # targets, which concat_anims takes from the *last* stream (Cleric's
    # reference carries _run.a's 25, Run being the final `.ac` state).
    ordered = list(streams)
    parsed = [animmod.parse_anim(open(os.path.join(folder, n), "rb").read())
              for n in ordered]
    anim = animmod.concat_anims(parsed)
    if not quiet:
        ui.info(f"{chosen}: concatenated {len(ordered)} animation streams "
                f"({', '.join(ordered)}) into {anim.frame_count} frames")
    return anim


def _warn_if_animation_truncated(g_path: str, anim, anim_path: str) -> None:
    """Warn when the chosen `.a` covers only part of the unit's animation.

    dis3tool concatenates *every* distinct `.a` that the unit's `.ac`
    references (verified on all 24 bundled units that reference more than one:
    the reference glTF frame count equals the sum, e.g. Angel 64+84+28+32+55 =
    263).  d3tool currently exports a single stream, so those units come out
    short — say so instead of writing a silently truncated animation.
    """
    folder = os.path.dirname(os.path.abspath(g_path))
    stem = os.path.splitext(os.path.basename(g_path))[0]
    # Same authoritative source as `_load_anim_stream`: the `.g`'s own
    # `.ac` first (lod configs included), then the de-lodded main one.
    try:
        states = acmod.detect_anim_files(folder, stem)
    except Exception:  # noqa: BLE001 - a warning must never break the export
        return
    seen: List[str] = []
    for name in states.values():
        if name and name not in seen:
            seen.append(name)
    if len(seen) < 2:
        return
    total = 0
    for name in seen:
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            total += animmod.parse_anim(open(p, "rb").read()).frame_count
    if total > anim.frame_count:
        ui.warn(f"{os.path.basename(anim_path)} covers {anim.frame_count} of "
                f"{total} frames: the .ac references {len(seen)} animation "
                f"files ({', '.join(seen)}) and dis3tool concatenates them")


def _export_gl(g_path: str, anim_path: Optional[str], out: Optional[str],
               texture: Optional[str], quiet: bool = False) -> Tuple[str, str]:
    if not quiet:
        ui.section("Forward export  Disciples 3 → glTF")
    data = open(g_path, "rb").read()
    mesh = gfile.parse_geometry_file(data)
    anim = None
    if anim_path:
        anim = _load_anim_stream(g_path, anim_path, quiet)
        # An explicitly passed `-a` can name a single stream of a multi-
        # stream unit (or another unit's file entirely) — say so instead
        # of writing a silently shortened animation.
        _warn_if_animation_truncated(g_path, anim, anim_path)
    if not out:
        out = (g_path[: -len(".g")] if g_path.endswith(".g") else g_path + ".gltf")
    attrs, _ = gfile.parse_attributes(data)
    out_dir = os.path.dirname(os.path.abspath(out))
    os.makedirs(out_dir, exist_ok=True)
    if mesh.parts:
        # compound container: the glTF derives its per-sub-mesh material
        # URIs from the `.g` attributes (tga -> dds rename), matching the
        # dis3tool reference exports; we only stage the image files
        _emit_compound_textures(g_path, mesh, out_dir, quiet)
        gt, bt = gltfout.write_gltf_to(out, mesh, anim)
    else:
        uri = _resolve_texture(g_path, attrs, texture, out_dir, quiet)
        gt, bt = gltfout.write_gltf_to(out, mesh, anim, texture=uri)
    if not quiet:
        ui.wrote(gt)
        ui.wrote(bt)
    return gt, bt


# --------------------------------------------------------------------------- #
#  recursive forward export
# --------------------------------------------------------------------------- #
def _find_animation_for_geometry(g_path: str) -> Optional[str]:
    """Find the most likely animation for a geometry file.

    Prefer the animation referenced by the sibling animation config.  This is
    important for names such as ``*_iadd.a`` and ``*_baseanims.a``.  LOD files
    normally share the main model's config.  If no config gives an answer, use
    conventional names, then a sole animation in the directory.

    When the config *names* streams but none of them exists in the unit
    folder, there is no fallback: dis3tool exports such a unit rigid
    (Blacknaga's config points at mermaid's `.a`, which is not in its
    folder), and guessing a conventional `.a` here would animate a mesh the
    reference leaves rigid.
    """
    folder = os.path.dirname(os.path.abspath(g_path))
    stem = os.path.splitext(os.path.basename(g_path))[0]
    main_stem = stem[:-4] if stem.lower().endswith("_lod") else stem

    for ac_stem in dict.fromkeys((stem, main_stem)):
        ac_path = os.path.join(folder, ac_stem + ".ac")
        if not os.path.isfile(ac_path):
            continue
        try:
            cfg = acmod.parse_ac(open(ac_path, "r", encoding="utf-8-sig",
                                      errors="replace").read())
        except OSError:
            continue
        named = False
        for state in cfg.states:
            if state.file:
                named = True
                candidate = os.path.join(
                    folder, state.file.replace("\\", "/").rsplit("/", 1)[-1])
                if os.path.isfile(candidate):
                    return candidate
        if named:
            # The unit's own config names streams that are not in its
            # folder — ship it rigid, like the dis3tool reference does.
            return None

    for base in dict.fromkeys((stem, main_stem)):
        for suffix in (".a", "_iadd.a", "_baseanims.a"):
            candidate = os.path.join(folder, base + suffix)
            if os.path.isfile(candidate):
                return candidate

    animations = sorted(glob.glob(os.path.join(folder, "*.a")))
    if len(animations) != 1:
        return None
    # A folder can hold several meshes (the Cyclop and its rock projectile),
    # and the sole `.a` belongs to exactly one of them.  Hand it to this
    # `.g` only when a sibling `.ac` claims it for this very meshfile —
    # Wolfsnow's config names `character_neutrals_wolf.g` — or when the
    # folder has just the one `.g` (nothing else to mis-assign it to).
    # Without the claim the Cyclop's animation would animate its rock.
    meshes = sorted(glob.glob(os.path.join(folder, "*.g")))
    if len(meshes) == 1:
        return animations[0]
    me = {stem.lower() + ".g", main_stem.lower() + ".g"}
    claimed: set = set()
    for ac_path in glob.glob(os.path.join(folder, "*.ac")):
        try:
            cfg = acmod.parse_ac(open(ac_path, "r", encoding="utf-8-sig",
                                      errors="replace").read())
        except Exception:  # noqa: BLE001 - a claim scan must never break
            continue
        names = {os.path.basename(s.file.replace("\\", "/").rsplit("/", 1)[-1])
                 .lower() for s in cfg.states if s.file}
        if os.path.basename(animations[0]).lower() not in names:
            continue
        for s in cfg.states:
            if s.meshfile:
                claimed.add(os.path.basename(
                    s.meshfile.replace("\\", "/").rsplit("/", 1)[-1]).lower())
    return animations[0] if claimed & me else None


def _export_all(folder: str, out_dir: str, use_anim: bool = True) -> int:
    """Recursively convert every `.g` below *folder* to glTF."""
    root = os.path.abspath(folder)
    if not os.path.isdir(root):
        ui.section("Recursive export  Disciples 3 → glTF")
        ui.fail(f"no such folder: {folder}")
        return 1

    g_files: list = []
    for current, dirs, files in os.walk(root):
        # Do not accidentally consume a previous export when output is inside
        # the source tree.
        dirs[:] = [d for d in dirs
                   if os.path.abspath(os.path.join(current, d)) !=
                   os.path.abspath(out_dir)]
        g_files.extend(os.path.join(current, f) for f in files
                       if f.lower().endswith(".g"))
    g_files.sort()

    ui.section("Recursive export  Disciples 3 → glTF")
    if not g_files:
        ui.fail(f"no .g files found in {folder}")
        return 1

    succeeded = failed = 0
    for g_path in g_files:
        relative = os.path.relpath(g_path, root)
        target = os.path.join(out_dir, os.path.splitext(relative)[0] + ".gltf")
        animation = _find_animation_for_geometry(g_path) if use_anim else None
        try:
            _export_gl(g_path, animation, target, texture=None, quiet=True)
            detail = os.path.relpath(target, os.getcwd())
            if animation:
                detail += f"  + {os.path.basename(animation)}"
            elif use_anim:
                # the unit's .ac names a stream its folder does not have
                detail += "  + no animation (rigid, like the dis3tool export)"
            ui.ok(detail)
            succeeded += 1
        except Exception as exc:  # noqa: BLE001
            ui.fail(f"{relative}: {exc}")
            failed += 1

    print("")
    ui.info(f"exported {succeeded}/{len(g_files)} models to {out_dir}")
    return 1 if failed else 0


# --------------------------------------------------------------------------- #
#  bundle: full pipeline over a unit folder
# --------------------------------------------------------------------------- #
def _bundle(folder: str, out_dir: str, weights_on_vertex: int) -> int:
    """Run the full glTF↔GM pipeline over every `.gltf` in *folder*.

    Returns 0 when every unit round-tripped, 1 otherwise (so scripts can tell).
    """
    gltf_files = sorted(glob.glob(os.path.join(folder, "*.gltf")))
    if not gltf_files:
        ui.fail(f"no .gltf found in {folder}")
        return 1

    ui.section(f"Bundle {folder}")
    failed = 0
    for gltf in gltf_files:
        ui.info(f"processing {os.path.basename(gltf)}")
        base = os.path.splitext(os.path.basename(gltf))[0]
        d = os.path.join(out_dir, base)
        try:
            os.makedirs(d, exist_ok=True)
            _export(gltf, d, weights_on_vertex, quiet=True)
        except Exception as exc:  # noqa: BLE001 - one bad unit must not abort
            # the batch: report it against its own name and carry on, the way
            # _export_all does for forward exports.
            ui.fail(f"{base}: reverse export failed — {exc}")
            failed += 1
            continue
        # forward round-trip back
        g_path = os.path.join(d, base + ".g")
        # the reverse export names the rebuilt `.a` after the unit's own
        # `.ac` entry, which is not always `<base>_iadd.a`
        rebuilt = sorted(glob.glob(os.path.join(d, "*.a")))
        a_path = rebuilt[0] if rebuilt else None
        fwd_dir = os.path.join(d, "gltf")
        if not os.path.exists(g_path):
            ui.fail(f"{base}: .g was not produced")
            failed += 1
            continue
        try:
            _export_gl(g_path, a_path,
                       os.path.join(fwd_dir, "roundtrip.gltf"),
                       texture=None, quiet=True)
            ui.ok(f"{base} → glTF round-trip wrote gltf/roundtrip.gltf")
        except Exception as exc:  # noqa: BLE001
            ui.fail(f"{base}: forward export failed — {exc}")
            failed += 1

    print("")
    ui.info(f"bundle written to {out_dir}"
            + (f"  ({failed} failed)" if failed else ""))
    return 1 if failed else 0


# --------------------------------------------------------------------------- #
#  texture conversion
# --------------------------------------------------------------------------- #
def _format_label(info) -> str:
    """Human-readable pixel-format name for a ``.t`` / ``.dds`` container.

    Only the block-compressed forms carry a DDS fourCC; the uncompressed GM
    codes (1/2 = 16-bit, 3 = A1R5G5B5, 4/5 = 32-bit) have none, so the label
    is derived from the GM code instead of dereferencing a ``None`` fourCC.
    """
    # `fourcc` is `data[84:88]`, which is four NUL bytes (truthy!) for the
    # uncompressed RGB forms, so test for printable ASCII, not truthiness.
    if info.fourcc and all(32 <= c < 127 for c in info.fourcc):
        return info.fourcc.decode("latin1")
    if info.r5g5b5 or info.gm_format == 3:
        return "16-bit A1R5G5B5"
    if info.gm_format in (1, 2):
        return "16-bit uncompressed"
    if info.gm_format in (4, 5):
        return "32-bit A8R8G8B8"
    return f"GM code {info.gm_format}"


def _vdpack(args) -> int:
    """`vdpack list|extract|pack` — the one-file form of a unit bundle."""
    if args.vd_cmd == "list":
        pack = vdmod.read_vdpack(args.archive)
        ui.section(f"vdpack {os.path.basename(args.archive)}")
        if pack.parse_error:
            ui.fail(pack.parse_error)
            return 1
        ui.table([(e.name, f"{e.size}", f"{e.flags}") for e in pack.entries],
                 headers=("file", "bytes", "flags"))
        summary = pack.summary()
        ui.info(f"{summary['entries']} file(s), "
                f"{summary['bytes']} bytes, index {summary['index_bytes']}, "
                f"types {summary['types']}")
        return 0

    if args.vd_cmd == "extract":
        pack = vdmod.read_vdpack(args.archive)
        ui.section(f"vdpack {os.path.basename(args.archive)}")
        if pack.parse_error:
            ui.fail(pack.parse_error)
            return 1
        dest = args.out or os.path.splitext(os.path.basename(args.archive))[0]
        try:
            written = vdmod.extract_vdpack(pack, dest, overwrite=args.force)
        except FileExistsError as exc:
            ui.fail(f"refusing to overwrite {exc}; pass -f to replace it")
            return 1
        for path in written:
            ui.wrote(os.path.relpath(path, dest))
        return 0

    if args.vd_cmd == "manifest":
        if os.path.isdir(args.target):
            files = vdmod.pack_folder(args.target)
            label = os.path.basename(os.path.normpath(args.target))
        else:
            pack = vdmod.read_vdpack(args.target)
            if pack.parse_error:
                ui.section("vdpack manifest")
                ui.fail(f"{pack.parse_error}")
                return 1
            files = list(pack.files().items())
            label = os.path.basename(args.target)
        doc = vdmod.manifest(files, label)
        text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
        if args.out:
            with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            ui.section(f"vdpack manifest {label}")
            ui.wrote(args.out, f"{doc['files']} file(s), {doc['bytes']} bytes")
        else:
            sys.stdout.write(text)
        problems = doc["references"]["problems"]
        if problems:
            ui.warn(f"{len(problems)} reference(s) would not resolve inside "
                    f"the bundle (see `vdpack validate`)")
            return 1
        return 0

    if args.vd_cmd == "validate":
        if os.path.isdir(args.target):
            files = dict(vdmod.pack_folder(args.target))
            label = os.path.basename(os.path.normpath(args.target))
        else:
            pack = vdmod.read_vdpack(args.target)
            if pack.parse_error:
                ui.section("vdpack validate")
                ui.fail(f"{pack.parse_error}")
                return 1
            files = pack.files()
            label = os.path.basename(args.target)
        ui.section(f"vdpack validate {label}")
        report = vdmod.reference_findings(files)
        for line in report.notes:
            ui.info("outside the pack: " + line)
        for line in report.warnings:
            ui.warn("  " + line)
        if report.warnings and not report:
            ui.warn(f"{len(files)} file(s): every reference resolves, but "
                    f"{len(report.warnings)} reference path(s) point at the "
                    f"wrong folder — the pack can load in a viewer and fail "
                    f"in the game")
            return 1 if args.strict else 0
        if not report:
            ui.ok(f"{len(files)} file(s), every reference resolves inside")
            return 0
        ui.warn("the pack would not load on its own:")
        for line in report.problems:
            ui.warn("  " + line)
        return 1

    # pack
    if not os.path.isdir(args.folder):
        ui.fail(f"not a folder: {args.folder}")
        return 1
    files = vdmod.pack_folder(args.folder)
    if not files:
        ui.fail(f"{args.folder} has no files to pack")
        return 1
    if args.like:
        source = vdmod.read_vdpack(args.like)
        if source.parse_error:
            ui.fail(f"{args.like}: {source.parse_error}")
            return 1
        blob, extra = vdmod.pack_like(source, files, drop=args.drop)
        for name in extra:
            ui.warn(f"{name}: not in {os.path.basename(args.like)}, dropped — "
                    "a pack that adds paths is not the confirmed shape")
        out = args.out or (os.path.basename(os.path.normpath(args.folder)) +
                           ".vdpack")
        check = vdmod.parse_vdpack(blob)
        if check.parse_error or not len(check):
            ui.fail(f"the archive would not read back: {check.parse_error}")
            return 1
        with open(out, "wb") as fh:
            fh.write(blob)
        changed = []
        for entry in source.entries:
            landed = check.find(entry.name)
            if landed is None:                  # dropped, reported below
                continue
            before = source.payload(entry)
            after = check.payload(landed)
            if before != after:
                changed.append((entry.name, len(before), len(after)))
        ui.section(f"vdpack {os.path.basename(out)}")
        ui.info(f"{len(check)} file(s) in {os.path.basename(args.like)}'s "
                f"order, {len(blob)} bytes")
        for name in args.drop:
            ui.info(f"dropped: {name}")
        for name, before, after in changed:
            ui.ok(f"{name.split(chr(92))[-1]}: {before} -> {after} bytes — "
                  "everything else carried over byte for byte")
        ui.wrote(out, f"{len(blob)} bytes")
        return 0
    if args.keep_index:
        source = vdmod.read_vdpack(args.keep_index)
        if source.parse_error:
            ui.fail(f"{args.keep_index}: {source.parse_error}")
            return 1
        taken = vdmod.index_entry(list(source.files().items()))
        if taken is None:
            ui.fail(f"{args.keep_index} has no `index` entry to keep")
            return 1
        if vdmod.index_entry(files) is not None:
            ui.fail(f"{args.folder} already carries an `index` entry")
            return 1
        files = files + [taken]
    out = args.out or (os.path.basename(os.path.normpath(args.folder)) +
                       ".vdpack")
    blob = vdmod.build_vdpack(files)
    # Every pack the Heroes-VII port examined carries one opaque `index`
    # resource; same-path replacement inside such a pack is game-confirmed,
    # a pack that added paths is not.  Say so instead of implying that any
    # archive this tool writes will be mounted.
    if vdmod.index_entry(files) is None:
        ui.warn("no opaque `index` entry: retail and ported packs carry one, "
                "and only in-place replacement inside such a pack has been "
                "confirmed to load — pass `--keep-index <existing.vdpack>` "
                "to build on that proven shape")
    # never ship an archive this toolkit cannot read back
    check = vdmod.parse_vdpack(blob)
    if check.parse_error or len(check) != len(files):
        ui.fail(f"the archive would not read back: {check.parse_error}")
        return 1
    with open(out, "wb") as fh:
        fh.write(blob)
    ui.section(f"vdpack {os.path.basename(out)}")
    ui.table([(name, f"{len(data)}") for name, data in files[:20]],
             headers=("file", "bytes"))
    if len(files) > 20:
        ui.info(f"... and {len(files) - 20} more")
    ui.wrote(out, f"{len(files)} file(s), {len(blob)} bytes")
    # the same reference audit `vdpack validate` runs, on what was just written:
    # a pack whose `.ac` names a folder it does not carry loads in a viewer and
    # fails in the game, so say it here rather than after the crash
    audit = vdmod.reference_findings(dict(files))
    for line in audit.warnings:
        ui.warn("  " + line)
    if audit.problems:
        ui.warn(f"{len(audit.problems)} reference(s) would not resolve inside "
                f"the pack (see `vdpack validate`)")
    return 0


def _texture_convert(src: str, dst: str, quiet: bool = False) -> None:
    """``d3tool texture convert <in> -o <out>`` — .t <-> .dds."""
    if not quiet:
        ui.section("Texture convert")
    info = texmod.convert_file(src, dst)
    if not quiet:
        ui.wrote(dst, f"{info.width}x{info.height} {_format_label(info)}"
                      f" mips={info.mip_count}")


def _texture_info(path: str) -> None:
    """``d3tool texture info <file>`` — describe a .t or .dds texture."""
    ui.section(f"Texture  {path}")
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] == b"DDS ":
        info = texmod.parse_dds(data, os.path.basename(path))
    else:
        info = texmod.parse_t(data, os.path.basename(path))
    ui.table([
        ("format", _format_label(info)),
        ("GM code", str(info.gm_format)),
        ("width", str(info.width)),
        ("height", str(info.height)),
        ("mipmap levels", str(info.mip_count)),
        ("cubemap faces", str(info.faces)),
        ("payload", f"{len(info.payload)} bytes"),
    ], headers=["field", "value"])


# --------------------------------------------------------------------------- #
#  argparse
# --------------------------------------------------------------------------- #
# The GM vertex record only exists for 2/3/4 influence slots (46/51/56 bytes).
# 0 means auto-detect.  1 is deliberately *not* offered: the writer would use
# the 40-byte rigid stride while the reader's "w=1 bound to several bones"
# rule re-reads the same bytes as the 46-byte w=2 stride, so the file could
# not be parsed back (verified on Wildboar: 2350 vertices -> 0).
_WOV_CHOICES = (0, 2, 3, 4)


def _weights_on_vertex(value: str) -> int:
    """argparse type for ``--weights-on-vertex``."""
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected an integer, got {value!r}") from None
    if n not in _WOV_CHOICES:
        raise argparse.ArgumentTypeError(
            f"{n} is not a valid influence-slot count; use one of "
            f"{', '.join(str(c) for c in _WOV_CHOICES)} "
            "(0 = auto-detect from the source glTF)")
    return n


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="d3tool",
        description="Disciples 3 / dis3tool reverse-engineering toolkit — "
                    "glTF & the original GM (.g/.a/.scene/.ac) formats.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            the two everyday commands:
              d3tool import  unit.g    -o unit.gltf   # game → glTF (edit it anywhere)
              d3tool export  unit.gltf -o unit        # glTF → game (.g/.scene/.ac/.a/.t)

            more:
              d3tool analyze Neutrals/AirElemental
              d3tool export-all Neutrals -o gltf
              d3tool bundle Neutrals/AirElemental -o bundle
              d3tool audit   Empire/Inquisitor --against <retail>   # what the loader reads
              d3tool lod out/     # LOD mesh takes the parts the main one gained
        """),
    )
    parser.add_argument("--version", action="version",
                        version=f"d3tool {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_analyze = sub.add_parser("analyze", help="inspect a unit folder")
    p_analyze.add_argument("path")

    # `import` brings a game model INTO the editing pipeline (glTF); the
    # old `export-gl` is kept as an alias of it
    def _add_gm_source_args(p):
        p.add_argument("g")
        p.add_argument("-a", "--anim", default=None,
                       help=".a animation file (default: auto-detect next "
                            "to the .g via the unit's .ac)")
        p.add_argument("-o", "--out", default=None,
                       help="output glTF path (default: <base>.gltf)")
        p.add_argument("-t", "--texture", default=None)
        p.add_argument("--no-anim", action="store_true",
                       help="do not attach an animation (rigid model)")

    p_import = sub.add_parser(
        "import",
        help="IMPORT a game model for editing: GM (.g/.a) → glTF "
             "(.gltf/.bin); animation auto-detected via the .ac")
    _add_gm_source_args(p_import)
    p_g2gl = sub.add_parser("export-gl",
                            help="alias of `import` (GM → glTF)")
    _add_gm_source_args(p_g2gl)

    p_export = sub.add_parser(
        "export",
        help="EXPORT an edited model back INTO the game: glTF → GM "
             "(.g/.scene/.ac/.a/.t + Aliases)")
    p_export.add_argument("gltf")
    p_export.add_argument("-o", "--out", default=".",
                          help="output folder (default: current)")
    p_export.add_argument("--weights-on-vertex", type=_weights_on_vertex,
                          default=0, metavar="N",
                          help="influence slots to write (2/3/4; 0=auto-detect)")
    p_export.add_argument("--no-anim", action="store_true",
                          help="do not rebuild the .a animation file")

    p_all = sub.add_parser(
        "export-all", help="recursively convert every .g file to glTF")
    p_all.add_argument("folder", help="folder to scan recursively")
    p_all.add_argument("-o", "--out", default="gltf",
                       help="output folder (default: gltf)")
    p_all.add_argument("--no-anim", action="store_true",
                       help="export models without auto-detected .a animations")

    p_bundle = sub.add_parser(
        "bundle", help="run the full glTF↔GM pipeline over a unit folder")
    p_bundle.add_argument("folder")
    p_bundle.add_argument("-o", "--out", default="bundle")
    p_bundle.add_argument("--weights-on-vertex", type=_weights_on_vertex,
                          default=0, metavar="N",
                          help="influence slots to write (2/3/4; 0=auto-detect)")

    p_validate = sub.add_parser("validate", help="structural glTF self-check")
    p_validate.add_argument("gltf")

    p_w = sub.add_parser(
        "weights", help="inspect (or diff) the skin weights of a .g")
    p_w.add_argument("file")
    p_w.add_argument("--against", metavar="OTHER",
                     help="compare with another .g (e.g. the game's donor)")
    p_w.add_argument("--lot", type=int, default=10, metavar="N",
                     help="how many changed vertices to list (default 10)")
    p_w.add_argument("--json", action="store_true", help="machine output")
    p_rep = sub.add_parser(
        "repair",
        help="fix a .g whose headers advertise stale vertex/triangle counts")
    p_rep.add_argument("file")
    p_rep.add_argument("-i", "--in-place", action="store_true",
                       help="rewrite the file itself (default: report only)")
    p_rep.add_argument("-o", "--out", help="write the fixed file here")
    p_dump = sub.add_parser("dump", help="dump a parsed .g as JSON")
    p_dump.add_argument("gfile")

    p_texture = sub.add_parser(
        "texture", help="inspect / convert .t (GM) <-> .dds textures")
    p_texture_sub = p_texture.add_subparsers(dest="tex_cmd", required=True)
    p_tc = p_texture_sub.add_parser(
        "convert", help="convert a .t to .dds (or .dds to .t)")
    p_tc.add_argument("src")
    p_tc.add_argument("-o", "--out", help="output path (decides direction)")
    p_ti = p_texture_sub.add_parser(
        "info", help="describe a .t / .dds texture")
    p_ti.add_argument("file")

    p_vd = sub.add_parser(
        "vdpack", help="list / extract / pack a .vdpack mod archive")
    p_vd_sub = p_vd.add_subparsers(dest="vd_cmd", required=True)
    p_vl = p_vd_sub.add_parser("list", help="list the files in an archive")
    p_vl.add_argument("archive")
    p_ve = p_vd_sub.add_parser(
        "extract", help="write every entry under a folder")
    p_ve.add_argument("archive")
    p_ve.add_argument("-o", "--out",
                      help="output folder (default: the archive name)")
    p_ve.add_argument("-f", "--force", action="store_true",
                      help="overwrite existing files")
    p_vv = p_vd_sub.add_parser(
        "validate", help="check that every internal reference is inside")
    p_vv.add_argument("target", help="a .vdpack file or a unit folder")
    p_vv.add_argument("--strict", action="store_true",
                      help="exit 1 on warnings as well (a reference whose "
                           "folder is not in the pack while the file it names "
                           "is)")
    p_vm = p_vd_sub.add_parser(
        "manifest", help="sha256 inventory of a folder or an archive")
    p_vm.add_argument("target", help="a .vdpack file or a unit folder")
    p_vm.add_argument("-o", "--out", help="write JSON here instead of stdout")
    p_ch = sub.add_parser(
        "char", help="read / relink a .char unit profile")
    p_ch_sub = p_ch.add_subparsers(dest="char_cmd", required=True)
    p_cs = p_ch_sub.add_parser("show", help="print what a profile declares")
    p_cs.add_argument("file")
    p_cs.add_argument("--json", action="store_true", help="machine output")
    p_cl = p_ch_sub.add_parser("list", help="every profile and its scene")
    p_cl.add_argument("--profiles", help="folder of .char files "
                                         "(default: ./Profiles)")
    p_cl.add_argument("--root", help="resolve unit_scene under this folder")
    p_cl.add_argument("--json", action="store_true", help="machine output")
    p_cf = p_ch_sub.add_parser(
        "find", help="find the profile of a scene, a unit folder or an id")
    p_cf.add_argument("target", help=".scene path, unit folder, or profile id")
    p_cf.add_argument("--profiles", help="folder of .char files "
                                         "(default: ./Profiles)")
    p_cv = p_ch_sub.add_parser(
        "validate", help="check that every reference a profile makes exists")
    p_cv.add_argument("target", help="a .char file or a folder of them")
    p_cv.add_argument("--root", help="folder to resolve references against "
                                     "(default: the repository root)")
    p_cv.add_argument("--json", action="store_true", help="machine output")
    p_ci = p_ch_sub.add_parser(
        "set-id", help="rename a profile's `id` (how the port adds a unit)")
    p_ci.add_argument("file")
    p_ci.add_argument("new_id")
    p_ci.add_argument("--scene", help="also point unit_scene at this scene")
    p_ci.add_argument("-o", "--out", help="write here instead of in place")

    p_lod = sub.add_parser(
        "lod", help="give a unit's LOD mesh the parts its main mesh gained")
    p_lod.add_argument("unit_dir", help="folder holding <stem>.g and "
                                        "<stem>_lod.g")
    p_lod.add_argument("-o", "--out", default=None,
                       help="write the patched files here instead of in place")

    p_au = sub.add_parser(
        "audit",
        help="cross-check a unit folder the way the engine loads it",
        description="Read the .g, the _lod.g, both .scene files and the .ac "
                    "of a unit and report what disagrees: a gobj pointing past "
                    "the mesh, a gobj whose triangle/vertex/bone counts belong "
                    "to the mesh it replaced, a part the scene still draws with "
                    "another atlas, an atlas that is not in the folder. The "
                    "same checks the loader makes, before the game makes them.",
    )
    p_au.add_argument("unit_dir", help="folder holding the unit's bundle")
    p_au.add_argument("--against", metavar="REF", default=None,
                      help="compare with a known-good bundle (e.g. the retail "
                           "folder) and list what changed")
    p_ga = sub.add_parser(
        "gattr", help="insert or replace one attribute in a .g part")
    p_ga.add_argument("file", help="the .g to edit")
    p_ga.add_argument("--part", type=int, action="append", default=[],
                      metavar="N",
                      help="part index, in file order (repeatable; default: "
                           "every part)")
    p_ga.add_argument("--insert", action="append", default=[],
                      metavar="KEY=VALUE",
                      help="add an attribute the part does not carry")
    p_ga.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                      help="rewrite an attribute the part already carries")
    p_ga.add_argument("-o", "--out", help="write here instead of in place")

    p_vp = p_vd_sub.add_parser("pack", help="pack a folder into an archive")
    p_vp.add_argument("folder")
    p_vp.add_argument("-o", "--out",
                      help="output .vdpack (default: <folder>.vdpack)")
    p_vp.add_argument(
        "--drop", action="append", default=[], metavar="PATH",
        help="with --like: leave this entry out of the new archive (repeatable) "
             "— for a file the engine requests and cannot resolve, which stops "
             "the load with a missing-file dialog")
    p_vp.add_argument(
        "--like", metavar="ARCHIVE",
        help="rebuild in an existing pack's exact shape: same entry order, "
             "names, flags and `index`, only the payloads from the folder — "
             "the one form replacement that is confirmed to load in game")
    p_vp.add_argument(
        "--keep-index", metavar="ARCHIVE",
        help="copy the opaque `index` resource of an existing pack into the "
             "new one (the only game-confirmed pack shape)")

    return parser


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "analyze":
        return _analyze_unit(args.path)
    elif args.cmd in ("import", "export", "export-gl"):
        # pipeline semantics: import = game → glTF (edit), export =
        # glTF → game (ship back).  Legacy calls route by extension so
        # old scripts keep working either way.
        source = str(getattr(args, "g", None)
                     or getattr(args, "gltf") or "")
        cmd = "import" if args.cmd in ("import", "export-gl") else "export"
        if cmd == "export" and source.lower().endswith(".g"):
            ui.section("note")
            ui.info("`export` is glTF → GM; routing the .g input to "
                    "`import` (GM → glTF)")
            cmd = "import"
        elif cmd == "import" and source.lower().endswith((".gltf", ".glb")):
            ui.section("note")
            ui.info("`import` is GM → glTF; routing the .gltf input to "
                    "`export` (glTF → GM)")
            cmd = "export"
        if cmd == "import":
            # a routed call may come from the other parser: probe the attrs
            anim = getattr(args, "anim", None)
            if anim is None and not getattr(args, "no_anim", False):
                anim = _find_animation_for_geometry(source)
                if anim:
                    ui.info(f"animation: {os.path.basename(anim)}")
            _export_gl(source, anim, args.out,
                       getattr(args, "texture", None))
        else:
            _export(source, args.out,
                    getattr(args, "weights_on_vertex", 0),
                    anim=not getattr(args, "no_anim", False))
    elif args.cmd == "export-all":
        return _export_all(args.folder, args.out, use_anim=not args.no_anim)
    elif args.cmd == "bundle":
        return _bundle(args.folder, args.out, args.weights_on_vertex)
    elif args.cmd == "validate":
        if not os.path.exists(args.gltf):
            ui.section("glTF structure check")
            ui.fail(f"no such file: {args.gltf}")
            return 1
        try:
            errors, warnings, infos = gltfout.validate_gltf(args.gltf)
        except (ValueError, json.JSONDecodeError, OSError) as exc:
            ui.section("glTF structure check")
            ui.fail(f"could not parse {args.gltf}: {exc}")
            return 1
        ui.section("glTF structure check")
        ui.table([("errors", str(errors)), ("warnings", str(warnings)),
                  ("accessors", str(infos))])
        return 1 if errors else 0
    elif args.cmd == "weights":
        return _weights(args)
    elif args.cmd == "repair":
        return _repair(args)
    elif args.cmd == "dump":
        data = open(args.gfile, "rb").read()
        mesh = gfile.parse_geometry_file(data)
        if mesh.parse_error:
            # Same rule as `analyze`: an unparseable `.g` is an error, not an
            # empty mesh.  Printing a zeroed document with exit 0 hands a
            # script a result it cannot distinguish from a real empty file.
            ui.fail(f"{os.path.basename(args.gfile)}: unparsed "
                    f"({mesh.parse_error})")
            return 1
        print(json.dumps({
            "name": mesh.name,
            "geometry_file": mesh.geometry_file,
            "vertex_count": mesh.vertex_count,
            "tri_count": mesh.tri_count,
            "bones": [b.name for b in mesh.bones],
        }, indent=2))
    elif args.cmd == "texture":
        if args.tex_cmd == "convert":
            if not args.out:
                ui.fail("texture convert requires -o/--out")
                return 1
            _texture_convert(args.src, args.out)
        elif args.tex_cmd == "info":
            _texture_info(args.file)
    elif args.cmd == "vdpack":
        return _vdpack(args)
    elif args.cmd == "char":
        return _char(args)
    elif args.cmd == "lod":
        return _lod_sync(args.unit_dir, args.out)
    elif args.cmd == "audit":
        return _audit_cmd(args)
    elif args.cmd == "gattr":
        return _gattr(args)
    return 0


def _gattr(args) -> int:
    """`gattr <g>` — insert or replace one attribute in a `.g` part.

    The whole reason this exists: a `.g` part that a tool wrote without the
    attributes the engine reads its vertex block by is drawn as nothing at
    all, while every reader that takes the layout from ``weights_on_vertex``
    (this package included) sees a perfectly good mesh.  The Angel's appended
    sword part was exactly that — no ``new_vertex_weights_format`` — and the
    game never drew it.
    """
    ui.section("g attribute")
    if not os.path.isfile(args.file):
        ui.fail(f"no such file: {args.file}")
        return 1
    wanted = []
    for spec in list(args.insert) + list(args.set):
        if "=" not in spec:
            ui.fail(f"expected KEY=VALUE, got {spec!r}")
            return 1
        key, _, value = spec.partition("=")
        wanted.append((key, value))
    if not wanted:
        ui.fail("nothing to do: pass --insert and/or --set")
        return 1

    data = open(args.file, "rb").read()
    mesh = gfile.parse_geometry_file(data)
    if mesh.parse_error:
        ui.fail(f"{os.path.basename(args.file)}: unparsed ({mesh.parse_error})")
        return 1
    parts = gfile.iter_parts(mesh)
    indexes = args.part or list(range(len(parts)))
    for index in indexes:
        if not 0 <= index < len(parts):
            ui.fail(f"no part {index}; the file has {len(parts)}")
            return 1

    before = len(data)
    changed = 0
    for index in indexes:
        part = parts[index]
        for key, value in wanted:
            carries = key in dict(part.attr_items or ())
            if key in args.set or carries and key not in args.insert:
                if not carries:
                    ui.warn(f"part {index} ({part.name}): no {key!r} to set")
                    continue
                data = gfile.replace_part_attr(data, part, key, value)
            else:
                if carries:
                    ui.info(f"part {index} ({part.name}): already has {key!r}")
                    continue
                data = gfile.insert_part_attr(data, part, key, value)
            ui.ok(f"part {index} ({part.name}): {key} = {value}")
            changed += 1
            # re-parse: offsets moved, later parts are at new positions
            mesh = gfile.parse_geometry_file(data)
            parts = gfile.iter_parts(mesh)
    if not changed:
        return 0
    target = args.out or args.file
    with open(target, "wb") as fh:
        fh.write(data)
    ui.wrote(target, f"{len(data) - before:+d} bytes")
    check = gfile.parse_geometry_file(data)
    if check.parse_error:
        ui.fail(f"the result no longer parses: {check.parse_error}")
        return 1
    return 0


def _lod_sync(unit_dir: str, out_dir: Optional[str] = None,
              quiet: bool = False) -> int:
    """`lod <unit folder>` — make a unit's LOD model carry its added parts.

    The engine swaps to `unit_scene_lod` (marked in the `.char` profile with a
    draw distance, `10` for nearly every unit) as soon as a squad is further
    away than that, and the LOD `.g` is a separate, lower-poly mesh.  A part
    added to the main `.g` — a weapon — is therefore invisible on the
    battlefield until it is appended to the LOD mesh and the LOD scene has the
    `gobj` for it.

    The appended block is re-shaped for the file it lands in: renamed
    ``lod_<name>`` the way every LOD block is spelled, and re-pointed at the
    LOD file's own parent node (`gfile.lod_part`).  Running it again changes
    nothing — the match ignores the ``lod_`` prefix, so a synced unit is not
    given the part twice.
    """
    if not quiet:
        ui.section("LOD sync")
    if not os.path.isdir(unit_dir):
        ui.fail(f"no such folder: {unit_dir}")
        return 1
    stems = [os.path.join(unit_dir, fn[:-2])
             for fn in sorted(os.listdir(unit_dir))
             if fn.endswith(".g") and not fn[:-2].endswith("_lod")]
    if not stems:
        ui.fail(f"no .g in {unit_dir}")
        return 1
    changed = 0
    for stem in stems:
        lod_g = stem + "_lod.g"
        if not os.path.isfile(lod_g):
            ui.info(f"{os.path.basename(stem)}: no LOD model, nothing to do")
            continue
        main_parts = gfile.parse_geometry_file(open(stem + ".g", "rb").read())
        lod_bytes = open(lod_g, "rb").read()
        lod_mesh = gfile.parse_geometry_file(lod_bytes)
        lod_parts = gfile.iter_parts(lod_mesh)
        main_all = gfile.iter_parts(main_parts)
        # the LOD mesh is the same unit at lower detail: its parts are the
        # main mesh's, in the same order (and spelled `lod_<name>`, so a name
        # is no guide) — whatever trails them was added after the fact
        names = {p.name for p in lod_parts}
        names |= {n[4:] if n.startswith("lod_") else n for n in names}
        missing = [p for p in main_all[len(lod_parts):] if p.name not in names]
        if missing:
            parent = ((lod_parts[0].attrs or {}).get("dwParent")
                      if lod_parts else None)
            appended = [gfile.lod_part(p, parent) for p in missing]
            lod_bytes = gfile.append_parts(lod_bytes, appended)
            lod_parts = list(gfile.iter_parts(
                gfile.parse_geometry_file(lod_bytes)))
            target = (os.path.join(out_dir, os.path.basename(lod_g))
                      if out_dir else lod_g)
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            open(target, "wb").write(lod_bytes)
            ui.ok(f"{os.path.basename(lod_g)}: +{len(appended)} part(s) — "
                  + ", ".join(p.name for p in appended))
            changed += 1
        elif not quiet:
            ui.info(f"{os.path.basename(lod_g)}: already carries every part")
        # A part the export REPLACED instead of adding — the unit's mace slot
        # now holding a sword from another model — is already present by name,
        # so nothing is appended and the battlefield keeps drawing the old
        # weapon.  Only rigid single-bone parts are swapped: a skinned part is
        # lower-poly in the LOD on purpose and must stay as it is.
        main_by = {p.name: p for p in main_all}
        swaps = []
        for i, lp in enumerate(lod_parts):
            base_name = lp.name[4:] if lp.name.startswith("lod_") else lp.name
            mp = main_by.get(base_name)
            if mp is None or len(lp.bones or ()) != 1 or len(mp.bones or ()) != 1:
                continue
            # The LOD's own copy of a part is the SAME shape at lower detail,
            # so its bounding box survives decimation.  A part the export
            # replaced comes from another model and therefore another shape —
            # and it does not have to bring another *texture*: a sword dropped
            # into the mace's slot is exported under the mace's material name
            # (`weapon_empire_inquisitor.tga` comes from the part, not from the
            # file it was modelled in), so comparing materials missed exactly
            # the case this branch exists for.
            lod_dims, main_dims = partsmod.dimensions(lp), partsmod.dimensions(mp)
            if max(main_dims) > 0 and max(lod_dims) > 0 and all(
                    abs(lod_dims[k] - main_dims[k]) <= 0.10 * max(
                        main_dims[k], 1e-6) for k in range(3)):
                continue
            swaps.append((i, lp, mp))
        # A part the export only re-textured (same shape, another atlas) never
        # reaches `swaps` — the geometry is identical, so the LOD keeps the
        # mace's material name and the weapon changes texture the moment the
        # camera pulls back.  A material is a name, not geometry: copy it.
        retags = []
        for i, lp in enumerate(lod_parts):
            base_name = lp.name[4:] if lp.name.startswith("lod_") else lp.name
            mp = main_by.get(base_name)
            if mp is None:
                continue
            want = (mp.attrs or {}).get("material0_diffuse")
            have = (lp.attrs or {}).get("material0_diffuse")
            if want and have and (os.path.splitext(want)[0].lower()
                                  != os.path.splitext(have)[0].lower()):
                retags.append((i, lp, want))
        if swaps or retags:
            import copy as _copy
            prefix = lod_bytes[:len(lod_bytes)
                               - sum(len(p.raw) for p in lod_parts)]
            raws = [p.raw for p in lod_parts]
            for i, _lp, want in retags:
                clone = _copy.copy(_lp)
                items = [(k, want if k == "material0_diffuse" else v)
                         for k, v in (clone.attr_items or ())]
                clone.attr_items = items
                clone.attrs = dict(items)
                clone.raw = gfile._part_block(clone)
                raws[i] = clone.raw
            for i, lp, mp in swaps:
                clone = _copy.copy(lp)
                clone.vertices = list(mp.vertices)
                clone.indices = list(mp.indices)
                tex = (mp.attrs or {}).get("material0_diffuse")
                items = [(k, tex if k == "material0_diffuse" else v)
                         for k, v in (clone.attr_items or ())]
                clone.attr_items = items
                clone.attrs = dict(items)
                clone.raw = gfile._part_block(clone)
                raws[i] = clone.raw
            lod_bytes = prefix + b"".join(raws)
            lod_parts = list(gfile.iter_parts(
                gfile.parse_geometry_file(lod_bytes)))
            target = (os.path.join(out_dir, os.path.basename(lod_g))
                      if out_dir else lod_g)
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            open(target, "wb").write(lod_bytes)
            notes = [f"{lp.name} <- {mp.name}" for _i, lp, mp in swaps]
            notes += [f"{lp.name}: {want}" for _i, lp, want in retags
                      if _i not in {i for i, _l, _m in swaps}]
            ui.ok(f"{os.path.basename(lod_g)}: " + ", ".join(notes)
                  + (" (the export swapped the part, it was not added)"
                     if swaps else " (the part's atlas followed the main mesh)"))
            changed += 1
        lod_scene = stem + "_lod.scene"
        if not os.path.isfile(lod_scene):
            continue
        # newline="" keeps the file's own terminators: a scene read with
        # universal newlines loses every \r and the whole payload changes
        # even though one `gobj` was added
        with open(lod_scene, encoding="latin-1", newline="") as fh:
            text = fh.read()
        new_text, added = scenemod.ensure_part_gobjs(text, lod_parts)
        restated: List[str] = []
        try:
            # a `_lod.scene` repeats the numbers of the MAIN mesh, not of the
            # LOD `.g` it draws: the retail Inquisitor's LOD gobj says 4664
            # triangles where `_lod.g` holds 1716, and every shipped unit does
            # this — restating from the LOD mesh wrote numbers no retail file
            # has, and left the gobj on the old atlas
            new_text, restated = scenemod.refresh_part_gobjs(new_text, main_all)
        except Exception:  # noqa: BLE001 - cosmetic, never fatal
            restated = []
        if restated and not added:
            target = (os.path.join(out_dir, os.path.basename(lod_scene))
                      if out_dir else lod_scene)
            open(target, "w", encoding="latin-1", newline="").write(new_text)
            ui.ok(f"{os.path.basename(lod_scene)}: restated "
                  + ", ".join(restated))
            changed += 1
        if added:
            target = (os.path.join(out_dir, os.path.basename(lod_scene))
                      if out_dir else lod_scene)
            open(target, "w", encoding="latin-1", newline="").write(new_text)
            ui.ok(f"{os.path.basename(lod_scene)}: +{len(added)} gobj — "
                  + ", ".join(added))
            changed += 1
    ui.info(f"{changed} file(s) rewritten")
    return 0


def _audit_cmd(args) -> int:
    """`audit <folder>` — the cross-check the engine performs, spelled out."""
    facts, findings = auditmod.audit(args.unit_dir)
    if not facts["parts"] and not facts["gobjs"]:
        ui.fail(f"nothing to audit in {args.unit_dir}")
        return 1
    ui.section(f"audit {args.unit_dir}")
    for name, parts in facts["parts"]:
        ui.info(f"{name}")
        for part in parts:
            print(f"    #{part.index} {part.summary()}")
    for name, gobjs in facts["gobjs"]:
        ui.info(f"{name}")
        for gobj in gobjs:
            where = (f"{gobj.g_base} #{gobj.index}" if gobj.g_base
                     else "no mesh reference")
            mat = gobj.attrs.get("material0_diffuse", "-")
            print(f"    {gobj.name} -> {where}, {mat}")
    if args.against:
        ui.section(f"against {args.against}")
        lines = auditmod.compare(args.against, args.unit_dir)
        if not lines:
            ui.ok("structurally identical")
        for line in lines:
            print("  " + line.replace("\n", "\n  "))
    if findings:
        ui.warn(f"{len(findings)} disagreement(s) — these are what the loader "
                f"reads first:")
        for finding in findings:
            ui.warn("  " + finding)
        return 1
    ui.ok("the bundle is consistent: every gobj matches its part, every part "
          "finds its atlas, the LOD carries the main mesh's parts")
    return 0


def _weights(args) -> int:
    """`weights <g>` — what a `.g` carries, optionally diffed against another."""
    for path in [args.file] + ([args.against] if args.against else []):
        if not os.path.exists(path):
            ui.section("weights")
            ui.fail(f"no such file: {path}")
            return 1
    mesh = gfile.parse_geometry_file(open(args.file, "rb").read())
    if mesh.parse_error:
        ui.section("weights")
        ui.fail(f"{os.path.basename(args.file)}: unparsed "
                f"({mesh.parse_error})")
        return 1
    doc = {
        "file": args.file,
        "parts": [
            {"name": info.name, "vertices": info.vertex_count,
             "weights_on_vertex": info.influences,
             "max_influences": info.max_influences,
             "bones": info.bone_count, "bones_used": info.bones_used,
             "dominant": info.dominant, "unweighted": info.unweighted,
             "sum": [round(info.min_sum, 4), round(info.max_sum, 4)]}
            for info in weightsmod.summarise(mesh)
        ],
    }
    diff = None
    if args.against:
        other = gfile.parse_geometry_file(open(args.against, "rb").read())
        if other.parse_error:
            ui.section("weights")
            ui.fail(f"{os.path.basename(args.against)}: unparsed "
                    f"({other.parse_error})")
            return 1
        diff = weightsmod.diff_weights(other, mesh, limit=args.lot)
        doc["against"] = args.against
        doc["diff"] = {
            "parts": diff.parts, "compared": diff.compared,
            "changed": diff.changed, "moved_bone": diff.moved_bone,
            "unmatched": diff.unmatched, "notes": diff.notes,
            "samples": [
                {"part": c.part, "index": c.index,
                 "position": [round(v, 4) for v in c.position],
                 "was": [[n, round(w, 4)] for n, w in c.was],
                 "now": [[n, round(w, 4)] for n, w in c.now],
                 "was_dominant": c.old_dominant,
                 "now_dominant": c.new_dominant}
                for c in diff.samples],
        }
    if args.json:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
        return 0

    ui.section(f"weights {os.path.basename(args.file)}")
    ui.table(weightsmod.weight_report(mesh),
             headers=(".g part", "slots (max used)", "dominant bones",
                      "weight sum"))
    if diff is None:
        return 0
    ui.section(f"against {os.path.basename(args.against)}")
    for line in diff.notes:
        ui.info(line)
    if not diff.compared:
        ui.fail("nothing comparable: the two files share no part")
        return 1
    ui.table([("vertices compared", str(diff.compared)),
              ("weights changed", str(diff.changed)),
              ("dominant bone moved", str(diff.moved_bone)),
              ("unmatched vertices", str(diff.unmatched))])
    for change in diff.samples:
        ui.info(weightsmod.describe_change(change))
    if diff.changed:
        ui.ok(f"{diff.changed} of {diff.compared} vertices carry different "
              f"weights than {os.path.basename(args.against)}")
    else:
        ui.warn("the weights are identical — nothing you painted reached "
                "this file")
    return 0


def _repair(args) -> int:
    """`repair` — recompute the container counts a `.g`'s headers advertise.

    The engine trusts the vertex/triangle pair in every sub-mesh header, so an
    edited mesh written with the donor's old counts makes it read the vertex,
    index and bone blocks out of step: the unit crashes the game (and any tool
    that loads it) instead of looking wrong.  `d3tool repair` fixes a file that
    is already out there; a fresh export is fixed by the writer itself.
    """
    path = args.file
    if not os.path.exists(path):
        ui.section("repair")
        ui.fail(f"no such file: {path}")
        return 1
    raw = open(path, "rb").read()
    ui.section(f"repair {os.path.basename(path)}")
    try:
        mesh = gfile.parse_geometry_file(raw)
        fixes = gfile.count_mismatches(mesh)
    except Exception as exc:  # noqa: BLE001
        ui.fail(f"could not read {path}: {exc}")
        return 1
    if mesh.parse_error and not fixes:
        ui.warn(f"{path} does not fully parse: {mesh.parse_error}")
    if not fixes:
        ui.ok("every container header agrees with the data — nothing to fix")
        return 0
    for line in fixes:
        ui.warn(line)
    fixed, _ = gfile.repair_counts(raw)
    out = args.out or (path if args.in_place else "")
    if not out:
        ui.info("nothing was written: pass --in-place (or -o FILE) to apply "
                "the fix")
        return 0
    with open(out, "wb") as fh:
        fh.write(fixed)
    ui.wrote(out, f"{len(fixed)} bytes")
    again = gfile.count_mismatches(gfile.parse_geometry_file(fixed))
    if again:
        for line in again:
            ui.fail(line)
        return 1
    ui.ok(f"fixed {len(fixes)} header(s) — the data and the counts now agree")
    return 0


def _profiles_dir(given: Optional[str]) -> str:
    """The folder of `.char` profiles: what was asked for, else ``Profiles``."""
    if given:
        return given
    for candidate in ("Profiles", "profiles"):
        if os.path.isdir(candidate):
            return candidate
    raise ValueError("no --profiles folder given and no ./Profiles here")


def _char_json(prof: "charmod.CharProfile", path: str) -> dict:
    return {
        "file": path,
        "id": prof.id,
        "encoding": prof.encoding,
        "unit_scene": prof.unit_scene,
        "unit_scene_lod": prof.get("unit_scene_lod"),
        "scene_file": (charmod.scene_basename(prof.unit_scene)
                       if prof.unit_scene else None),
        "includes": prof.includes,
        "blocks": [{"name": b.name, "index": b.argument,
                    "slots": prof.slots(i)}
                   for i, b in enumerate(prof.blocks)],
        "keys": prof.keys(),
        "resources": [{"key": k, "path": v} for k, v in prof.resources()],
    }


def _char(args) -> int:
    """`char show|list|find|validate|set-id` — the `.char` unit profile."""
    if args.char_cmd == "show":
        path = args.file
        if not os.path.exists(path):
            ui.section("char show")
            ui.fail(f"no such file: {path}")
            return 1
        prof = charmod.parse_char(open(path, "rb").read())
        prof.path = path
        if args.json:
            print(json.dumps(_char_json(prof, path), ensure_ascii=False,
                             indent=2))
            return 0
        ui.section(f"char {os.path.basename(path)}")
        rows = [("id", prof.id or "-"),
                ("encoding", prof.encoding + (" + BOM" if prof.bom else "")),
                ("unit_scene", charmod.normalize_reference(prof.unit_scene)
                 if prof.unit_scene else "-"),
                ("unit_scene_lod",
                 charmod.normalize_reference(prof.get("unit_scene_lod"))
                 if prof.get("unit_scene_lod") else "-"),
                ("statements", str(len(prof.statements))),
                ("keys", str(len(prof.keys())))]
        for index, block in enumerate(prof.blocks):
            slots = prof.slots(index)
            rows.append((f"set {block.argument}",
                         ", ".join(f"{k}={os.path.basename(v)}"
                                   for k, v in slots.items())))
        for key, ref in prof.resources():
            if key in charmod.RESOURCE_KEYS and key not in (
                    "unit_scene", "unit_scene_lod"):
                rows.append((key, charmod.normalize_reference(ref)))
        ui.table(rows, headers=("key", "value"))
        if prof.includes:
            ui.info("includes: " + ", ".join(prof.includes))
        if prof.parse_error:
            ui.warn(prof.parse_error)
        return 0

    if args.char_cmd == "list":
        folder = _profiles_dir(args.profiles)
        files = charmod.find_profiles(folder)
        index = charmod.ReferenceIndex(args.root) if args.root else None
        rows = []
        doc = []
        for path in files:
            prof = charmod.parse_char(open(path, "rb").read())
            if not prof.id:
                continue                     # a desc/stats/sound fragment
            scene = charmod.scene_basename(prof.unit_scene or "")
            state = ""
            if index is not None:
                state = "ok" if index.resolve(prof.unit_scene or "") else "-"
            rows.append((prof.id, scene, prof.encoding, state,
                         os.path.basename(path)))
            doc.append({"id": prof.id, "scene": scene, "file": path,
                        "resolved": state})
        if args.json:
            print(json.dumps(doc, ensure_ascii=False, indent=2))
            return 0
        ui.section(f"char list {folder}")
        ui.table(rows, headers=("id", "scene", "encoding", "scene?", "file"))
        ui.info(f"{len(rows)} profile(s) with an id")
        if index is not None:
            missing = sum(1 for r in rows if r[3] == "-")
            if missing:
                ui.warn(f"{missing} profile(s) name a scene that is not "
                        f"under {args.root}")
        return 0

    if args.char_cmd == "find":
        folder = _profiles_dir(args.profiles)
        target = args.target
        if os.path.isdir(target):
            scenes = [os.path.join(target, n) for n in sorted(os.listdir(target))
                      if n.lower().endswith(".scene")]
            scenes.sort(key=lambda p: ("_lod" in os.path.basename(p).lower(),
                                       len(p)))
            if not scenes:
                ui.section("char find")
                ui.fail(f"no .scene in {target}")
                return 1
            target = scenes[0]
        files = charmod.find_profiles(folder)
        hit = charmod.profile_for_scene(files, target)
        ui.section(f"char find {os.path.basename(target)}")
        if hit is None:
            # fall back to an id match, which is what the browser uses
            want = target.lower()
            for path in files:
                prof = charmod.parse_char(open(path, "rb").read())
                if prof.id and prof.id.lower() in (want,
                                                   os.path.basename(want)):
                    hit = (path, prof)
                    break
        if hit is None:
            ui.fail(f"no profile in {folder} points at {target}")
            return 1
        path, prof = hit
        ui.ok(f"{prof.id}  ({path})")
        ui.info(f"unit_scene: {prof.unit_scene}")
        return 0

    if args.char_cmd == "validate":
        if os.path.isdir(args.target):
            files = charmod.find_profiles(args.target)
            label = os.path.basename(os.path.normpath(args.target))
        else:
            files = [args.target]
            label = os.path.basename(args.target)
        root = args.root or os.getcwd()
        index = charmod.ReferenceIndex(root)
        ui.section(f"char validate {label}")
        problems: List[str] = []
        notes: List[str] = []
        checked = 0
        for path in files:
            prof = charmod.parse_char(open(path, "rb").read())
            prof.path = path
            if not prof.id:
                continue                     # an include fragment
            checked += 1
            report = charmod.check_profile(prof, index)
            problems.extend(report.problems)
            notes.extend(report.notes)
        if args.json:
            print(json.dumps({"profiles": checked, "problems": problems,
                              "notes": notes}, ensure_ascii=False, indent=2))
            return 1 if problems else 0
        for line in notes[:20]:
            ui.info(line)
        if len(notes) > 20:
            ui.info(f"… {len(notes) - 20} more note(s)")
        if not problems:
            ui.ok(f"{checked} profile(s), every reference resolves under "
                  f"{os.path.abspath(root)}")
            return 0
        for line in problems[:40]:
            ui.warn(line)
        if len(problems) > 40:
            ui.warn(f"… {len(problems) - 40} more")
        ui.fail(f"{len(problems)} unresolved reference(s) in {checked} "
                f"profile(s)")
        return 1

    if args.char_cmd == "set-id":
        path = args.file
        if not os.path.exists(path):
            ui.section("char set-id")
            ui.fail(f"no such file: {path}")
            return 1
        prof = charmod.parse_char(open(path, "rb").read())
        prof.path = path
        old = prof.id
        ui.section(f"char set-id {os.path.basename(path)}")
        if not prof.set_id(args.new_id):
            ui.fail("this profile has no `id` statement")
            return 1
        if args.scene and not prof.set_value("unit_scene", args.scene):
            ui.fail("this profile has no `unit_scene` statement to relink")
            return 1
        out = args.out or path
        with open(out, "wb") as fh:
            fh.write(charmod.encode_char(prof))
        ui.wrote(out, f"id {old!r} -> {args.new_id!r}")
        if args.scene:
            ui.info(f"unit_scene -> {args.scene}")
        return 0
    return 1


def _run(argv=None) -> int:
    """`main` with the argument dispatch wrapped so any user-facing error is
    reported cleanly rather than as a raw Python traceback."""
    try:
        return main(argv)
    except (ValueError, json.JSONDecodeError, OSError, struct.error) as exc:
        ui.section("error")
        ui.fail(str(exc))
        if os.environ.get("D3TOOL_DEBUG"):
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(_run())

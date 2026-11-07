"""Reader / writer for the Disciples 3 scene description file (`.scene`).

The `.scene` is the editor/game scene that references a `.g` geometry and a
`.ac` animation config, positioning them under a "Scene Root" group.  This
module both writes such a scene (mirroring the structure emitted by dis3tool
for a skinned character) and parses any `.scene` back into a node tree.

The particle emitters present in the original `.scene` files (e.g. the 9
"child particles" blocks on the AirElemental, 1 on the Zombie) are *authoring
data* that is not present in the geometry/glTF, so they cannot be reconstructed
from a reverse export.  When the source unit folder already contains a
`.scene`, the exporter reuses it verbatim so those emitters (and the GUI
camera) are preserved; otherwise a faithful, particle-free scene is generated.

Parser notes (verified against all 245 bundled `.scene` files):

* the file is a line-based block language — `globalsettings`, `group "<name>"`
  and `child <kind> "<name>"` open a block whose body is wrapped in `{` / `}`
  lines.  Braces are always alone on their line, in both the dis3tool exports
  (column 0) and this writer's generated output (tab-indented);
* prop lines are `key value;` — the trailing semicolon is optional
  (`uid 181 40875272`), and quoted values may contain commas and semicolons
  (`ps_spline_track "Red",0.0,255.0,1;0.0,128.6,0.0,0.0;`), so a prop is never
  split across lines;
* a few props are keyless (the gobj mesh line `"<path>.g" 0 0.000000`);
* children nest but indentation does not encode the depth (dis3tool puts
  `child particles` at column 0 inside a bones block);
* five shipped files use CRLF line endings; since every line is kept verbatim,
  parse ∘ render is byte-exact for all of them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple, Union

_CHILD_RE = re.compile(
    r'^\s*child\s+([A-Za-z_0-9]+)\s+"([^"]*)"(?:\s+"([^"]*)")?\s*$')
_GROUP_RE = re.compile(r'^\s*group\s+"([^"]*)"\s*$')
_GLOBAL_RE = re.compile(r'^\s*globalsettings\s*$')


@dataclass
class SceneNode:
    """One `child <kind> "<name>"` / `group` / `globalsettings` block.

    ``items`` keeps the block's original inner lines in order — plain prop
    lines verbatim plus nested :class:`SceneNode` markers — so
    :func:`render_scene` reproduces the file byte for byte while ``props``
    / ``children`` expose the parsed structure.
    """

    kind: str                       # "globalsettings" | "group" | child kind
    name: str                       # quoted header name ("" for globalsettings)
    header: str                     # verbatim header line
    # verbatim brace lines — five shipped files mix LF and CRLF per line, so
    # they cannot be re-created from a constant
    open_line: str = "{"
    close_line: str = "}"
    items: List[Union[str, "SceneNode"]] = field(default_factory=list)
    children: List["SceneNode"] = field(default_factory=list)
    # first occurrence wins, semicolon stripped, keyless lines skipped
    props: Dict[str, str] = field(default_factory=dict)

    def walk(self) -> Iterator["SceneNode"]:
        """Yield this node and every descendant, depth-first."""
        yield self
        for child in self.children:
            yield from child.walk()

    def find_all(self, kind: str) -> List["SceneNode"]:
        """Every descendant (self included) whose ``kind`` matches."""
        return [n for n in self.walk() if n.kind == kind]

    def prop(self, key: str) -> Optional[str]:
        return self.props.get(key)

    def files(self) -> List[str]:
        """All ``file`` prop values in the subtree, in document order."""
        out: List[str] = []
        for n in self.walk():
            if "file" in n.props:
                out.append(n.props["file"])
        return out


@dataclass
class SceneDoc:
    """A parsed `.scene`: top-level blocks plus the glue lines between them."""

    blocks: List[SceneNode] = field(default_factory=list)
    # verbatim lines before the first block header, between blocks, and after
    # the final closing brace (blank lines / comments; the corpus has blanks)
    leading: List[str] = field(default_factory=list)
    gaps: List[List[str]] = field(default_factory=list)
    trailing: List[str] = field(default_factory=list)

    @property
    def settings(self) -> Optional[SceneNode]:
        for block in self.blocks:
            if block.kind == "globalsettings":
                return block
        return None

    @property
    def root(self) -> Optional[SceneNode]:
        """The top-level `group` (always "Scene Root" in the corpus)."""
        for block in self.blocks:
            if block.kind == "group":
                return block
        return None

    def find_all(self, kind: str) -> List[SceneNode]:
        out: List[SceneNode] = []
        for block in self.blocks:
            out.extend(block.find_all(kind))
        return out


def _parse_prop(line: str) -> Optional[Tuple[str, str]]:
    """Split a prop line into ``(key, value)`` (semicolon stripped).

    Returns ``None`` for keyless lines (the gobj mesh reference
    ``"<path>" 0 0.000000``) — those stay in ``items`` but carry no key.
    """
    stripped = line.strip()
    if not stripped or stripped in ("{", "}"):
        return None
    if stripped.startswith('"'):
        return None
    stripped = stripped.rstrip(";").rstrip() if stripped.endswith(";") \
        else stripped
    parts = stripped.split(None, 1)
    if not parts:
        return None
    value = parts[1].strip() if len(parts) > 1 else ""
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        value = value[1:-1]     # quotes are syntax, not data
    return parts[0], value


def parse_scene(text: str) -> SceneDoc:
    """Parse a `.scene` into a :class:`SceneDoc`.

    Raises ``ValueError`` on structurally broken input (unclosed block,
    closing brace without an open one, header not followed by ``{``).
    """
    lines = text.split("\n")
    doc = SceneDoc()
    stack: List[SceneNode] = []
    pending_header: Optional[SceneNode] = None
    current: List[str] = doc.leading      # lines of the innermost open context

    def _close(line: str) -> SceneNode:
        if not stack:
            raise ValueError("unmatched '}' in scene file")
        node = stack.pop()
        node.close_line = line
        if stack:
            stack[-1].items.append(node)
            stack[-1].children.append(node)
        return node

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if pending_header is not None:
            if stripped != "{":
                raise ValueError(
                    f"line {lineno}: expected '{{' after "
                    f"{pending_header.header.strip()!r}, got {stripped!r}")
            pending_header.open_line = line
            stack.append(pending_header)
            if len(stack) == 1:
                doc.blocks.append(pending_header)
            pending_header = None
            continue
        if stack:
            if stripped == "{":
                raise ValueError(
                    f"line {lineno}: stray '{{' inside "
                    f"{stack[-1].kind} block")
            if stripped == "}":
                _close(line)
                if not stack:
                    # closing a top-level block: following glue lines go
                    # into a fresh inter-block gap (or the trailing lines)
                    doc.gaps.append([])
                    current = doc.gaps[-1]
                continue
            m = _CHILD_RE.match(line)
            if m:
                pending_header = SceneNode(kind=m.group(1), name=m.group(2),
                                           header=line)
                continue
            stack[-1].items.append(line)
            prop = _parse_prop(line)
            if prop is not None and prop[0] not in stack[-1].props:
                stack[-1].props[prop[0]] = prop[1]
            continue
        # top level: headers open blocks, everything else is glue
        if stripped == "}":
            raise ValueError(f"line {lineno}: unmatched '}}' in scene file")
        m = _CHILD_RE.match(line)
        if m:
            pending_header = SceneNode(kind=m.group(1), name=m.group(2),
                                       header=line)
            continue
        m = _GROUP_RE.match(line)
        if m:
            pending_header = SceneNode(kind="group", name=m.group(1),
                                       header=line)
            continue
        if _GLOBAL_RE.match(line):
            pending_header = SceneNode(kind="globalsettings", name="",
                                       header=line)
            continue
        current.append(line)

    if pending_header is not None:
        raise ValueError(
            f"unterminated block {pending_header.header.strip()!r}")
    if stack:
        raise ValueError(f"unclosed {stack[-1].kind} block at end of file")
    if doc.gaps and doc.gaps[-1] == []:
        doc.gaps.pop()
    return doc


def _render_node(node: SceneNode) -> List[str]:
    out = [node.header, node.open_line]
    for item in node.items:
        if isinstance(item, SceneNode):
            out.extend(_render_node(item))
        else:
            out.append(item)
    out.append(node.close_line)
    return out


def render_scene(doc: SceneDoc) -> str:
    """Re-assemble a :class:`SceneDoc` — byte-identical to the parse input."""
    out = list(doc.leading)
    for i, block in enumerate(doc.blocks):
        out.extend(_render_node(block))
        if i < len(doc.gaps):
            out.extend(doc.gaps[i])
    out.extend(doc.trailing)
    return "\n".join(out)


def count_particles(doc: SceneDoc) -> int:
    """Number of `child particles` blocks (authoring-only FX emitters)."""
    return len(doc.find_all("particles"))


def _node_header(ind: str, name: str, kind: str = "bones") -> list:
    """Common header lines for a `child <kind> "<name>"` block."""
    return [
        f"{ind}child {kind} \"{name}\" ",
        f"{ind}{{",
        f"{ind}\tisrotate 0, 0, 0;",
        f"{ind}\trotate 0.000000, 0.000000, 0.000000;",
        f"{ind}\tapllytransformtochildren 0;",
        f"{ind}\tisfluctuation 0, 0, 0;",
        f"{ind}\tfluctuation 0.000000, 0.000000, 0.000000;",
        f"{ind}\tfluctuationamplitude 1.000000, 1.000000, 1.000000;",
        f"{ind}\tapllyfluctuationtochildren 0;",
    ]


def write_scene(
    mesh_name: str,
    base: str,
    res_root: str,
    attrs: Dict[str, str],
    *,
    gobj_name: Optional[str] = None,
    obb: Optional[str] = None,
) -> str:
    """Render a `.scene` file for a skinned character.

    ``res_root`` is the relative resource directory, e.g.
    ``resources\\characters\\neutrals\\airelemental``.  ``base`` is the geometry
    basename (used for the `bones` node and the `.g`/`.ac` file paths);
    ``gobj_name`` is the display name used for the `gobj` node (defaults to
    ``mesh_name``).  ``obb`` is an optional 6-value OBB box.
    """
    geometry_file = f"{res_root}\\{base}.g"
    ac_file = f"{res_root}\\{base}.ac"
    gobj = gobj_name or mesh_name
    if obb is None:
        obb = "-0.010000,-0.010000,-0.010000,0.010000,0.010000,0.010000"

    lines = [
        "globalsettings ",
        "{",
        "\tfov 1.100000;",
        "\tfarplane 90000.000000;",
        "\tnearplane 0.100000;",
        "\twaterlevel 0.000000;",
        "\tshadowint 0.750000;",
        "\ttoolsscale 0.010000;",
        "\tambcolor -13684945;",
        "\tshadowcolor -16777216;",
        "\tportalrm 0;",
        "\tpostprocess 0;",
        "\tpostprocess_glow 0;",
        "\tpostprocess_blur 0;",
        "\tfogparams 1,2634320,0.660000,300.000000,5500.000000;",
        "\tguicamera -2.918151,1.822160,4.188418,-2.458722,1.717352,3.306410,1.315115,0.856448,-3.938563,-2.918151,1.822160,4.188418,0.105000,-9.904998,0.000000,14.319616;",
        "\tblur_burn 1.000000;",
        "\tglow_power 1.000000;",
        "\tglow_offset 1.000000;",
        "\tglow_color 1.000000,1.000000,1.000000;",
        "\tload_counter 1507;",
        "}",
        "",
        'group "Scene Root" ',
        "{",
        "\tisrotate 0, 0, 0;",
        "\trotate 0.000000, 0.000000, 0.000000;",
        "\tapllytransformtochildren 0;",
        "\tisfluctuation 0, 0, 0;",
        "\tfluctuation 0.000000, 0.000000, 0.000000;",
        "\tfluctuationamplitude 1.000000, 1.000000, 1.000000;",
        "\tapllyfluctuationtochildren 0;",
        "\tuid 181 40875272",
        "\tcoords 0.000000,0.000000,0.000000,0.000000,0.000000,0.000000",
        f"\tobbdata {obb}",
        "\tdwflags 1",
        "\tplvl 512",
        "\tlodgroup 1",
    ]

    # ---- child bones ----
    lines += _node_header("\t", base, "bones")
    lines += [
        "\t\tuid 182 41514848",
        "\t\tcoords 0.000000,0.000000,0.000000,0.000000,0.000000,0.000000",
        "\t\tdwflags 1048576",
        f'\t\tfile "{ac_file}"',
        "\t\tplvl 512",
        "\t\tlooktocamera 0;",
    ]

    # ---- child gobj ----
    lines += _node_header("\t\t", gobj, "gobj")
    lines += [
        "\t\t\tuid 209 108716040",
        "\t\t\tdwflags 1053761",
        '\t\t\ttech "model"',
        "\t\t\tbonesuid 182 41514848",
        "\t\t\tmesh_lods 1",
        f'\t\t\t"{geometry_file}" 0 0.000000',
        "\t\t\tcoords 0.000000,2.833831,0.005191,0.000000,0.000000,0.000000",
        '\t\t\tfragmentshader "system\\fragment\\default.fr"',
        '\t\t\ttransparentshader "system\\xshader\\transparent.xshader";',
        "\t\t\talphainrendertoshadowmap 0;",
        '\t\t\tblockshader "system\\xshader\\dif_light2x.xshader"',
        "\t\t\tlights_num 4",
        "\t\t\tplvl 512",
    ]
    for k, v in attrs.items():
        lines.append(f'\t\t\tattr "{k}" "{v}"')
    lines.append('\t\t\tattr "vdshader" "dif_light2x"')
    lines += [
        "\t\t}",
        "\t}",
        "}",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  parts the scene does not name
# --------------------------------------------------------------------------- #

_ATTR_LINE_RE = re.compile(r'^(\s*)attr\s+"([^"]+)"\s+"([^"]*)"\s*$')
#: A per-material attribute line, e.g. ``attr "material1_diffuse" "…"``.
_MATERIAL_ATTR_RE = re.compile(r'^\s*attr\s+"material\d+_')
_MESH_LINE_RE = re.compile(r'^(\s*)"([^"]*)"\s+(\d+)\s+([0-9.]+)\s*$')
_UID_LINE_RE = re.compile(r'^(\s*)uid\s+(\d+)\s+(\d+)\s*$')


def node_attr(node: SceneNode, key: str) -> Optional[str]:
    """The value of an ``attr "<key>" "<value>"`` line inside ``node``.

    ``SceneNode.props`` keeps only the first line per key, so the repeated
    attribute block a `gobj` carries cannot be read through it.
    """
    for line in node.items:
        if not isinstance(line, str):
            continue
        m = _ATTR_LINE_RE.match(line)
        if m and m.group(2) == key:
            return m.group(3)
    return None


def node_mesh_index(node: SceneNode) -> Optional[int]:
    """The number a `gobj` writes after its `.g` path (its slot in the file)."""
    for line in node.items:
        if not isinstance(line, str):
            continue
        m = _MESH_LINE_RE.match(line)
        if m:
            return int(m.group(3))
    return None


def _set_attr(items: List[str], key: str, value: str) -> bool:
    """Rewrite ``attr "<key>" "…"`` in place; ``False`` when there is none."""
    for i, line in enumerate(items):
        m = _ATTR_LINE_RE.match(line)
        if m and m.group(2) == key:
            items[i] = f'{m.group(1)}attr "{key}" "{value}"' + _eol(line)
            return True
    return False


def _parents(doc: SceneDoc) -> Dict[int, SceneNode]:
    out: Dict[int, SceneNode] = {}

    def walk(node: SceneNode) -> None:
        for child in node.children:
            out[id(child)] = node
            walk(child)

    for block in doc.blocks:
        walk(block)
    return out


def _eol(line: str) -> str:
    """The line terminator ``line`` carries (``"\\r"`` or ``""``).

    A scene is read with ``newline=""`` and rendered by joining on ``"\\n"``,
    so the carriage return is part of the line.  A block cloned out of the
    file keeps its neighbours' endings only if every line this module
    *rewrites* puts its `\\r` back — otherwise one inserted `gobj` converts
    the whole file, which shows up as a payload that differs everywhere.
    """
    return "\r" if line.endswith("\r") else ""


def ensure_part_gobjs(text: str, parts) -> Tuple[str, List[str]]:
    """Give every part in the `.g` a `gobj` the engine will instantiate.

    The engine attaches the i-th mesh part to the i-th `gobj`, in document
    order: every shipped unit carries exactly one gobj per part (84 of 85
    scenes), and the LOD scenes settle that it is *position*, not the
    ``attr "name"``, that binds — their gobj names (`empire_angel_wing`) never
    match their part names (`lod_empire_angel_wing`) and the game draws them
    anyway.  So a `.g` with more parts than the scene has gobjs leaves the
    *trailing* parts without a node and invisible — exactly the Angel's sword,
    the fifth mesh with four gobjs to bind it.

    Each unbound trailing part gets a clone of the unit's largest *opaque*
    `gobj`: same tech, shaders, flags and `.g` path, a fresh `uid`/`dwNode`,
    the next mesh slot number, and the attribute block re-stated for that part
    (name, diffuse, triangle/vertex/weight/bone counts).

    ``parts`` are objects (or mappings) with ``name``, ``material_diffuse``,
    ``tri_count``, ``vertex_count``, ``weights_on_vertex`` and ``bones``.
    Returns the new text and the names of the blocks that were added; the text
    comes back unchanged, byte for byte, when there is nothing to add.
    """
    doc = parse_scene(text)
    gobjs = doc.find_all("gobj")
    if not gobjs:
        return text, []

    def field(part, key: str):
        return part.get(key) if isinstance(part, dict) else getattr(part, key)

    ordered = list(parts)
    # one gobj per mesh part, in file order
    unbound = ordered[len(gobjs):]
    if not unbound:
        return text, []

    opaque = [n for n in gobjs if "alpha" not in (n.props.get("tech") or "")]
    template = max(opaque or gobjs,
                   key=lambda n: int(node_attr(n, "material0_triangles_num")
                                     or 0))
    parent = _parents(doc).get(id(template))
    if parent is None:
        return text, []
    indent = template.header[:len(template.header)
                             - len(template.header.lstrip())]

    used_uid = []
    used_dw = []
    for n in doc.find_all("gobj") + doc.find_all("bones"):
        m = re.match(r"^(\d+)\s+(\d+)$", n.props.get("uid") or "")
        if m:
            used_uid.append((int(m.group(1)), int(m.group(2))))
        dw = node_attr(n, "dwNode")
        if dw and dw.isdigit():
            used_dw.append(int(dw))
    next_uid = (max([u for u, _ in used_uid] or [0]),
                max([u for _, u in used_uid] or [0]))
    next_dw = max(used_dw or [0])
    slots = [node_mesh_index(n) for n in gobjs]
    slot = max([s for s in slots if s is not None] or [len(gobjs) - 1]) + 1

    eol = _eol(template.items[-1]) if template.items else _eol(template.header)
    # ... and so do the attribute lines this module appends: take the
    # template's own indentation rather than assuming one level deeper
    attr_indent = indent + "\t"
    for line in template.items:
        if isinstance(line, str) and _ATTR_LINE_RE.match(line):
            attr_indent = line[:len(line) - len(line.lstrip())]
            break
    added: List[str] = []
    for i, part in enumerate(unbound):
        name = str(field(part, "name"))
        items: List[str] = []
        for item in template.items:
            if not isinstance(item, str):
                continue            # a clone must not duplicate FX/children
            if _MATERIAL_ATTR_RE.match(item):
                # the clone is re-stated for the part it draws: one material
                # (`materials_num 1`, the part's diffuse and triangle count).
                # A template that carries two materials — the LOD body always
                # does — would otherwise hand the new part the *donor's*
                # second diffuse, which the engine then tries to resolve for a
                # mesh that has no such range.
                continue
            m = _UID_LINE_RE.match(item)
            if m:
                next_uid = (next_uid[0] + 1, next_uid[1] + 1_048_576)
                items.append(f"{m.group(1)}uid {next_uid[0]} {next_uid[1]}"
                             + _eol(item))
                continue
            m = _MESH_LINE_RE.match(item)
            if m:
                items.append(f'{m.group(1)}"{m.group(2)}" {slot} {m.group(4)}'
                             + _eol(item))
                continue
            items.append(item)
        for key, value in (("name", name),
                           ("material0_diffuse",
                            str(field(part, "material_diffuse") or "")),
                           ("material0_triangles_num",
                            str(field(part, "tri_count"))),
                           ("vertexs_weights_num",
                            str(field(part, "vertex_count"))),
                           ("weights_on_vertex",
                            str(field(part, "weights_on_vertex"))),
                           ("bones_num", str(len(field(part, "bones") or ()))),
                           ("materials_num", "1")):
            if not _set_attr(items, key, value):
                items.append(f'{attr_indent}attr "{key}" "{value}"' + eol)
        next_dw += 8_000_000 + i
        if not _set_attr(items, "dwNode", str(next_dw)):
            items.append(f'{attr_indent}attr "dwNode" "{next_dw}"' + eol)
        node = SceneNode(kind="gobj", name=name,
                         header=f'{indent}child gobj "{name}" ' + eol,
                         open_line=template.open_line,
                         close_line=template.close_line, items=items)
        node.props = {}
        for line in items:
            prop = _parse_prop(line)
            if prop is not None and prop[0] not in node.props:
                node.props[prop[0]] = prop[1]
        slots_used = [i for i, it in enumerate(parent.items)
                      if isinstance(it, SceneNode) and it.kind == "gobj"]
        at = (slots_used[-1] + 1) if slots_used else len(parent.items)
        if at and isinstance(parent.items[at - 1], SceneNode):
            parent.items.insert(at, "" + eol)   # the blank separator line
            at += 1
        parent.items.insert(at, node)
        parent.children.append(node)
        added.append(name)
        slot += 1
    return render_scene(doc), added

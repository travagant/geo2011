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

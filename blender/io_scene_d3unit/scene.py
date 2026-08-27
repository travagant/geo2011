# -*- coding: utf-8 -*-
"""Minimal .scene / .ac text parsers (DarkWave "cfg" format).

Line-oriented syntax:
    key value value ;                 - property
    child type "Name" { ... }        - nested block
    "somefile.g" 0 0.0              - bare statement (mesh reference)
    attr "k" "v"                     - attribute (no trailing ';')

We only extract what the importer/exporter needs.
"""
import re

_TOKRE = re.compile(r'"[^"]*"|[^\s{};"]+')


class Block:
    def __init__(self, words):
        self.header = words            # tokens on the 'child' line
        self.props = {}                # key -> raw value string
        self.attrs = []                # list of (key, value)
        self.children = []
        self.raw = []                  # every statement as token list

    @property
    def name(self):
        for t in self.header:
            if t.startswith('"'):
                return t[1:-1]
        return ''

    def get(self, key, default=None):
        return self.props.get(key, default)

    def attr(self, key, default=None):
        for k, v in self.attrs:
            if k == key:
                return v
        return default

    def find(self, type_name, name=None):
        for c in self.children:
            if type_name in c.header and (name is None or c.name == name):
                yield c


def _split(line):
    return _TOKRE.findall(line)


def parse_text(text):
    root = Block(['<root>'])
    stack = [root]
    pending = []          # tokens of a header line waiting for '{'

    def commit_pending():
        if pending:
            stack[-1].raw.append(list(pending))
            pending.clear()

    lines = []
    for raw in text.splitlines():
        line = raw.split('//')[0].strip()
        if not line:
            continue
        if line == '{' and lines:
            lines[-1] += ' {'
        else:
            lines.append(line)
    for line in lines:
        if line == '}':
            commit_pending()
            if len(stack) > 1:
                stack.pop()
            continue
        opening = line.endswith('{')
        line = line[:-1].strip() if opening else line
        words = _split(line)
        if opening:
            blk = Block(pending + words)
            pending.clear()
            stack[-1].children.append(blk)
            stack.append(blk)
            continue
        commit_pending()
        if words and words[0] in ('child', 'state', 'group'):
            pending = words
            continue
        cur = stack[-1]
        cur.raw.append(words)
        if words and words[0] == 'attr' and len(words) >= 3:
            cur.attrs.append((words[1].strip('"'), words[2].strip('"')))
        elif len(words) >= 2 and re.fullmatch(r'[A-Za-z_]\w*', words[0]):
            cur.props[words[0]] = ' '.join(w.strip('"') for w in words[1:])
    commit_pending()
    return root


def parse_scene(path):
    text = open(path, 'r', encoding='latin-1').read()
    root = parse_text(text)
    gobjs = []
    bones_files = []

    def walk(blk):
        for c in blk.children:
            head = ' '.join(c.header)
            if 'child gobj' in head or 'gobj' in head.split():
                g = {'name': c.name, 'attrs': dict(c.attrs),
                     'coords': c.get('coords', ''),
                     'meshfile': '', 'meshindex': 0}
                for r in c.raw:
                    if len(r) >= 2 and r[0].strip('"').endswith('.g'):
                        g['meshfile'] = r[0].strip('"')
                        try:
                            g['meshindex'] = int(r[1])
                        except ValueError:
                            pass
                gobjs.append(g)
            elif 'child bones' in head:
                bones_files.append(c.get('file', ''))
            walk(c)
    walk(root)
    return {'gobjs': gobjs, 'bones_file': bones_files[0] if bones_files else ''}


def parse_ac(path):
    text = open(path, 'r', encoding='latin-1').read()
    root = parse_text(text)
    states = []
    for c in root.children:
        if 'state' not in c.header:
            continue
        st = {'name': c.name, 'file': c.get('file', ''),
              'frame0': int(c.get('frame0', 0)),
              'frame1': int(c.get('frame1', 0)),
              'fps': float(c.get('fps', 15.0)),
              'priority': int(c.get('priority', 256)),
              'flags': int(c.get('flags', 0)),
              'links': [], 'events': [], 'gaestate': None, 'meshfile': ''}
        for r in c.raw:
            if not r:
                continue
            if r[0] == 'link' and len(r) >= 3:
                blend = 0.0
                for j, t in enumerate(r):
                    if t == 'blend' and j + 1 < len(r):
                        blend = float(r[j + 1])
                st['links'].append((r[1].strip('"'), int(r[2]), blend))
            elif r[0] == 'event2' and len(r) >= 3:
                st['events'].append(r[1:])
            elif r[0] == 'gaestate' and len(r) >= 2:
                st['gaestate'] = r[1].strip('"')
            elif len(r) >= 1 and r[0].endswith('.g'):
                st['meshfile'] = r[0]
        states.append(st)
    return {'states': states}

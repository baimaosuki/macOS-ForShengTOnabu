#!/usr/bin/env python3
"""Minimal-edit writer for the Apple DeviceTree (.adt) blob.

The generator rebuilds the whole tree, which also refreshes /chosen/random-seed
and can add or drop unrelated nodes.  When only one property or child node has
to change, edit the deployed blob instead: every node that is not named on the
command line is re-serialised from the bytes that were read, so the result is
byte-identical everywhere else.

    adt_edit.py kernel.macho.adt --output out.adt \
        --add-prop '/chosen:crypto-hash-method=sha2-384' \
        --add-child '/chosen:manifest-properties'
"""
import argparse
import struct
import sys


def parse(data):
    def read(at):
        nprops, nchildren = struct.unpack_from('<II', data, at)
        at += 8
        props = []
        for _ in range(nprops):
            key, size = struct.unpack_from('<32sI', data, at)
            at += 36
            props.append((key.rstrip(b'\0').decode(), data[at:at + size]))
            at += (size + 3) & ~3
        children = []
        for _ in range(nchildren):
            child, at = read(at)
            children.append(child)
        return {'props': props, 'children': children}, at
    root, end = read(0)
    if end != len(data):
        raise SystemExit('adt: parsed %d bytes of %d' % (end, len(data)))
    return root


def serialise(node):
    out = bytearray(struct.pack('<II', len(node['props']), len(node['children'])))
    for key, value in node['props']:
        out += struct.pack('<32sI', key.encode('ascii'), len(value))
        out += value + bytes(-len(value) % 4)
    for child in node['children']:
        out += serialise(child)
    return bytes(out)


def name_of(node):
    for key, value in node['props']:
        if key == 'name':
            return value.rstrip(b'\0').decode()
    raise SystemExit('adt: node without name property')


def walk(node, path=()):
    here = (*path, name_of(node))
    yield here, node
    for child in node['children']:
        yield from walk(child, here)


def find(root, path):
    """Exact path, or the unique node whose path ends with it."""
    want = tuple(p for p in path.strip('/').split('/') if p)
    nodes = list(walk(root))
    for here, node in nodes:
        if here == want:
            return node
    tail = [(here, node) for here, node in nodes if here[-len(want):] == want]
    if len(tail) == 1:
        return tail[0][1]
    if not tail:
        raise SystemExit('adt: no node %s' % path)
    raise SystemExit('adt: %s is ambiguous: %s'
                     % (path, ', '.join('/'.join(h) for h, _ in tail)))


def set_prop(node, key, value):
    for i, (k, _) in enumerate(node['props']):
        if k == key:
            node['props'][i] = (key, value)
            return 'replaced'
    node['props'].append((key, value))
    return 'added'


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('adt')
    ap.add_argument('--output')
    ap.add_argument('--add-prop', action='append', default=[],
                    metavar='/node:key=value')
    ap.add_argument('--add-child', action='append', default=[],
                    metavar='/node:child')
    ap.add_argument('--check', action='store_true', help='verify without writing')
    args = ap.parse_args(argv)

    raw = open(args.adt, 'rb').read()
    root = parse(raw)
    before = {here: dict(props) for here, node in walk(root) for props in [node['props']]}

    if not args.add_prop and not args.add_child:
        for here, node in walk(root):
            if len(here) == 1 or here[-1] == 'chosen':
                print('/%s  props=%d children=%d' % ('/'.join(here), len(node['props']),
                                                     len(node['children'])))
        return 0

    for spec in args.add_prop:
        where, _, assignment = spec.rpartition(':')
        key, _, value = assignment.partition('=')
        node = find(root, where)
        state = set_prop(node, key, value.encode('ascii') + b'\0')
        print('  %-34s %s -> %r [%s]' % (where, key, value, state))
    for spec in args.add_child:
        where, _, child = spec.rpartition(':')
        node = find(root, where)
        if any(name_of(c) == child for c in node['children']):
            print('  %-34s child %-24s already present' % (where, child))
            continue
        node['children'].append({'props': [('name', child.encode('ascii') + b'\0')],
                                 'children': []})
        print('  %-34s child %-24s appended' % (where, child))

    out = serialise(root)
    after = {here: dict(props) for here, node in walk(parse(out)) for props in [node['props']]}
    changed = []
    for here, props in before.items():
        now = after.get(here)
        if now is None:
            changed.append((here, 'node disappeared'))
            continue
        for key, value in props.items():
            if now.get(key) != value:
                changed.append((here, key))
    for here in after:
        if here not in before:
            changed.append((here, 'new node'))
    print('  %d node(s) differ, %d -> %d bytes' % (len(changed), len(raw), len(out)))
    for here, what in changed:
        print('    /%s  %s' % ('/'.join(here), what))
    if args.check:
        return 0
    target = args.output or args.adt
    open(target, 'wb').write(out)
    print('  written %s' % target)
    return 0


if __name__ == '__main__':
    sys.exit(main())

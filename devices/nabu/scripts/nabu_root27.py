"""Host-side ROOT assembly helpers. No device, mount or network operations."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import struct

MIB = 1 << 20
WINDOWS = ('OS', 'SystemOS', 'App')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def target_path(value):
    path = PurePosixPath(value)
    if (not path.is_absolute() or '..' in path.parts or '\n' in value
            or '\r' in value or '\t' in value or '\\' in value):
        raise ValueError('Invalid target path')
    return path.as_posix()


def save(path, value):
    with Path(path).open('x') as stream:
        stream.write(json.dumps(value, indent=2) + '\n')


def export_volume(source, output):
    """Read decompressed bytes through a read-only mount; record native owners.

    The APFS BSD-flags reader is a separate required step, since Linux stat
    does not expose SF_RESTRICTED. This is a new image, not an in-place edit.
    """
    source, output = source.resolve(), output.absolute()
    if output.exists() or output.is_relative_to(source):
        raise ValueError('Export must be a new directory outside the source')
    output.mkdir(parents=True)
    files, directories, symlinks = [], [], []
    for parent, dirs, names in os.walk(source, followlinks=False,
                                       onerror=lambda err: (_ for _ in ()).throw(err)):
        directory = Path(parent)
        status = directory.stat()
        relative = directory.relative_to(source).as_posix()
        name = '/' if relative == '.' else '/' + relative
        directories.append(dict(path=target_path(name), uid=status.st_uid,
                                gid=status.st_gid, mode=f'{stat.S_IMODE(status.st_mode):o}'))
        for name in sorted(dirs + names):
            path = directory / name
            relative = path.relative_to(source)
            target = target_path('/' + relative.as_posix())
            status = path.lstat()
            metadata = dict(path=target, uid=status.st_uid, gid=status.st_gid,
                            mode=f'{stat.S_IMODE(status.st_mode):o}')
            if stat.S_ISLNK(status.st_mode):
                symlinks.append(dict(metadata, target=os.readlink(path)))
            elif stat.S_ISREG(status.st_mode):
                destination = output / 'files' / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, destination)
                files.append(dict(metadata, source=str(destination), sha256=sha(destination)))
            elif not stat.S_ISDIR(status.st_mode):
                raise ValueError(f'Unsupported special file in source: {target}')
    return dict(files=files, directories=directories, symlinks=symlinks,
                source_root=str(source), bsd_flags_pending=True,
                metadata_scope='uid/gid/mode/symlink/native BSD flags; ACL/xattrs are not copied')


def merge_plan(plan, overlays, cache=None, cache_prefix=None):
    merged = dict(plan, files=[dict(item) for item in plan['files']])
    files = {target_path(item['path']): item for item in merged['files']}
    if len(files) != len(merged['files']):
        raise ValueError('Duplicate target in volume plan')
    changes = [item for overlay in overlays for item in overlay]
    if cache is not None:
        prefix = target_path(cache_prefix)
        changes += [dict(path=prefix.rstrip('/') + '/' + row['name'],
                         source=str(Path(cache['output']) / row['name']), sha256=row['sha256'])
                    for row in cache['files']]
    replaced = set()
    for item in changes:
        target = target_path(item['path'])
        if target not in files or target in replaced:
            raise ValueError(f'Unknown or duplicate overlay target: {target}')
        source = Path(item['source']).resolve()
        digest = sha(source)
        if item.get('sha256') and item['sha256'] != digest:
            raise ValueError('Overlay checksum mismatch')
        # Keep original ownership/mode, irrespective of the export user's UID.
        files[target].update(source=str(source), sha256=digest)
        replaced.add(target)
    merged['overlays'] = len(replaced)
    return merged


def converted_text_fixups(specs, cache):
    """Retain each declared edit, checking old and converted preimages exactly."""
    result = []
    original = Path(cache['cache']).parent
    converted = Path(cache['output'])
    for spec in specs:
        name, offset, before, after = spec.split(',')
        if Path(name).name != name:
            raise ValueError('Invalid signed-text cache basename')
        address, expected = int(offset, 16), bytes.fromhex(before)
        with (original / name).open('rb') as stream:
            stream.seek(address)
            if stream.read(len(expected)) != expected:
                raise ValueError('Declared signed-text preimage differs from original cache')
        with (converted / name).open('rb') as stream:
            stream.seek(address)
            actual = stream.read(len(expected))
        if len(actual) != len(expected) or len(bytes.fromhex(after)) != len(actual):
            raise ValueError('Invalid converted signed-text fixup length')
        result.append(','.join((name, offset, actual.hex(), after)))
    return result


def pack_root(output, windows, tables):
    """Write the production SHNGROOT v2 layout and rehash its readback."""
    output = Path(output)
    if output.exists() or tuple(windows) != WINDOWS or tuple(tables) != WINDOWS:
        raise ValueError('New output and OS/SystemOS/App in order are required')
    rows, cursor = [], MIB
    for flags, inputs in ((1, windows), (3, tables)):
        for name, path in inputs.items():
            path = Path(path)
            status = path.stat()
            if not stat.S_ISREG(status.st_mode) or not status.st_size:
                raise ValueError('ROOT inputs must be nonempty regular files')
            size = status.st_size
            rows.append(dict(name=name if flags == 1 else name + '.vroot',
                             offset=cursor, size=size, block_size=512 if flags == 1 else 4096,
                             flags=flags, sha256=sha(path), source=str(path.resolve())))
            cursor += (size + MIB - 1) // MIB * MIB
    header = bytearray(4096)
    struct.pack_into('<8sIIQ', header, 0, b'SHNGROOT', 2, len(rows), cursor)
    for index, item in enumerate(rows):
        struct.pack_into('<32sQQII32s40s', header, 24 + index * 128,
                         item['name'].encode(), item['offset'], item['size'], item['block_size'],
                         item['flags'], bytes.fromhex(item['sha256']), bytes(40))
    header[4064:] = hashlib.sha256(header[:4064]).digest()
    with output.open('xb') as stream:
        stream.truncate(cursor)
        for item in rows:
            stream.seek(item['offset'])
            with Path(item['source']).open('rb') as source:
                shutil.copyfileobj(source, stream, 8 << 20)
        stream.seek(0)
        stream.write(header)
        stream.flush()
        os.fsync(stream.fileno())
    with output.open('rb') as stream:
        if stream.read(4096) != header:
            raise ValueError('ROOT header readback mismatch')
        for item in rows:
            stream.seek(item['offset'])
            digest = hashlib.sha256()
            left = item['size']
            while left:
                data = stream.read(min(left, 8 << 20))
                if not data:
                    raise ValueError('ROOT truncated during readback')
                digest.update(data)
                left -= len(data)
            if digest.hexdigest() != item['sha256']:
                raise ValueError('ROOT window readback mismatch')
    return dict(image_bytes=cursor, image_sha256=sha(output), windows=rows,
                manifest_sha256=hashlib.sha256(header).hexdigest(), readback_verified=True)

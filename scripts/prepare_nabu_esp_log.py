#!/usr/bin/env python3
"""Prepare an offline copy of a NABU FAT32 ESP for bounded NABULOG2 writes.

Never opens a block device or edits GPT. Preserve every other file allocation.
The input must be a fresh complete ESP backup, with byte size matching GPT.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import secrets
import struct
import tomllib

from nabu_log_volume import BLOCK, FILE_BYTES, NAME, FatVolume, check_config, make_config


def volume(image):
    if len(image) % BLOCK:
        raise ValueError('ESP image must contain whole 4096-byte blocks')
    return FatVolume(lambda first, count: image[first*BLOCK:(first+count)*BLOCK], len(image)//BLOCK)


def enable_config(text, token):
    """Change only the default xnu entry's args, keeping existing options."""
    if len(token) != 16 or not any(token):
        raise ValueError('invalid allocation identity')
    parsed = tomllib.loads(text)
    default = parsed.get('boot', {}).get('default')
    selected = [e for e in parsed.get('entry', []) if e.get('id') == default]
    if len(selected) != 1 or selected[0].get('type') != 'xnu':
        raise ValueError('requires exactly one default xnu entry')
    old = selected[0].get('args', '')
    args = [a for a in old.split() if not a.startswith(('nabu-log=', 'nabu-log-token='))]
    args += ['nabu-log=1', 'nabu-log-token=' + token.hex()]
    new = ' '.join(args)
    if not new.isascii() or len(new.encode()) > 1023:
        raise ValueError('boot args exceed the loader ASCII/1023-byte limit')
    blocks = re.split(r'(?m)^(?=\[\[entry\]\]\s*$)', text)
    changes = 0
    for i, part in enumerate(blocks):
        if not part.startswith('[[entry]]'):
            continue
        entry = tomllib.loads(part).get('entry', [{}])[0]
        if entry.get('id') != default:
            continue
        # Only support a simple quoted args line, which our assembler generates.
        replacement = 'args = ' + json.dumps(new)
        blocks[i], count = re.subn(r'(?m)^args[ \t]*=[ \t]*"[^"\n]*"[ \t]*$', lambda _: replacement, part)
        if count != 1:
            raise ValueError('default entry requires one simple args line')
        changes += 1
    if changes != 1:
        raise ValueError('default entry not uniquely editable')
    result = ''.join(blocks)
    check = tomllib.loads(result)
    expected = tomllib.loads(text)
    next(e for e in expected['entry'] if e.get('id') == default)['args'] = new
    if check != expected:
        raise ValueError('configuration changed outside default entry boot args')
    return result


def prepare(image, token=None):
    original = volume(image)
    owners = original.ownership()
    incoming = {original.next(c) for c in range(2, original.clusters+2)}
    matches = [e for e in original.directory(original.root) if e[0] == NAME]
    if len(matches) > 1:
        raise ValueError('duplicate log root entries')
    previous_chain, entry_at, contents = [], None, None
    if matches:
        _, attr, first, size, entry_at = matches[0]
        if attr != 0x20 or size != FILE_BYTES:
            raise ValueError('existing log entry attributes/size differ; preserve it for manual recovery')
        previous_chain = original.chain(first)
        if len(previous_chain) != FILE_BYTES // BLOCK:
            raise ValueError('existing log allocation length differs')
        contents = b''.join(original.read(original.data_first+c-2, 1) for c in previous_chain)
        old_token = check_config(contents[:BLOCK])
        if token is not None and token != old_token:
            raise ValueError('explicit token differs from existing log; refusing to erase old snapshots')
        token = old_token
    if token is None:
        token = secrets.token_bytes(16)
    config = make_config(token)
    if contents is None:
        contents = config + bytes(FILE_BYTES-BLOCK)
    count = FILE_BYTES // BLOCK
    if previous_chain and previous_chain == list(range(previous_chain[0], previous_chain[0]+count)):
        start = previous_chain[0]
    else:
        # Relocate only into clusters free before this operation; old log stays recoverable in backup.
        start, length = 0, 0
        for c in range(2, original.clusters+2):
            length = length+1 if original.next(c) == 0 and c not in owners and c not in incoming else 0
            if length == count:
                start = c+1-count
                break
        if not start:
            raise ValueError('no original free contiguous 2051-cluster run; no repartitioning performed')
    result = bytearray(image)
    allowed = set()
    def write(at, data):
        result[at:at+len(data)] = data
        allowed.update(range(at//BLOCK, (at+len(data)+BLOCK-1)//BLOCK))
    if entry_at is None:
        # Require existing root-directory capacity; do not extend another allocation.
        for cluster in original.chain(original.root):
            block = original.read(original.data_first+cluster-2, 1)
            slot = next((off for off in range(0, BLOCK, 32) if block[off] in (0, 0xe5)), None)
            if slot is not None:
                entry_at = (original.data_first+cluster-2)*BLOCK+slot
                break
        if entry_at is None:
            raise ValueError('no free root directory slot')
        entry = bytearray(32)
        entry[:11] = NAME
        entry[11] = 0x20
    else:
        entry = bytearray(image[entry_at:entry_at+32])
    fat = bytearray(original.fat)
    for c in previous_chain:
        struct.pack_into('<I', fat, c*4, 0)
    for i in range(count):
        struct.pack_into('<I', fat, (start+i)*4, start+i+1 if i+1<count else 0x0fffffff)
    for copy in range(2):
        write((original.fat_first+copy*original.fat_blocks)*BLOCK, fat)
    struct.pack_into('<H', entry, 20, start >> 16)
    struct.pack_into('<H', entry, 26, start & 0xffff)
    struct.pack_into('<I', entry, 28, FILE_BYTES)
    write(entry_at, entry)
    at = (original.data_first+start-2)*BLOCK
    write(at, contents)
    # FAT32 FSInfo advisory counters become unknown; do not leave stale free counts.
    fsinfo = struct.unpack_from('<H', image, 48)[0]
    backup = struct.unpack_from('<H', image, 50)[0]
    for sector in (fsinfo, backup+fsinfo if backup not in (0, 0xffff) else 0):
        if 0 < sector < original.fat_first:
            block = image[sector*BLOCK:(sector+1)*BLOCK]
            if block[:4] == b'RRaA' and block[484:488] == b'rrAa':
                write(sector*BLOCK+488, b'\xff'*8)
    verified = volume(result)
    first, blocks, actual = verified.log_extent()
    verified.ownership()
    for c in range(2, verified.clusters+2):
        destination = verified.next(c)
        if start <= destination < start+count and not (start <= c < start+count-1 and destination == c+1):
            raise ValueError('another FAT chain points into the prepared log allocation')
    if (first, blocks, actual) != (original.data_first+start-2, count, token):
        raise ValueError('prepared allocation readback differs')
    # Every previous allocated cluster except old log/root entry bytes remains exact.
    root_block = entry_at//BLOCK
    for c in owners:
        if c in previous_chain:
            continue
        offset = (original.data_first+c-2)*BLOCK
        before, after = image[offset:offset+BLOCK], result[offset:offset+BLOCK]
        if offset//BLOCK == root_block:
            within = entry_at % BLOCK
            before = before[:within]+bytes(32)+before[within+32:]
            after = after[:within]+bytes(32)+after[within+32:]
        if before != after or original.next(c) != verified.next(c):
            raise ValueError('candidate changes an unrelated allocated cluster')
    for b in range(len(image)//BLOCK):
        if b not in allowed and image[b*BLOCK:(b+1)*BLOCK] != result[b*BLOCK:(b+1)*BLOCK]:
            raise ValueError('candidate changes bytes outside declared updates')
    return bytes(result), dict(esp_blocks=len(image)//BLOCK, file_relative_block=first,
        file_blocks=blocks, token_hex=token.hex(), relocated=bool(previous_chain and start != previous_chain[0]),
        existing_log_preserved=bool(previous_chain), input_sha256=hashlib.sha256(image).hexdigest(),
        output_sha256=hashlib.sha256(result).hexdigest(), unrelated_allocations_preserved=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--esp-image',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--boot-config',type=Path,required=True)
    p.add_argument('--output-config',type=Path,required=True)
    p.add_argument('--token',help='optional nonzero allocation identity, exactly32 hex')
    a=p.parse_args()
    if a.output.exists() or a.output_config.exists():
        p.error('output paths must be new files')
    token=bytes.fromhex(a.token) if a.token else None
    image, report=prepare(a.esp_image.read_bytes(),token)
    config=enable_config(a.boot_config.read_text(),bytes.fromhex(report['token_hex']))
    a.output.write_bytes(image)
    a.output_config.write_text(config)
    report['boot_config_sha256']=hashlib.sha256(config.encode()).hexdigest()
    report['deployed']=False
    a.output.with_suffix(a.output.suffix+'.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()

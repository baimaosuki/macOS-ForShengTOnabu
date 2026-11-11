"""Exercise real FAT32 allocation and reject geometry/identity mistakes."""
from pathlib import Path
import struct
import sys
import tomllib
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_nabu_esp_log import prepare, enable_config, volume
from nabu_log_volume import BLOCK, FILE_BYTES, NAME, make_config


def esp():
    blocks, fat_blocks, reserved = 65525+32+2*65, 65, 32
    image=bytearray(blocks*BLOCK)
    struct.pack_into('<HBHB', image, 11, BLOCK, 1, reserved, 2)
    struct.pack_into('<II', image, 32, blocks, fat_blocks)
    struct.pack_into('<I', image, 44, 2)
    image[82:87]=b'FAT32';image[510:512]=b'\x55\xaa'
    for i in range(2):
        fat=(reserved+i*fat_blocks)*BLOCK
        struct.pack_into('<4I',image,fat,0x0ffffff8,0x0fffffff,0x0fffffff,0x0fffffff)
    data=(reserved+2*fat_blocks)*BLOCK
    image[data:data+11]=b'KEEP    BIN';image[data+11]=0x20
    struct.pack_into('<HI',image,data+26,3,7)
    image[data+BLOCK:data+BLOCK+7]=b'KEEP-ME'
    return image


class PrepareLog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.source=esp()

    def test_prepare_idempotent_and_preserves_existing_file(self):
        token=bytes(range(1,17))
        candidate, info=prepare(self.source,token)
        v=volume(candidate);self.assertEqual(v.log_extent()[2],token)
        self.assertEqual(candidate[(v.data_first+1)*BLOCK:(v.data_first+1)*BLOCK+7],b'KEEP-ME')
        same, second=prepare(candidate)
        self.assertEqual(same,candidate)
        self.assertTrue(second['existing_log_preserved'])
        with self.assertRaisesRegex(ValueError,'token differs'):
            prepare(candidate,b'x'*16)

    def test_fragmented_previous_file_relocated_and_keeps_payload(self):
        candidate,_=prepare(self.source,b'x'*16)
        original=volume(candidate)
        start,_,_=original.log_extent()
        first=start-original.data_first+2
        changed=bytearray(candidate)
        last=first+FILE_BYTES//BLOCK-1
        replacement=last+5
        # Move one terminal data block elsewhere and update both FATs.
        for copy in range(2):
            fat=(original.fat_first+copy*original.fat_blocks)*BLOCK
            struct.pack_into('<I',changed,fat+4*(last-1),replacement)
            struct.pack_into('<I',changed,fat+4*last,0)
            struct.pack_into('<I',changed,fat+4*replacement,0x0fffffff)
        at=(original.data_first+replacement-2)*BLOCK
        changed[at:at+BLOCK]=b'z'*BLOCK
        fixed,info=prepare(changed)
        self.assertTrue(info['relocated'])
        v=volume(fixed);at=v.log_extent()[0]*BLOCK
        self.assertEqual(fixed[at+FILE_BYTES-BLOCK:at+FILE_BYTES],b'z'*BLOCK)

    def test_mirror_and_geometry_and_crosslink_refused(self):
        damaged=bytearray(self.source);damaged[32*BLOCK+20]=7
        with self.assertRaisesRegex(ValueError,'mirrors'):prepare(damaged)
        damaged=bytearray(self.source);struct.pack_into('<I',damaged,32,len(damaged)//BLOCK-1)
        with self.assertRaisesRegex(ValueError,'malformed'):prepare(damaged)
        damaged=bytearray(self.source);v=volume(damaged);at=v.data_first*BLOCK+32
        damaged[at:at+11]=b'ALIAS   BIN';damaged[at+11]=0x20
        struct.pack_into('<HI',damaged,at+26,3,7)
        with self.assertRaisesRegex(ValueError,'cross-linked'):prepare(damaged)

    def test_config_only_changes_default_args_and_respects_loader_limit(self):
        text='version = 1\n[boot]\ndefault="main"\n[[entry]]\nid="main"\ntype="xnu"\nargs="-v cpus=1 nabu-log=0"\n[[entry]]\nid="other"\ntype="xnu"\nargs="-v"\n'
        patched=enable_config(text,b'x'*16)
        d=tomllib.loads(patched)
        self.assertEqual(d['entry'][1]['args'],'-v')
        self.assertIn('cpus=1 nabu-log=1 nabu-log-token=',d['entry'][0]['args'])
        self.assertEqual(enable_config(patched,b'x'*16),patched)
        with self.assertRaisesRegex(ValueError,'1023'):
            enable_config(text.replace('-v cpus=1 nabu-log=0','z'*1000),b'x'*16)


if __name__=='__main__':unittest.main()

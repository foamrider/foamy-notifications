"""Bounded external sender-image normalization and legacy popup migration, run as a process."""
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import time
import unittest

from helperproc import Sandbox, from_uri

IMAGE_LIMIT = 5 * 1024 * 1024
IMAGE_NAME = re.compile(r"^([0-9]+-[0-9]+)-(appIcon|image)-([a-f0-9]{64})\.png$")


def dimensions(path):
    data = Path(path).read_bytes()
    assert data[:16] == b'\x89PNG\r\n\x1a\n\0\0\0\rIHDR', data[:16]
    return struct.unpack('!II', data[16:24])


@unittest.skipUnless(shutil.which('magick') and shutil.which('file'), 'needs ImageMagick 7 and file')
class Images(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox(tools=True)
        self.addCleanup(self.box.cleanup)
        self.root = self.box.root / 'sources'
        self.root.mkdir()

    def image(self, name='input.png', *args):
        path = self.root / name
        subprocess.run(['magick', *(args or ('-size', '1440x900', 'xc:steelblue')), str(path)], check=True, timeout=5)
        return path

    def normalize(self, path, role='image', key='1000-1', local=True):
        """Writes a popup whose `role` is `path`; returns the stored image path, or None if rejected.

        A rejected local file is reported on stderr; other values are dropped quietly."""
        stamp, ident = key.split('-')
        entry = {'timestamp': int(stamp), 'originalId': int(ident), 'summary': 'Picture', role: str(path)}
        result = self.box.run('write', {'entry': entry}, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = self.box.saved(key)[role]
        if not value:
            self.assertEqual(b'foamy.notifications: ' + role.encode() + b' rejected: ' in result.stderr, local, result.stderr)
            return None
        self.assertTrue(value.startswith('file:///'), value)
        return from_uri(value)

    def test_supported_formats_and_transparency(self):
        args = ('-size', '64x64', 'xc:none', '-fill', 'red', '-draw', 'circle 32,32 32,12')
        for ext in ['png', 'jpg', 'gif', 'webp', 'bmp', 'ico', 'svg']:
            with self.subTest(ext=ext):
                path = self.image('icon.' + ext, *args)
                result = self.normalize(path, 'appIcon')
                self.assertTrue(IMAGE_NAME.fullmatch(result.name), result)
                self.assertEqual(result.parent, self.box.images)
                self.assertEqual(dimensions(result), (64, 64))
                self.assertEqual(result.stat().st_mode & 0o777, 0o600)
        progressive = self.image('progressive.jpg', '-size', '1440x900', 'xc:blue', '-interlace', 'Plane')
        result = self.normalize(progressive)
        self.assertEqual(dimensions(result), (720, 450))
        transparent = self.normalize(self.image('transparent.png', *args))
        alpha = subprocess.check_output(['magick', str(transparent), '-format', '%[pixel:p{0,0}]', 'info:'], text=True)
        self.assertIn(',0)', alpha)
        self.assertEqual([p.name for p in self.box.images.iterdir() if p.name.startswith('.')], [])

    def test_file_uris_are_decoded_and_must_be_local(self):
        path = self.image('with space%.png', '-size', '8x8', 'xc:red')
        self.assertEqual(dimensions(self.normalize(path.as_uri())), (8, 8))
        for value in ['file://host' + str(path), path.as_uri() + '?x=1', path.as_uri() + '#f', 'relative.png', 'https://example.com/a.png']:
            with self.subTest(value=value):
                self.assertIsNone(self.normalize(value, local=False))

    def test_text_updates_cache_reuse_and_replacement_url(self):
        source = self.image()
        first = self.normalize(source)
        before = first.stat()
        # Without the decoders on PATH a second decode would fail.
        self.box.path = self.box.path[:1]
        again = self.normalize(source)
        self.assertEqual(again, first)
        self.assertEqual(again.stat().st_ino, before.st_ino)
        self.assertEqual(again.stat().st_mtime_ns, before.st_mtime_ns)
        self.box.tools()
        self.image('input.png', '-size', '1440x900', 'xc:red')
        second = self.normalize(source)
        self.assertNotEqual(first, second)
        self.assertFalse(first.exists())
        self.assertEqual(self.normalize(second), second)
        other = self.normalize(second, key='1000-2')
        self.assertTrue(other.name.startswith('1000-2-image-'))
        self.assertTrue(second.exists())

    def test_invalid_oversized_and_progressive_allocation(self):
        corrupt = self.image('corrupt.png')
        corrupt.write_bytes(corrupt.read_bytes()[:40])
        huge = self.image('large.png', '-size', '9000x1', 'xc:red')
        oversized = self.image('bytes.png')
        oversized.write_bytes(b'x' * (IMAGE_LIMIT + 1))
        jpeg = self.image('bomb.jpg', '-size', '8x8', 'xc:red', '-interlace', 'Plane')
        data = bytearray(jpeg.read_bytes())
        frame = data.index(b'\xff\xc2')
        data[frame + 5:frame + 9] = (60000).to_bytes(2, 'big') * 2
        jpeg.write_bytes(data)
        text = self.root / 'notes.txt'
        text.write_text('hello')
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        symlink = self.root / 'symlink'
        symlink.symlink_to(self.image('fine.png', '-size', '8x8', 'xc:red'))
        for path in [corrupt, huge, oversized, jpeg, text, fifo, symlink, self.root, self.root / 'missing']:
            with self.subTest(path=path.name):
                self.assertIsNone(self.normalize(path))
        self.assertFalse(list(self.box.images.glob('*.png')))
        self.assertFalse(list(self.box.images.glob('.foamy-*')))

    def test_svg_external_resources_are_not_loaded(self):
        linked = self.image('external.png', '-size', '8x8', 'xc:red')
        for url in ['../external.png', str(linked), linked.as_uri()]:
            with self.subTest(url=url):
                svg = self.root / 'linked.svg'
                svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="8" height="8"><image width="8" height="8" xlink:href="' + url + '"/></svg>')
                result = self.normalize(svg)
                pixel = subprocess.check_output(['magick', str(result), '-format', '%[pixel:p{0,0}]', 'info:'], text=True)
                self.assertIn(',0)', pixel)

    def test_decoder_timeout_failure_and_os_memory_limit(self):
        path = self.image()
        cli = self.box.root / 'fake'
        cli.mkdir()
        mock = cli / 'magick'
        self.box.path.insert(0, str(cli))
        for code in ['import time;time.sleep(20)',
                     'import sys;sys.exit(1)',
                     'data=bytearray(600*1024*1024)',
                     'import sys;open(sys.argv[-1][4:],"wb").write(b"partial")',
                     'import os,sys;assert os.environ["MAGICK_CONFIGURE_PATH"].endswith("/bin");sys.exit(3)']:
            with self.subTest(code=code):
                mock.write_text('#!/usr/bin/python3\n' + code + '\n')
                mock.chmod(0o700)
                started = time.monotonic()
                self.assertIsNone(self.normalize(path))
                self.assertLess(time.monotonic() - started, 7)
                self.assertFalse(list(self.box.images.glob('*.png')))
                self.assertFalse(list(self.box.images.glob('.foamy-image-*')))

    def test_decoder_gets_the_policy_limits_and_an_empty_directory(self):
        path = self.root / 'icon.svg'
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="red"/></svg>')
        cli = self.box.root / 'fake'
        cli.mkdir()
        report = self.box.root / 'report.json'
        (cli / 'magick').write_text('#!/usr/bin/python3\nimport json,os,resource,sys\n'
                                    'json.dump({"argv":sys.argv[1:],"cwd":os.getcwd(),"files":os.listdir("."),"stdin":sys.stdin.buffer.read(8).decode("latin-1"),'
                                    '"policy":os.environ.get("MAGICK_CONFIGURE_PATH"),'
                                    '"limits":[resource.getrlimit(r) for r in (resource.RLIMIT_CORE,resource.RLIMIT_AS,resource.RLIMIT_FSIZE,resource.RLIMIT_CPU)]},'
                                    'open(%r,"w"))\nsys.exit(1)\n' % str(report))
        (cli / 'magick').chmod(0o700)
        self.box.path.insert(0, str(cli))
        self.assertIsNone(self.normalize(path))
        seen = json.loads(report.read_text())
        self.assertEqual(seen['argv'][:3], ['-background', 'none', 'RSVG:-[0]'])
        self.assertEqual(seen['argv'][3:7], ['-auto-orient', '-resize', '720x720>', '-strip'])
        self.assertTrue(seen['argv'][7].startswith('PNG:' + str(self.box.images) + '/.foamy-image-'))
        self.assertTrue(seen['argv'][7].endswith('/output.png'))
        self.assertEqual(seen['cwd'], seen['argv'][7][4:-len('output.png')] + 'empty')
        self.assertEqual(seen['files'], [])
        self.assertEqual(seen['stdin'], path.read_bytes()[:8].decode('latin-1'))
        self.assertTrue(Path(seen['policy'], 'policy.xml').is_file())
        self.assertEqual(seen['limits'], [[0, 0], [512 * 1024 * 1024] * 2, [IMAGE_LIMIT] * 2, [5, 5]])
        png = self.image('icon.png', '-size', '8x8', 'xc:red')
        self.assertIsNone(self.normalize(png))
        seen = json.loads(report.read_text())
        self.assertTrue(seen['argv'][2].startswith('PNG:' + str(self.box.images) + '/.foamy-image-'))
        self.assertTrue(seen['argv'][2].endswith('/input[0]'))
        self.assertEqual(seen['stdin'], '')

    def test_write_restore_migration_rejection_and_cleanup(self):
        source = self.image()
        row = {'timestamp': 1000, 'originalId': 1, 'summary': 'Picture', 'image': source.as_uri(), 'appIcon': 'foot', 'actionsJson': '[]'}
        out = json.loads(self.box.write(row, sources={'image': row['image'], 'appIcon': 'foot'}))
        saved = self.box.saved('1000-1')
        self.assertEqual(out['image'], saved['image'])
        self.assertNotIn('actionsJson', saved)
        self.assertTrue(saved['image'].endswith('.png'))
        self.assertEqual(saved['appIcon'], 'foot')
        source.unlink()
        self.assertEqual(json.loads(self.box.ok('read', {}))['image'], saved['image'])
        legacy = self.image('legacy.jpg', '-size', '64x64', 'xc:red', '-interlace', 'Plane')
        (self.box.popups / '1001-2.json').write_text(json.dumps({'timestamp': 1001, 'originalId': 2, 'summary': 'Legacy', 'image': str(legacy)}))
        self.box.ok('read', {})
        migrated = self.box.saved('1001-2')
        self.assertEqual(migrated['imageFormat'], 1)
        self.assertIn('-image-', migrated['image'])
        self.assertNotIn('legacy.jpg', migrated['image'])
        self.box.write({'timestamp': 1002, 'originalId': 3, 'summary': 'Text retained', 'image': str(self.root / 'missing'), 'appIcon': 'image://unsafe'})
        rejected = self.box.saved('1002-3')
        self.assertEqual(rejected['image'], '')
        self.assertEqual(rejected['appIcon'], '')
        self.assertEqual(rejected['summary'], 'Text retained')
        self.box.ok('forget', {'keys': ['1000-1', '1001-2', '1002-3']})
        self.assertFalse(list(self.box.images.glob('*.png')))

    def test_copied_sender_files_are_converted_from_their_source(self):
        source = self.image('sender.png', '-size', '16x16', 'xc:red')
        destination = self.box.images / '1000-1-image'
        row = {'timestamp': 1000, 'originalId': 1, 'summary': 'Copied', 'image': 'file://' + str(destination)}
        self.box.write(row, copies=[{'from': str(source), 'to': str(destination)}])
        result = from_uri(self.box.saved('1000-1')['image'])
        self.assertEqual(dimensions(result), (16, 16))
        self.assertTrue(IMAGE_NAME.fullmatch(result.name))

    def test_absent_decoder_keeps_text_without_unsafe_image(self):
        source = self.image()
        self.box.path = self.box.path[:1]
        self.assertIsNone(self.normalize(source))
        self.assertEqual(self.box.saved('1000-1')['summary'], 'Picture')


if __name__ == '__main__':
    unittest.main()

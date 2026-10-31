"""Bounded external sender-image normalization and legacy popup migration."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('notification_image_helper', ROOT/'bin/notification-helper.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


class Images(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root/'state')})
        self.env.start()
        self.addCleanup(self.env.stop)

    def image(self, name='input.png', *args):
        path = self.root/name
        subprocess.run(['magick', *(args or ('-size', '1440x900', 'xc:steelblue')), str(path)], check=True, timeout=5)
        return path

    def normalize(self, path, role='image', key='1000-1'):
        return h.normalize_image(path, key, role)

    def test_supported_formats_and_transparency(self):
        for ext in ['png', 'jpg', 'gif', 'webp', 'bmp', 'ico', 'svg']:
            with self.subTest(ext=ext):
                args = ('-size','64x64','xc:none','-fill','red','-draw','circle 32,32 32,12')
                path = self.image('icon.'+ext, *args)
                result = self.normalize(path, 'appIcon')
                self.assertTrue(h.IMAGE_NAME.fullmatch(result.name))
                self.assertEqual(h.png_dimensions(result.read_bytes()), (64,64))
                self.assertEqual(result.stat().st_mode & 0o777, 0o600)
        progressive = self.image('progressive.jpg', '-size','1440x900','xc:blue','-interlace','Plane')
        result = self.normalize(progressive)
        self.assertEqual(h.png_dimensions(result.read_bytes()),(720,450))
        transparent = self.normalize(self.image('transparent.png', *args))
        alpha = subprocess.check_output(['magick',str(transparent),'-format','%[pixel:p{0,0}]','info:'],text=True)
        self.assertIn(',0)',alpha)

    def test_text_updates_cache_reuse_and_replacement_url(self):
        source = self.image()
        first = self.normalize(source)
        before = first.stat()
        with patch.object(h.subprocess, "run", side_effect=AssertionError("Unchanged source decoded again")):
            again = self.normalize(source)
        self.assertEqual(again,first)
        self.assertEqual(again.stat().st_ino,before.st_ino)
        self.assertEqual(again.stat().st_mtime_ns,before.st_mtime_ns)
        self.image('input.png','-size','1440x900','xc:red')
        second = self.normalize(source)
        self.assertNotEqual(first,second)
        self.assertFalse(first.exists())
        self.assertEqual(self.normalize(second),second)
        other = self.normalize(second,key='1000-2')
        self.assertTrue(other.name.startswith('1000-2-image-'))

    def test_invalid_oversized_and_progressive_allocation(self):
        corrupt = self.image('corrupt.png');corrupt.write_bytes(corrupt.read_bytes()[:40])
        huge = self.image('large.png','-size','9000x1','xc:red')
        oversized = self.image('bytes.png');oversized.write_bytes(b'x'*(h.IMAGE_LIMIT+1))
        jpeg = self.image('bomb.jpg','-size','8x8','xc:red','-interlace','Plane')
        data = bytearray(jpeg.read_bytes());frame=data.index(b'\xff\xc2');data[frame+5:frame+9]=(60000).to_bytes(2,'big')*2;jpeg.write_bytes(data)
        fifo = self.root/'fifo';os.mkfifo(fifo)
        symlink = self.root/'symlink';symlink.symlink_to(corrupt)
        for path in [corrupt,huge,oversized,jpeg,fifo,symlink]:
            with self.subTest(path=path.name), self.assertRaises((ValueError,OSError,subprocess.SubprocessError)):
                self.normalize(path)
        self.assertFalse(list((h.base()/'images').glob('*.png')))

    def test_svg_external_resources_are_not_loaded(self):
        linked = self.image('external.png','-size','8x8','xc:red')
        for url in ['../external.png',str(linked),linked.as_uri()]:
            svg = self.root/'linked.svg'
            svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="8" height="8"><image width="8" height="8" xlink:href="'+url+'"/></svg>')
            result = self.normalize(svg)
            pixel = subprocess.check_output(['magick',str(result),'-format','%[pixel:p{0,0}]','info:'],text=True)
            self.assertIn(',0)',pixel)

    def test_decoder_timeout_failure_and_os_memory_limit(self):
        path = self.image()
        cli = self.root/'bin';cli.mkdir()
        mock = cli/'magick';mock.chmod(0o700) if mock.exists() else None
        for code in ['import time;time.sleep(20)',
                     'import sys;sys.exit(1)',
                     'data=bytearray(600*1024*1024)',
                     'import sys;open(sys.argv[-1][4:],"wb").write(b"partial")']:
            mock.write_text('#!/usr/bin/python3\n'+code+'\n');mock.chmod(0o700)
            started=time.monotonic()
            with patch.dict(os.environ,{'PATH':str(cli)+':'+os.environ['PATH']}),self.assertRaises((ValueError,subprocess.SubprocessError)):
                self.normalize(path)
            self.assertLess(time.monotonic()-started,7)
            self.assertFalse(list((h.base()/'images').glob('*.png')))
            self.assertFalse(list((h.base()/'images').glob('.foamy-image-*')))

    def test_write_restore_migration_rejection_and_cleanup(self):
        source = self.image()
        row = {'timestamp':1000,'originalId':1,'summary':'Picture','image':source.as_uri(),'appIcon':'foot','actionsJson':'[]'}
        with contextlib.redirect_stdout(io.StringIO()):h.write({'entry':row,'sources':{'image':row['image'],'appIcon':'foot'}})
        saved = json.loads((h.popups()/'1000-1.json').read_text())
        self.assertNotIn('actionsJson',saved)
        self.assertTrue(saved['image'].endswith('.png'))
        self.assertEqual(saved['appIcon'],'foot')
        source.unlink()
        output=io.StringIO()
        with contextlib.redirect_stdout(output):h.read({})
        self.assertEqual(json.loads(output.getvalue())['image'],saved['image'])
        legacy = self.image('legacy.jpg','-size','64x64','xc:red','-interlace','Plane')
        h.atomic(h.popups()/'1001-2.json',json.dumps({'timestamp':1001,'originalId':2,'summary':'Legacy','image':str(legacy)}).encode())
        with contextlib.redirect_stdout(io.StringIO()):h.read({})
        migrated=json.loads((h.popups()/'1001-2.json').read_text());self.assertEqual(migrated['imageFormat'],1)
        self.assertIn('-image-',migrated['image']);self.assertNotIn('legacy.jpg',migrated['image'])
        with contextlib.redirect_stderr(io.StringIO()):
            h.write({'entry':{'timestamp':1002,'originalId':3,'summary':'Text retained','image':str(self.root/'missing'),'appIcon':'image://unsafe'}})
        rejected=json.loads((h.popups()/'1002-3.json').read_text())
        self.assertEqual(rejected['image'],'');self.assertEqual(rejected['appIcon'],'');self.assertEqual(rejected['summary'],'Text retained')
        h.forget({'keys':['1000-1','1001-2','1002-3']})
        self.assertFalse(list((h.base()/'images').glob('*.png')))

    def test_absent_decoder_keeps_text_without_unsafe_image(self):
        source=self.image()
        row={'timestamp':1000,'originalId':1,'summary':'Useful text','image':str(source)}
        with patch.object(h.shutil,'which',return_value=None),contextlib.redirect_stderr(io.StringIO()):h.write({'entry':row})
        saved=json.loads((h.popups()/'1000-1.json').read_text());self.assertEqual(saved['image'],'');self.assertEqual(saved['summary'],'Useful text')


if __name__=='__main__':unittest.main()

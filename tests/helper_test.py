import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('helper',ROOT/'bin/notification-helper.py')
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)

class HelperTest(unittest.TestCase):
    def test_browser_identity_is_exact_and_profiles_are_not_guessed(self):
        data={'app':'Vivaldi','body':'<a href="https://teams.microsoft.com/">teams.microsoft.com</a>\nAlex: Hi'}
        clients=[{'class':'vivaldi','address':'0x10'},{'class':'vivaldi-teams.microsoft.com__-Profile_1','address':'0x11'}]
        self.assertEqual(h.select_window(data,clients),'0x11')
        clients.append({'class':'vivaldi-teams.microsoft.com__-Profile_2','address':'0x12'})
        with self.assertRaises(ValueError): h.select_window(data,clients)
        data['mappings']=[{'origin':'teams.microsoft.com','windowClass':'vivaldi-teams.microsoft.com__-Profile_2'}]
        self.assertEqual(h.select_window(data,clients),'0x12')
        self.assertEqual(h.origin_of('Alex says visit https://evil.invalid'), '')
        self.assertEqual(h.origin_of('<a href="javascript:alert(1)">x</a>'), '')

    def test_sender_is_not_a_regex_or_dispatch_expression(self):
        with self.assertRaises(ValueError): h.select_window({'app':'.*'},[{'class':'anything','address':'0x1'}])
        with self.assertRaises(ValueError): h.select_window({'app':'Chat'},[{'class':'Chat','address':'0x1;evil'}])

    def test_bare_browser_origin_selects_teams_instead_of_browser(self):
        data={'app':'Vivaldi','desktopEntry':'vivaldi-stable','body':'teams.microsoft.com\nAlex: Hi'}
        clients=[{'class':'vivaldi-stable','address':'0x10'},
                 {'class':'vivaldi-teams.microsoft.com__-Profile_1','address':'0x11'}]
        self.assertEqual(h.select_window(data,clients),'0x11')
        # A recognized web app must not fall back to the general browser if absent.
        with self.assertRaises(ValueError): h.select_window(data,clients[:1])
        clients.append({'class':'vivaldi-teams.microsoft.com__-Profile_2','address':'0x12'})
        with self.assertRaises(ValueError): h.select_window(data,clients)
        data['mappings']=[{'origin':'teams.microsoft.com','windowClass':'vivaldi-teams.microsoft.com__-Profile_2'}]
        self.assertEqual(h.select_window(data,clients),'0x12')

    def test_bare_origin_requires_a_complete_hostname_line(self):
        for body in ['teams.microsoft.com', '  TEAMS.MICROSOFT.COM\r\nAlex: Hi', 'teams.cloud.microsoft\nMessage']:
            with self.subTest(body=body): self.assertEqual(h.origin_of(body),body.strip().splitlines()[0].lower())
        for body in ['Alex says visit teams.microsoft.com', 'teams.microsoft.com is mentioned here',
                     'Message\nteams.microsoft.com', 'teams.microsoft.com/path',
                     'teams.microsoft.com@evil.invalid', '-teams.microsoft.com',
                     'teams..microsoft.com', 'localhost', 'a'*64+'.com']:
            with self.subTest(body=body): self.assertEqual(h.origin_of(body),'')

    def test_non_browser_message_hostname_is_not_app_identity(self):
        clients=[{'class':'Chat','address':'0x10'},{'class':'vivaldi-teams.microsoft.com__-Profile_1','address':'0x11'}]
        self.assertEqual(h.select_window({'app':'Chat','body':'teams.microsoft.com\nMessage'},clients),'0x10')

    def test_atomic_private_persistence_and_handled_removal(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{'XDG_STATE_HOME':d,'XDG_CONFIG_HOME':d+'/config'}):
            row={'timestamp':1000,'originalId':3,'summary':'Hello','body':'$(touch nope)'}
            h.write({'entry':dict(row, actionsJson='[{"identifier":"reply"}]')})
            path=h.base()/'1000-3.json'
            self.assertEqual(json.loads(path.read_text()),row)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            h.forget({'keys':['1000-3']})
            self.assertFalse(path.exists())
            with self.assertRaises(ValueError):h.forget({'keys':['../secrets']})

    def test_fifo_image_cannot_block_storage(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{'XDG_STATE_HOME':d}):
            fifo=Path(d)/'fifo';os.mkfifo(fifo)
            destination=h.base()/'images/1000-3-appIcon'
            row={'timestamp':1000,'originalId':3,'summary':'Hello','appIcon':'file://'+str(destination)}
            result=subprocess.run(['python3',str(ROOT/'bin/notification-helper.py'),'write'],input=json.dumps({'entry':row,'copies':[{'from':str(fifo),'to':str(destination)}]}),text=True,capture_output=True,timeout=2)
            self.assertEqual(result.returncode,0,result.stderr)
            saved=json.loads((h.base()/'1000-3.json').read_text());self.assertEqual(saved['appIcon'],'')

    def test_corrupt_and_oversized_restores_are_skipped(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{'XDG_STATE_HOME':d}):
            root=h.base();root.mkdir(parents=True)
            (root/'1000-1.json').write_text('')
            (root/'1000-2.json').write_text('x'*(h.LIMIT+1))
            h.write({'entry':{'timestamp':1000,'originalId':3,'summary':'Good'}})
            result=subprocess.run(['python3',str(ROOT/'bin/notification-helper.py'),'read'],input='{}',text=True,capture_output=True,timeout=2)
            self.assertEqual(len(result.stdout.splitlines()),1)
            self.assertEqual(json.loads(result.stdout)['summary'],'Good')

    def test_unchanged_image_copy_preserves_inode_and_mtime(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'src';dest=Path(d)/'dest';src.write_bytes(b'image')
            h.copy_image(src,dest);before=dest.stat()
            h.copy_image(src,dest);self.assertEqual(dest.stat().st_ino,before.st_ino);self.assertEqual(dest.stat().st_mtime_ns,before.st_mtime_ns)
            src.write_bytes(b'updated image');h.copy_image(src,dest);self.assertEqual(dest.read_bytes(),b'updated image')

    def test_center_focus_uses_current_browser_mapping_and_ignores_commands(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{'XDG_CONFIG_HOME':d}), patch.object(h,'focus') as focus:
            folder=Path(d)/'omarchy';folder.mkdir()
            mapping={'origin':'teams.microsoft.com','windowClass':'vivaldi-teams.microsoft.com__-Profile_2'}
            (folder/'shell.json').write_text(json.dumps({'plugins':[{'id':'foamy.notifications','browserMappings':[mapping]}]}))
            h.focus_configured({'app':'Vivaldi','body':'teams.microsoft.com','execArgv':'["ignored"]'})
            self.assertEqual(focus.call_args.args[0]['mappings'],[mapping]);self.assertNotIn('execArgv',focus.call_args.args[0])

if __name__=='__main__':unittest.main()

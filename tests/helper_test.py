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
            path=h.popups()/'1000-3.json'
            self.assertEqual(json.loads(path.read_text()),dict(row, appIcon="", image="", imageFormat=1))
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            h.forget({'keys':['1000-3']})
            self.assertFalse(path.exists())
            # The center reads this journal; Foamy never runs the center's scripts.
            self.assertEqual((h.base()/'handled').read_text().split(),['1000-3'])
            h.forget({'keys':['1000-4','1000-3']})
            self.assertEqual((h.base()/'handled').read_text().split(),['1000-4','1000-3'])
            with self.assertRaises(ValueError):h.forget({'keys':['../secrets']})

    def test_fifo_image_cannot_block_storage(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{'XDG_STATE_HOME':d}):
            fifo=Path(d)/'fifo';os.mkfifo(fifo)
            destination=h.base()/'images/1000-3-appIcon'
            row={'timestamp':1000,'originalId':3,'summary':'Hello','appIcon':'file://'+str(destination)}
            result=subprocess.run(['python3',str(ROOT/'bin/notification-helper.py'),'write'],input=json.dumps({'entry':row,'copies':[{'from':str(fifo),'to':str(destination)}]}),text=True,capture_output=True,timeout=2)
            self.assertEqual(result.returncode,0,result.stderr)
            saved=json.loads((h.popups()/'1000-3.json').read_text());self.assertEqual(saved['appIcon'],'')

    def test_corrupt_and_oversized_restores_are_skipped(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{'XDG_STATE_HOME':d}):
            root=h.popups();root.mkdir(parents=True)
            (root/'1000-1.json').write_text('')
            (root/'1000-2.json').write_text('x'*(h.LIMIT+1))
            h.write({'entry':{'timestamp':1000,'originalId':3,'summary':'Good'}})
            result=subprocess.run(['python3',str(ROOT/'bin/notification-helper.py'),'read'],input='{}',text=True,capture_output=True,timeout=2)
            self.assertEqual(len(result.stdout.splitlines()),1)
            self.assertEqual(json.loads(result.stdout)['summary'],'Good')

    def test_center_focus_uses_current_browser_mapping_and_ignores_commands(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{'XDG_CONFIG_HOME':d}), patch.object(h,'focus') as focus:
            folder=Path(d)/'omarchy';folder.mkdir()
            mapping={'origin':'teams.microsoft.com','windowClass':'vivaldi-teams.microsoft.com__-Profile_2'}
            (folder/'shell.json').write_text(json.dumps({'plugins':[{'id':'foamy.notifications','browserMappings':[mapping]}]}))
            h.focus_configured({'app':'Vivaldi','body':'teams.microsoft.com','execArgv':'["ignored"]'})
            self.assertEqual(focus.call_args.args[0]['mappings'],[mapping]);self.assertNotIn('execArgv',focus.call_args.args[0])

    def test_one_time_migration_from_the_4_0_layout(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{'XDG_STATE_HOME':d}):
            old=Path(d)/'omarchy/notifications';(old/'history').mkdir(parents=True);(old/'images').mkdir()
            image='1000-1-image-'+'a'*64+'.png'
            (old/'images'/image).write_bytes(b'png')
            (old/'1000-1.json').write_text(json.dumps({'timestamp':1000,'originalId':1,'summary':'Live','image':(old/'images'/image).as_uri()}))
            (old/'history/900-2.json').write_text(json.dumps({'timestamp':900,'originalId':2,'summary':'Old'}))
            for stock in ('live.json','history.json','quiet.json'): (old/stock).write_text('[]')
            (Path(d)/'omarchy/notifications.json').write_text(json.dumps({'dnd':True,'snoozes':{}}))
            h.migrate({})
            live=json.loads((h.popups()/'1000-1.json').read_text())
            self.assertEqual(live['image'],(h.base()/'images'/image).as_uri())
            self.assertTrue((h.history()/'900-2.json').exists())
            self.assertFalse((old/'1000-1.json').exists())
            for stock in ('live.json','history.json','quiet.json'): self.assertTrue((old/stock).exists())
            self.assertTrue(json.loads((h.base()/'state.json').read_text())['dnd'])
            self.assertEqual(h.base().stat().st_mode & 0o777,0o700)
            # Once only: later 4.0-layout files belong to whoever wrote them (stock 4.0).
            (old/'1100-3.json').write_text(json.dumps({'timestamp':1100,'originalId':3,'summary':'Stock'}))
            h.migrate({});self.assertTrue((old/'1100-3.json').exists())

    def test_migration_reads_dnd_from_stock_quiet_after_the_4_5_migration(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{'XDG_STATE_HOME':d}):
            old=Path(d)/'omarchy/notifications';old.mkdir(parents=True)
            (old/'quiet.json').write_text(json.dumps({'dnd':True,'snoozes':{}}))
            h.migrate({});self.assertTrue(json.loads((h.base()/'state.json').read_text())['dnd'])

    def test_serve_answers_each_job_and_drop_removes_own_copies(self):
        with tempfile.TemporaryDirectory() as d:
            env=dict(os.environ,XDG_STATE_HOME=d)
            jobs=[{'id':1,'op':'write','data':{'entry':{'timestamp':10,'originalId':1,'summary':'A'},'history':True}},
                  {'id':2,'op':'write','data':{'entry':{'timestamp':20,'originalId':2,'summary':'B'}}},
                  {'id':3,'op':'drop','data':{'before':15}},{'id':4,'op':'archive','data':{'key':'20-2'}},
                  {'id':5,'op':'read','data':{'history':True}},{'id':6,'op':'unknown','data':{}}]
            result=subprocess.run(['python3','-I',str(ROOT/'bin/notification-helper.py'),'serve'],input=''.join(json.dumps(j)+'\n' for j in jobs),text=True,capture_output=True,timeout=5,env=env)
            replies=[json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual([r['id'] for r in replies],[1,2,3,4,5,6])
            self.assertEqual([r['ok'] for r in replies],[True,True,True,True,True,False])
            self.assertEqual([json.loads(l)['summary'] for l in replies[4]['out'].splitlines()],['B'])

    def test_history_write_removes_the_stale_popup_copy(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{'XDG_STATE_HOME':d}):
            h.private_dirs()
            image=h.base()/'images'/('5-5-image-'+'a'*64+'.png');image.write_bytes(b'png')
            h.write({'entry':{'timestamp':5,'originalId':5,'summary':'Shown'}})
            self.assertTrue((h.popups()/'5-5.json').exists())
            # A queued update converted into a history write after the popup was dismissed.
            h.write({'entry':{'timestamp':5,'originalId':5,'summary':'Updated'},'history':True})
            self.assertFalse((h.popups()/'5-5.json').exists())
            self.assertEqual(json.loads((h.history()/'5-5.json').read_text())['summary'],'Updated')
            self.assertTrue(image.exists())

    def test_migration_moves_legacy_unhashed_image_copies(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{'XDG_STATE_HOME':d}):
            old=Path(d)/'omarchy/notifications';(old/'history').mkdir(parents=True);(old/'images').mkdir()
            (old/'images/900-2-image').write_bytes(b'legacy')
            (old/'history/900-2.json').write_text(json.dumps({'timestamp':900,'originalId':2,'summary':'Old','image':(old/'images/900-2-image').as_uri()}))
            h.migrate({})
            moved=h.base()/'images/900-2-image'
            self.assertTrue(moved.exists())
            self.assertEqual(json.loads((h.history()/'900-2.json').read_text())['image'],moved.as_uri())

if __name__=='__main__':unittest.main()

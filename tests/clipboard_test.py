import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HELPER=Path(__file__).resolve().parents[1]/'bin/notification-clipboard'

class ClipboardTest(unittest.TestCase):
    def test_sensitive_copy_bounded_clear_and_failure(self):
        with tempfile.TemporaryDirectory(prefix='foamy-code-clipboard-') as tmp:
            root=Path(tmp)
            for program in ['timeout','head','cat']:
                (root/program).symlink_to('/usr/bin/'+program)
            (root/'wl-copy').write_text('#!/bin/bash\nprintf "%s" "$*" > "$HOME/args"\nif [[ $1 == --clear ]]; then : > "$HOME/value"; else cat > "$HOME/value"; fi\n')
            (root/'wl-paste').write_text('#!/bin/bash\ncat "$HOME/value"\n')
            for program in ['wl-copy','wl-paste']:(root/program).chmod(0o755)
            env=dict(os.environ,HOME=tmp,PATH=tmp)
            def run(mode,code):return subprocess.run(['/usr/bin/bash',str(HELPER),mode],input=code+'\n',env=env,text=True,capture_output=True,timeout=4)
            self.assertEqual(run('copy','482913').returncode,0)
            self.assertEqual((root/'args').read_text(),'--sensitive')
            self.assertEqual((root/'value').read_text(),'482913')
            (root/'value').write_text('new clipboard contents')
            self.assertEqual(run('clear','482913').returncode,0)
            self.assertEqual((root/'value').read_text(),'new clipboard contents')
            (root/'value').write_text('482913');self.assertEqual(run('clear','482913').returncode,0)
            self.assertEqual((root/'args').read_text(),'--clear');self.assertEqual((root/'value').read_text(),'')
            self.assertNotEqual(run('copy','$(touch marker)').returncode,0)
            (root/'wl-copy').unlink();self.assertNotEqual(run('copy','482913').returncode,0)

    def test_clear_leaves_a_longer_clipboard_without_error(self):
        # Clear reads at most 65 bytes. A longer clipboard (text or an image copied after
        # the code) cannot hold the code; wl-paste then fails writing to the closed pipe,
        # by SIGPIPE or, when the shell ignores SIGPIPE as Quickshell does, EPIPE.
        with tempfile.TemporaryDirectory(prefix='foamy-code-clipboard-') as tmp:
            root=Path(tmp)
            for program in ['timeout','head','tr']:
                (root/program).symlink_to('/usr/bin/'+program)
            (root/'wl-copy').write_text('#!/bin/bash\nprintf "%s" "$*" > "$HOME/args"\n')
            (root/'wl-paste').write_text('#!/bin/bash\nif [[ -e $HOME/fail ]]; then exit 1; fi\nhead -c 1048576 /dev/zero | tr "\\0" x\n')
            for program in ['wl-copy','wl-paste']:(root/program).chmod(0o755)
            env=dict(os.environ,HOME=tmp,PATH=tmp)
            for restore in (True,False):
                run=subprocess.run(['/usr/bin/bash',str(HELPER),'clear'],input='482913\n',env=env,text=True,capture_output=True,timeout=4,restore_signals=restore)
                self.assertEqual(run.returncode,0,(restore,run.stderr))
                self.assertFalse((root/'args').exists())
            # A clipboard that cannot be read is still an error.
            (root/'fail').touch()
            run=subprocess.run(['/usr/bin/bash',str(HELPER),'clear'],input='482913\n',env=env,text=True,capture_output=True,timeout=4)
            self.assertNotEqual(run.returncode,0)
            self.assertFalse((root/'args').exists())

if __name__=='__main__':unittest.main()

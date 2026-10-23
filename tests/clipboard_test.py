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

if __name__=='__main__':unittest.main()

"""Project guidance is explicit, reviewable, and preserves user instructions."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location('ffsake_install',ROOT/'scripts/ffsake_install.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

class GuidanceTests(unittest.TestCase):
    def test_host_paths_and_repeat_preserve_existing(self):
        for host,filename in [('claude','CLAUDE.md'),('codex','AGENTS.md'),('opencode','AGENTS.md')]:
            with self.subTest(host=host),tempfile.TemporaryDirectory() as td:
                root=Path(td);path=root/filename;path.write_bytes(b'# existing\r\nkeep me\r\n')
                plan=installer.plan_guidance(host,root);self.assertEqual(len(plan),1)
                self.assertEqual(path.read_bytes(),b'# existing\r\nkeep me\r\n')
                destination,before,after,added=plan[0]
                self.assertEqual(before,path.read_bytes());self.assertTrue(after.startswith(before))
                self.assertIn('Never use a fallback to evade',added[0]['guidance'])
                destination.write_bytes(after);self.assertEqual(installer.plan_guidance(host,root),[])
    def test_opencode_refuses_to_shadow_claude_or_ancestor_rules(self):
        with tempfile.TemporaryDirectory() as td:
            parent=Path(td);root=parent/'project';root.mkdir()
            (parent/'CLAUDE.md').write_text('existing instructions')
            with self.assertRaisesRegex(ValueError, 'shadow'):installer.plan_guidance('opencode',root)
            self.assertFalse((root/'AGENTS.md').exists())
            self.assertEqual((parent/'CLAUDE.md').read_text(),'existing instructions')
    def test_modified_marker_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);path=root/'AGENTS.md';path.write_text(installer.GUIDANCE.replace('When FFSAKE','Changed FFSAKE'))
            with self.assertRaises(ValueError):installer.plan_guidance('codex',root)
    def test_symlink_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'target').write_text('untouched');(root/'AGENTS.md').symlink_to(root/'target')
            with self.assertRaises(ValueError):installer.plan_guidance('codex',root)
            self.assertEqual((root/'target').read_text(),'untouched')
if __name__=='__main__':unittest.main()

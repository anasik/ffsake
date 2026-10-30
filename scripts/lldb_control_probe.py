#!/usr/bin/env python3
"""Independent LLDB CLI control using the same disposable C++ target, no FFSAKE."""
import argparse,json,shutil,subprocess,tempfile
from pathlib import Path
from debug_smoke import ROOT

def main():
 p=argparse.ArgumentParser();p.add_argument('--lldb',required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args()
 with tempfile.TemporaryDirectory(prefix='ffsake-lldb-control-') as td:
  root=Path(td);shutil.copy(ROOT/'tests/fixtures/debug-cpp-list/main.cpp',root/'main.cpp')
  subprocess.run(['c++','-g','-O0','-c',str(root/'main.cpp'),'-o',str(root/'main.o')],check=True)
  subprocess.run(['c++',str(root/'main.o'),'-o',str(root/'demo')],check=True)
  cmd=[a.lldb,'-b','-o','breakpoint set --file main.cpp --line 10','-o','run','-o','frame variable','-o','quit',str(root/'demo')]
  proc=subprocess.run(cmd,text=True,capture_output=True,timeout=35)
  evidence={'command':cmd,'returncode':proc.returncode,'stdout':proc.stdout,'stderr':proc.stderr}
  a.report.write_text(json.dumps(evidence,indent=2));print(json.dumps(evidence,indent=2))
if __name__=='__main__':main()

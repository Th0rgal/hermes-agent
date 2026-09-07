"""A delayed label event must not restart a superseded CI candidate."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


CASES = [
    ("unchanged", 0, True),
    ("changed_before_start", 0, False),
    ("changed_during_wait", 0, False),
    ("head_read_fails", 1, False),
]


def _exercise_label_rerun(tmp_path, scenario, expected_code, reruns):
    gh = tmp_path / "gh"
    gh.write_text(f"#!{sys.executable}\n" + '''
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
root=Path(os.environ['STUB_DIR'])
with (root/'calls').open('a') as stream:
    stream.write(json.dumps(args)+'\\n')
scenario=os.environ['SCENARIO']
if args[0]=='api':
    if scenario=='head_read_fails': sys.exit(1)
    changed=scenario=='changed_before_start' or (scenario=='changed_during_wait' and (root/'waited').exists())
    print('successor' if changed else 'candidate')
elif args[:2]==['run','list']:
    print('123 in_progress')
elif args[:2]==['run','watch']:
    (root/'waited').touch()
elif args[:2]==['run','view']:
    print('completed')
elif args[:2]!=['run','rerun']:
    sys.exit(2)
''')
    gh.chmod(0o755)
    timeout = tmp_path / "timeout"
    timeout.write_text('#!/bin/sh\nshift\nexec "$@"\n')
    timeout.chmod(0o755)
    repo = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ["PATH"],
               STUB_DIR=str(tmp_path), SCENARIO=scenario, REPO="owner/repo",
               HEAD_SHA="candidate", PR_NUMBER="128")
    result = subprocess.run(["bash", str(repo / ".github/scripts/rerun-reviewed-ci.sh")],
                            env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == expected_code, result.stdout + result.stderr
    calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
    rerun_calls = [call for call in calls if call[:2] == ["run", "rerun"]]
    assert rerun_calls == ([["run", "rerun", "123", "--repo", "owner/repo", "--failed"]] if reruns else [])
    if scenario == "changed_during_wait":
        assert (tmp_path / "waited").exists()


@pytest.mark.linux_only
@pytest.mark.parametrize("scenario,expected_code,reruns", CASES)
def test_label_rerun_checks_live_head_linux(tmp_path, scenario, expected_code, reruns):
    _exercise_label_rerun(tmp_path, scenario, expected_code, reruns)


@pytest.mark.macos_only
@pytest.mark.parametrize("scenario,expected_code,reruns", CASES)
def test_label_rerun_checks_live_head_macos(tmp_path, scenario, expected_code, reruns):
    _exercise_label_rerun(tmp_path, scenario, expected_code, reruns)

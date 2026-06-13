"""Test J: the Hermes skill stub shells into the CLI cleanly — artifact written,
DB updated, zero exit, empty stderr. The stub contains no lore logic."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import STORIES

from lore_stack.db import connect

SCRIPTS = Path(__file__).parents[1] / "src" / "lore_stack" / "hermes" / "storage" / "scripts"


def _run_skill(args: list[str]) -> subprocess.CompletedProcess:
    env = {**os.environ, "LORE_STACK_PYTHON": sys.executable}
    if os.name == "nt":
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", str(SCRIPTS / "lore_skill.ps1")]
        flags = {"init-db": [], "ingest-delta": ["-File"], "stage-delta": ["-File"],
                 "compile-context": ["-Query", "-Out"]}
        command, db, *rest = args
        cmd += ["-Command", command, "-DbPath", db]
        for flag, value in zip(flags[command], rest):
            cmd += [flag, value]
    else:
        cmd = ["bash", str(SCRIPTS / "lore_skill.sh"), *args]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)


@pytest.mark.filterwarnings("ignore")
def test_hermes_stub_end_to_end(tmp_path):
    db_path = str(tmp_path / "lore.db")
    artifact = str(tmp_path / "context.txt")

    init = _run_skill(["init-db", db_path])
    assert init.returncode == 0, init.stderr
    assert init.stderr.strip() == ""

    ingest = _run_skill(
        ["ingest-delta", db_path, str(STORIES / "boxwell_story_01.delta.json")]
    )
    assert ingest.returncode == 0, ingest.stderr
    assert ingest.stderr.strip() == ""

    compile_run = _run_skill(
        ["compile-context", db_path, "Tell another story with Boxwell", artifact]
    )
    assert compile_run.returncode == 0, compile_run.stderr
    assert compile_run.stderr.strip() == ""

    text = Path(artifact).read_text(encoding="utf-8")
    assert "Boxwell is a quiet travelling clockmaker" in text

    # The storage skill can also stage a delta for review (writes nothing to canon).
    staged = _run_skill(["stage-delta", db_path, str(STORIES / "boxwell_story_02.delta.json")])
    assert staged.returncode == 0, staged.stderr
    assert "staged" in staged.stdout

    conn = connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM story_runs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM compiler_runs").fetchone()[0] == 1
    conn.close()

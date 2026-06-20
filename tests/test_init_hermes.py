"""Part B: `lore-stack init-hermes` wires a Hermes home in one command -- copies
the packaged skills, writes <home>/.env, and inits a bare lore -- non-destructively
and idempotently (a re-run backs the prior skill dirs up to <name>.bak)."""
from pathlib import Path

from lore_stack.cli import main
from lore_stack.db import connect


def _run(home, *extra) -> int:
    return main(["init-hermes", "--home", str(home), *extra])


def test_init_hermes_installs_skills_env_and_bare_lore(tmp_path):
    home = tmp_path / "hermes"
    assert _run(home) == 0

    skills = home / "skills"
    # Skills copied, named by their SKILL.md `name:` (not the source dir name),
    # with their reference/script subtrees intact.
    assert (skills / "lore-extract" / "SKILL.md").is_file()
    assert (skills / "lore-extract" / "references" / "contract.md").is_file()
    assert (skills / "lore-memory" / "SKILL.md").is_file()
    assert (skills / "lore-memory" / "scripts" / "lore_skill.ps1").is_file()
    assert (skills / "lore-memory" / "scripts" / "lore_skill.sh").is_file()

    # .env carries the three keys; default embedder is the deterministic fake.
    env = (home / ".env").read_text(encoding="utf-8")
    assert "LORE_STACK_PYTHON=" in env
    assert "LORE_STACK_EMBEDDER=fake" in env
    assert "LORE_STACK_DB=" in env

    # A bare lore exists with the schema but no content.
    db = home / "local" / "lore.db"
    assert db.is_file()
    conn = connect(db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"entities", "facts", "story_runs"} <= tables
    assert conn.execute("SELECT COUNT(*) FROM story_runs").fetchone()[0] == 0
    conn.close()


def test_init_hermes_is_idempotent_and_backs_up(tmp_path):
    home = tmp_path / "hermes"
    assert _run(home) == 0

    # Tamper an installed skill so we can tell the backup from the fresh copy.
    installed = home / "skills" / "lore-memory" / "SKILL.md"
    installed.write_text("STALE", encoding="utf-8")

    assert _run(home) == 0  # second run: non-destructive, backs up

    bak = home / "skills" / "lore-memory.bak" / "SKILL.md"
    assert bak.read_text(encoding="utf-8") == "STALE"          # prior dir preserved
    assert "name: lore-memory" in installed.read_text(encoding="utf-8")  # fresh copy


def test_init_hermes_honors_db_embedder_python_args(tmp_path):
    home = tmp_path / "hermes"
    db = tmp_path / "custom" / "world.db"
    rc = _run(home, "--db", str(db), "--embedder", "ollama",
              "--python", "C:/venv/python.exe")
    assert rc == 0

    assert db.is_file()
    env = (home / ".env").read_text(encoding="utf-8")
    assert "LORE_STACK_EMBEDDER=ollama" in env
    assert "world.db" in env                  # custom db path recorded
    assert "C:/venv/python.exe" in env        # explicit python recorded (posix slashes)


def test_init_hermes_force_skips_backup(tmp_path):
    home = tmp_path / "hermes"
    assert _run(home) == 0
    assert _run(home, "--force") == 0
    # --force overwrites in place; no .bak left behind.
    assert not (home / "skills" / "lore-memory.bak").exists()
    assert not (home / "skills" / "lore-extract.bak").exists()

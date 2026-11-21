import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "tools" / "mine_removed_lines.py"


def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True,
                   capture_output=True)


def test_miner_lists_removed_comment_phrases(tmp_path):
    for i in range(3):
        (tmp_path / f"f{i}.py").write_text(f"# Helper function to load data {i}\nx = {i}\n")
    nb = '{"cells": [{"cell_type": "code", "source": [\n"# Helper function to plot\n",\n"y = 1"\n]}]}\n'
    (tmp_path / "n.ipynb").write_text(nb)
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "add")
    for i in range(3):
        (tmp_path / f"f{i}.py").write_text(f"x = {i}\n")
    (tmp_path / "n.ipynb").write_text('{"cells": []}\n')
    git(tmp_path, "commit", "-qam", "scrub")
    out = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path)], capture_output=True, text=True, check=True).stdout
    assert "helper function to" in out.split("Most often")[0]
    assert "helper function to plot" in out
    assert "x = " not in out  # removed code is not a comment

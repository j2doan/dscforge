import subprocess
from pathlib import Path

from dscforge.snapshot import make_snapshot


def git(root, *a):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a],
                   cwd=root, check=True, capture_output=True)


def files(p):
    return sorted(x.relative_to(p).as_posix() for x in p.rglob("*") if x.is_file())


# repo with root.txt and sub/inner.txt, one commit
def make_repo(root: Path) -> None:
    git(root, "init", "-q")
    (root / "sub").mkdir()
    (root / "sub" / "inner.txt").write_text("in")
    (root / "root.txt").write_text("root")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "init")


# observed: git archive from a subfolder holds only that folder, paths relative to it
def test_subfolder_only(tmp_path):
    make_repo(tmp_path)
    snap, warnings, is_git, _ = make_snapshot(tmp_path / "sub")
    assert files(snap) == ["inner.txt"] and warnings == [] and is_git


def test_root(tmp_path):
    make_repo(tmp_path)
    snap, _, _, _ = make_snapshot(tmp_path)
    assert files(snap) == ["root.txt", "sub/inner.txt"]


def test_untracked_and_modified_warn(tmp_path):
    make_repo(tmp_path)
    (tmp_path / "sub" / "inner.txt").write_text("changed")
    (tmp_path / "sub" / "new.txt").write_text("new")
    (tmp_path / "root.txt").write_text("changed")  # outside sub: no warning
    snap, warnings, _, _ = make_snapshot(tmp_path / "sub")
    assert (snap / "inner.txt").read_text() == "in"
    assert len(warnings) == 2
    assert sorted(warnings) == ["modified: sub/inner.txt (others will not get this version)",
                                "untracked: sub/new.txt (others will not get this file)"]  # git letters, in words


def test_non_git_copy_skips_junk(tmp_path):
    (tmp_path / "a.py").write_text("x")
    for d in (".venv", "__pycache__"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "f").write_text("x")
    snap, warnings, is_git, _ = make_snapshot(tmp_path)
    assert files(snap) == ["a.py"] and warnings == [] and not is_git


def test_ignored_folder_falls_back_to_disk(tmp_path):
    make_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("ign/\n")
    (tmp_path / "ign").mkdir()
    (tmp_path / "ign" / "a.txt").write_text("x")
    snap, warnings, is_git, _ = make_snapshot(tmp_path / "ign")
    assert files(snap) == ["a.txt"] and len(warnings) == 1 and not is_git


def test_no_git_installed_means_a_plain_copy(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("x")
    import subprocess

    def no_git(*a, **k):
        raise FileNotFoundError("git")
    monkeypatch.setattr(subprocess, "run", no_git)
    snap, warnings, is_git, skipped = make_snapshot(tmp_path)
    assert files(snap) == ["a.txt"] and warnings == [] and not is_git and skipped == []


def test_files_that_cannot_be_copied_are_reported_not_fatal(tmp_path, monkeypatch):
    import shutil
    from dscforge import snapshot
    (tmp_path / "a.txt").write_text("x")

    def partial(src, dst, **k):
        raise shutil.Error([(str(tmp_path / "sub" / "locked.xlsx"), str(dst), "permission denied")])
    monkeypatch.setattr(snapshot.shutil, "copytree", partial)
    _, _, _, skipped = make_snapshot(tmp_path)
    assert skipped == [("sub/locked.xlsx", "permission denied")]


def test_a_failure_while_copying_deletes_the_half_made_copy(tmp_path, monkeypatch):
    import tempfile
    from dscforge import snapshot

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(snapshot, "_snapshot", boom)
    before = set(Path(tempfile.gettempdir()).iterdir())
    try:
        make_snapshot(tmp_path)
    except RuntimeError:
        pass
    assert set(Path(tempfile.gettempdir()).iterdir()) == before

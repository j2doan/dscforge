import io
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

SKIP = (".venv", "venv", ".git", "__pycache__", ".ipynb_checkpoints", "node_modules")


# True for a folder that is not project code: a junk folder by name, or any virtual or conda environment
# (whatever it is called) or installed packages. Without this a folder named `env/` would be read and run.
def is_junk(folder: Path) -> bool:
    return (folder.name in SKIP or folder.name == "site-packages"
            or (folder / "pyvenv.cfg").is_file() or (folder / "conda-meta").is_dir())


# shutil.copytree ignore function built on is_junk
def _ignore_junk(dirpath: str, names: list[str]) -> list[str]:
    return [n for n in names if is_junk(Path(dirpath) / n)]


# run git in `path`; returns (exit code, stdout bytes). Git not being installed counts as "not a repo".
def _git(path: Path, *args: str) -> tuple[int, bytes]:
    try:
        r = subprocess.run(["git", *args], cwd=path, capture_output=True)
    except OSError:
        return 127, b""
    return r.returncode, r.stdout


# one "not committed" note from a line of `git status --porcelain` (two status letters, then the path)
def _status_note(xy: str, name: str) -> str:
    if xy == "??" or "A" in xy:
        return f"{'untracked' if xy == '??' else 'new, not committed'}: {name} (others will not get this file)"
    if "D" in xy:
        return f"deleted: {name} (others will still get the committed version)"
    return f"{'renamed' if 'R' in xy else 'modified'}: {name} (others will not get this version)"


# copy `path` into `out` minus junk folders; returns (file relative to `path`, reason) for each file that could not
# be copied (locked, unreadable, a broken link): copytree copies all the others first
def _copy(path: Path, out: Path) -> list[tuple[str, str]]:
    try:
        shutil.copytree(path, out, dirs_exist_ok=True, ignore=_ignore_junk)
    except shutil.Error as e:
        skipped = []
        for src, _, why in e.args[0]:
            try:
                src = Path(src).relative_to(path).as_posix()
            except ValueError:
                pass
            skipped.append((str(src), str(why)))
        return skipped
    return []


# the copy itself, see make_snapshot
def _snapshot(path: Path, out: Path) -> tuple[Path, list[str], bool, list[tuple[str, str]]]:
    warnings: list[str] = []

    # git folder with at least one commit: archive HEAD for this folder only
    # (git archive run inside a subfolder yields just that subfolder, paths relative to it)
    is_git = _git(path, "rev-parse", "--is-inside-work-tree")[0] == 0
    if is_git and _git(path, "rev-parse", "--verify", "HEAD")[0] == 0:
        code, data = _git(path, "archive", "--format=zip", "HEAD")
        if code == 0:
            zipfile.ZipFile(io.BytesIO(data)).extractall(out)
            if not any(out.iterdir()):  # nothing tracked here (e.g. gitignored folder): check disk instead
                skipped = _copy(path, out)
                return out, ["no committed files in this folder (ignored or untracked); checked as on disk"], False, skipped

            # one warning per uncommitted or untracked file
            _, status = _git(path, "status", "--porcelain", "--untracked-files=all", "--", ".")
            for line in status.decode(errors="replace").splitlines():
                warnings.append(_status_note(line[:2], line[3:]))
            return out, warnings, True, []

    # no git, or no commits yet: plain copy minus junk folders
    return out, warnings, False, _copy(path, out)


# copy `path` into a temp dir as someone else would get it. Returns (the copy, "not committed" notes, whether it is
# the committed files of a git repo, files that could not be copied). A failure deletes the half-made copy.
def make_snapshot(path) -> tuple[Path, list[str], bool, list[tuple[str, str]]]:
    out = Path(tempfile.mkdtemp(prefix="dscforge_"))
    try:
        return _snapshot(Path(path).resolve(), out)
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise

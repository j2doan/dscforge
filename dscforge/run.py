import ast
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from .read import KNOWN_PACKAGES, Finding, install_args
from .snapshot import is_junk

HARNESS = ["ipykernel", "nbclient", "nbformat"]  # installed only into the throwaway environment, only if notebooks exist
MAX_COPY_MB = 500  # local data files bigger than this are not copied into the clean copy
INSTALL_SECONDS = 1800
SKIP_FILES = {"setup.py", "conftest.py"}
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# python snippets run inside the throwaway environment; the file to run arrives as sys.argv[1]
NOTEBOOK = (
    "import sys, nbformat\n"
    "from nbclient import NotebookClient\n"
    "nb = nbformat.read(sys.argv[1], as_version=4)\n"
    "NotebookClient(nb, timeout=int(sys.argv[2]), kernel_name='python3', "
    "resources={'metadata': {'path': '.'}}).execute()\n"
)
RUN_PATH = "import runpy, sys; runpy.run_path(sys.argv[1], run_name='dscforge_import')"
IMPORT_NAME = "import importlib, sys; importlib.import_module(sys.argv[1])"


# The variables the project's code may see: what any clean machine has. Everything else (API keys, tokens, your own
# settings) is left out, so code that needs one fails here as it would for someone else.
ENV_KEEP = {"PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "TZ", "TERM", "LANG"}
# folders that hold a person's files and settings; the project's code gets throwaway ones
HOME_VARS = ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
             "XDG_STATE_HOME")
TEMP_VARS = ("TEMP", "TMP", "TMPDIR")
# what pip or uv additionally need to reach the network and find their caches and settings
INSTALL_KEEP = {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR",
                "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"}


# the environment for a child process: for the installer (real home, temp and network settings) or for the
# project's code (nothing but ENV_KEEP, and home and temp folders inside the throwaway folder `work`)
def _clean_env(work: Path, installer: bool) -> dict[str, str]:
    src = os.environ
    env = {k: v for k, v in src.items() if k.upper() in ENV_KEEP or k.upper().startswith("LC_")}
    if installer:
        env.update({k: v for k, v in src.items() if k.upper() in INSTALL_KEEP or k.upper().startswith(("PIP_", "UV_"))})
        env.update({k: src[k] for k in HOME_VARS + TEMP_VARS if k in src})
        return env
    home, tmp = work / "home", work / "tmp"
    home.mkdir(exist_ok=True)
    tmp.mkdir(exist_ok=True)
    env.update({k: str(home) for k in HOME_VARS})
    env.update({k: str(tmp) for k in TEMP_VARS})
    return env


# kill a process and everything it started (a notebook kernel, a training worker)
def _kill_tree(p):
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    else:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:  # it had just exited by itself
            pass


# run cmd with stdout and stderr in a temp file; returns (exit code, or None if it timed out, tail of the output).
# The child runs in its own process group so the whole tree can be killed on a timeout, and also on Ctrl+C,
# which a terminal would otherwise deliver only to dscforge and leave the child running.
def _exec(cmd, cwd, env, seconds):
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    with tempfile.TemporaryFile() as out:
        p = subprocess.Popen([str(c) for c in cmd], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                             stdout=out, stderr=subprocess.STDOUT, **kw)
        try:
            code = p.wait(seconds)
        except subprocess.TimeoutExpired:
            _kill_tree(p)
            p.wait()
            code = None
        except BaseException:
            _kill_tree(p)
            p.wait()
            raise
        out.seek(0)
        return code, out.read().decode("utf-8", errors="replace")[-4000:]


# last non-empty line of some output
def _last_line(text: str) -> str:
    return next((l.strip() for l in reversed(ANSI.sub("", text).splitlines()) if l.strip()), "")


# (level, rule, message) for a run that ended; None if it was still running when it was stopped (code is None
# when the time limit killed it, or a notebook cell hit its limit): that counts as passing the startup check
def classify(code, text: str):
    text = ANSI.sub("", text)
    if code is None or "CellTimeoutError" in text or "Cell execution timed out" in text:
        return None
    m = re.search(r"ModuleNotFoundError: No module named '([^']+)'", text)
    if m:
        return "fail", "run-missing-package", f"missing package '{m.group(1).split('.')[0]}'"
    m = re.search(r"FileNotFoundError:.*?['\"]([^'\"]+)['\"]", text)
    if m:
        return "fail", "run-missing-file", f"cannot open '{m.group(1)}'"
    if "EOFError: EOF when reading a line" in text:
        return "warn", "run-skipped", "skipped (not a failure): it waits for keyboard input"
    if code == 2 and "usage:" in text.lower():
        return "warn", "run-skipped", "skipped (not a failure): it needs command-line arguments"
    return "fail", "run-error", _last_line(text)[:300] or f"exited with code {code}"


# every .py or .ipynb file under root (skipping junk folders), sorted by relative path
def _files(root: Path, *suffixes: str) -> list[Path]:
    found = []
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not is_junk(Path(dirpath) / d)]
        found += [Path(dirpath) / n for n in names if n.endswith(suffixes)]
    return sorted(found, key=lambda p: p.relative_to(root).as_posix())


# True for the test `__name__ == "__main__"`
def _is_main(test) -> bool:
    return (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "__name__"
            and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in test.comparators))


# what to do with each .py file: (relative path, "script" | "import" | "package"), sorted.
# script: has a __main__ block, or nothing imports it. import: imported by others, so it is loaded, not run.
# package: uses relative imports, so it can only be loaded by its dotted name.
def plan_scripts(root: Path, skip: set[str] = frozenset()) -> list[tuple[str, str]]:
    parsed = {}
    imported: set[str] = set()
    for path in _files(root, ".py"):
        rel = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, ValueError):
            continue
        has_main = any(isinstance(n, ast.If) and _is_main(n.test) for n in tree.body)
        relative = False
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    imported.update(a.name.split("."))
            elif isinstance(n, ast.ImportFrom):
                relative |= n.level > 0
                imported.update((n.module or "").split("."))
                imported.update(a.name for a in n.names)
        parsed[rel] = (has_main, relative)
    plan = []
    for rel, (has_main, relative) in parsed.items():
        p = Path(rel)
        stem = p.parent.name if p.name == "__init__.py" else p.stem
        if p.name in SKIP_FILES or rel in skip:
            continue
        if relative:
            plan.append((rel, "package"))
        elif p.name != "__init__.py" and (has_main or stem not in imported):
            plan.append((rel, "script"))
        else:
            plan.append((rel, "import"))
    return plan


# the command that runs one item inside the environment (None if it cannot be run)
def _command(py, root: Path, rel: str, kind: str, cell_seconds: int):
    path = root / rel
    if kind == "script":
        return [py, path.name]
    if kind == "import":
        return [py, "-c", RUN_PATH, path]
    if kind == "notebook":
        return [py, "-c", NOTEBOOK, path, cell_seconds]
    parts = list(Path(rel).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    if not parts or not all(p.isidentifier() for p in parts):
        return None
    return [py, "-c", IMPORT_NAME, ".".join(parts)]


# pip (or uv, if installed, because it is faster) install into the environment; returns (code, output)
def _install(py, args, cwd, env):
    uv = shutil.which("uv")
    cmd = [uv, "pip", "install", "--quiet", "--python", py, *args] if uv else [py, "-m", "pip", "install", "--quiet", *args]
    return _exec(cmd, cwd, env, INSTALL_SECONDS)


# copy the local-only data files into the clean copy; returns findings for files that are too big
def _copy_data(root: Path, source: Path, data) -> list[Finding]:
    skipped = []
    for file, loc, target in dict.fromkeys(data):
        src = source / target
        if src.stat().st_size > MAX_COPY_MB * 1024 * 1024:
            skipped.append(Finding("warn", file, loc, "local-only",
                                   f"{target} is over {MAX_COPY_MB} MB, so it was not copied; runs that read it will fail"))
            continue
        (root / target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, root / target)
    return skipped


# why `python -m venv` failed, in words. Debian and Ubuntu ship the venv module without its pip bootstrap, and Python's
# own message then names the package for this exact version (python3.12-venv, python3.14-venv, ...): pass that on.
# Other systems get the general advice.
def _venv_failure(text: str) -> str:
    if "ensurepip" not in text:
        return _last_line(text)
    package = re.search(r"apt install (\S+)", text)
    if package:
        return f"this Python cannot create environments with pip (on Debian or Ubuntu, run: sudo apt install {package.group(1)})"
    return "this Python cannot create environments with pip (its venv or ensurepip part is missing: install your system's python venv package)"


# "30 s" or "2 min"
def _limit(seconds: int) -> str:
    return f"{seconds} s" if seconds < 120 else f"{seconds / 60:g} min"


# startup check: start everything in the clean copy `root` inside a brand-new environment, each item for at most
# `minutes`. A crash is a finding; still running at the limit counts as passing and is only listed in the notes.
# Returns (findings, notes).
# data: local-only files (see read.find_local_data); findings: from the read pass; log(message): progress lines
def run_pass(root: Path, source: Path, data, findings, minutes: float, log) -> tuple[list[Finding], list[str]]:
    root = Path(root)
    out: list[Finding] = []
    still: list[str] = []  # items that were still running at the limit
    ran = 0
    seconds = max(1, round(minutes * 60))
    broken = {f.file for f in findings if f.rule == "syntax-error"}
    items = plan_scripts(root, broken)
    items += [(p.relative_to(root).as_posix(), "notebook") for p in _files(root, ".ipynb")]
    items.sort()
    if not items:
        log("nothing to run")
        return out, []

    work = Path(tempfile.mkdtemp(prefix="dscforge_env_"))
    pyver = f"Python {sys.version_info.major}.{sys.version_info.minor}"  # the environment is built from this Python
    try:
        log(f"{len(items)} notebooks and scripts to start, {_limit(seconds)} each")
        log(f"creating an empty environment ({pyver}) ...")
        code, text = _exec([sys.executable, "-m", "venv", work / "env"], work, os.environ.copy(), INSTALL_SECONDS)
        py = work / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if code != 0 or not py.exists():
            return [Finding("fail", "", "", "install-failed", f"could not create an environment: {_venv_failure(text)}")], []
        ours = {
            "VIRTUAL_ENV": str(work / "env"), "PYTHONPATH": os.pathsep.join([str(root), str(root / "src")]),
            "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "MPLBACKEND": "Agg",
            "JUPYTER_DATA_DIR": str(work / "jupyter"), "JUPYTER_CONFIG_DIR": str(work / "jupyter-config"),
            "JUPYTER_NO_CONFIG": "1",
        }
        env = _clean_env(work, installer=False)  # what the project's code sees
        install_env = _clean_env(work, installer=True)  # what pip or uv sees
        for e in (env, install_env):
            e.update(ours)
            e["PATH"] = str(py.parent) + os.pathsep + e.get("PATH", "")

        # install what the project declares; if it declares nothing, install what its imports suggest
        args = install_args(root)
        if args:
            log(f"installing the declared packages ({pyver}) ...")
            code, text = _install(py, args, root, install_env)
            if code != 0:
                return [Finding("fail", "", "", "install-failed",
                                f"could not install the declared packages in a new {pyver} environment: {_last_line(text)}")], []
        else:
            guessed = next((f.message.split("\n")[1:] for f in findings if f.rule == "no-requirements"), [])
            for pkg in guessed:  # one at a time, so one wrong guess does not block the rest
                if pkg not in KNOWN_PACKAGES:
                    out.append(Finding("warn", "", "", "install-failed",
                                       f"did not install '{pkg}': it is only a guess from an import and may be one of "
                                       f"your own modules. If it is a package, add it to requirements.txt"))
                    continue
                log(f"installing {pkg} (guessed from the imports) ...")
                code, text = _install(py, [pkg], root, install_env)
                if code != 0:
                    out.append(Finding("warn", "", "", "install-failed",
                                       f"could not install '{pkg}' (guessed from the imports): {_last_line(text)}"))
        if any(kind == "notebook" for _, kind in items):
            log("installing the notebook runner ...")
            code, text = _install(py, HARNESS, root, install_env)
            if code != 0:
                out.append(Finding("fail", "", "", "install-failed", f"could not install the notebook runner: {_last_line(text)}"))
                items = [i for i in items if i[1] != "notebook"]

        out += _copy_data(root, source, data)

        # run the items in path order, one at a time, each from its own folder
        for rel, kind in items:
            cmd = _command(py, root, rel, kind, max(5, seconds - 5))
            if cmd is None:
                continue
            log(f"starting {rel} ...")
            ran += 1
            code, text = _exec(cmd, (root / rel).parent, env, seconds)
            if code == 0:
                continue
            result = classify(code, text)
            if result is None:
                still.append(rel)
            else:
                out.append(Finding(result[0], rel, "", result[1], result[2]))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    notes = []
    if still:
        shown = ", ".join(still[:10]) + (f" and {len(still) - 10} more" if len(still) > 10 else "")
        notes.append(f"{len(still)} of {ran} were still running after {_limit(seconds)}, so they were not checked "
                     f"further (this counts as passing): {shown}")
    return out, notes

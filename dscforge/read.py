import ast
import fnmatch
import json
import os
import posixpath
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .snapshot import is_junk


@dataclass
class Finding:
    level: str  # "fail" or "warn"
    file: str  # relative to the copy
    location: str  # "line 12", "cell 3" or ""
    rule: str
    message: str


IMPORT_TO_PACKAGE = {
    "sklearn": "scikit-learn", "cv2": "opencv-python", "PIL": "pillow", "yaml": "pyyaml",
    "bs4": "beautifulsoup4", "skimage": "scikit-image", "dateutil": "python-dateutil",
    "dotenv": "python-dotenv", "attr": "attrs", "umap": "umap-learn", "imblearn": "imbalanced-learn",
    "Crypto": "pycryptodome", "serial": "pyserial", "jwt": "pyjwt", "OpenSSL": "pyopenssl", "docx": "python-docx",
    "pptx": "python-pptx", "fitz": "pymupdf", "git": "gitpython", "zmq": "pyzmq", "Bio": "biopython",
    "MySQLdb": "mysqlclient",
}
STRING = re.compile(r"""(['"])((?:\\.|(?!\1).)*)\1""")
# absolute path in string-literal source: any drive letter, a network share, or a known unix root folder
ABS_PATH = re.compile(
    r"(?<![A-Za-z])[A-Za-z]:[\\/]+[\w.$\\/-]{2}"  # two path characters after the drive, so "A:\n" is not a path
    r"|^\\{2,4}[A-Za-z][\w$.-]*\\{1,2}\w"  # network share: only at the start, or "..\\data\\x" would match
    r"|(?<![\w.:/])/(?:Users|home|mnt|Volumes|tmp|var|opt|root|media|private|content|kaggle|workspace)/"
)
# plain-text files besides .py and .ipynb that are read too: configs and scripts (a problem there fails, because
# programs read them) and prose and logs (a problem there is polish)
CONFIG_TYPES = {".json", ".toml", ".yaml", ".yml", ".cfg", ".ini", ".sh", ".ps1", ".bat"}
DOC_TYPES = {".md", ".txt", ".log", ".rst"}
SKIP_TEXT_NAMES = {"package-lock.json"}
MAX_TEXT_BYTES = 2 * 1024 * 1024  # bigger files are data, not config or prose
MAX_PER_FILE = 25  # distinct findings per text file; the rest are counted in one line
RELATIVE_ESCAPE = re.compile(r"(?:\.\./)+[^\s)\"'`<>\]]*")
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
MAX_AI_LINE = 2000  # longer lines (embedded images, minified code) are not prose or comments: not scanned for AI phrases


# entries of a data file next to this module: one per line, blank lines and lines starting with `marker` skipped
def _data_lines(name: str, marker: str) -> list[str]:
    lines = (Path(__file__).parent / name).read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lines if l.strip() and not l.lstrip().startswith(marker)]


# The well-known packages: the most downloaded on PyPI (known_packages.txt, written by tools/refresh_known_packages.py).
# When a project declares nothing, only these are installed from a guess: any other name in an import may be one of
# the project's own modules, and installing it from PyPI would pull in unrelated code.
KNOWN_PACKAGES = frozenset(_data_lines("known_packages.txt", "#"))

# environment variables that every machine has: reading them is not a dependency on someone's setup
SYSTEM_VARS = {"PATH", "HOME", "USER", "USERNAME", "USERPROFILE", "TEMP", "TMP", "TMPDIR", "SHELL", "LANG", "PWD", "OS"}
# conda package names that differ from the PyPI name
CONDA_TO_PIP = {"pytorch": "torch", "opencv": "opencv-python", "matplotlib-base": "matplotlib"}


# ai_phrases.txt ("//" starts a comment, because an entry may start with #): lowercase, each once
AI_PHRASES = list(dict.fromkeys(l.lower() for l in _data_lines("ai_phrases.txt", "//")))


# phrase -> whole-word pattern, grouped by the phrase's first word, so a line only tries the phrases that start
# with a word it contains
def _index_phrases(phrases: list[str]) -> dict[str, list]:
    index: dict[str, list] = {}
    for p in phrases:
        first = re.search(r"\w+", p)
        if first:
            left = r"(?<!\w)" if re.match(r"\w", p) else ""
            right = r"(?!\w)" if re.search(r"\w$", p) else ""
            index.setdefault(first.group(0), []).append((p, re.compile(left + re.escape(p) + right)))
    return index


AI_INDEX = _index_phrases(AI_PHRASES)


# the AI phrases found in `line`, sorted; when one phrase sits inside a longer one that also matched, only the longer
def _ai_hits(line: str) -> list[str]:
    if len(line) > MAX_AI_LINE:
        return []
    low = line.lower().replace("’", "'")
    hits = {p for w in set(re.findall(r"\w+", low)) for p, pat in AI_INDEX.get(w, ()) if pat.search(low)}
    return sorted(h for h in hits if not any(h != g and h in g for g in hits))


# the message for one place: the phrases found there and, if it has one, an emoji; None when there is nothing
def _ai_message(phrases: list[str], emoji: bool = False) -> str | None:
    parts = [f"'{p}'" for p in phrases] + (["an emoji"] if emoji else [])
    return "possibly AI: " + ", ".join(parts) if parts else None


# a path as the person would read it: the doubled backslashes of Python source or JSON become single ones
def _shown(path: str) -> str:
    return path.replace("\\\\", "\\")


# one finding per distinct message in a file: at its first place, with how many other places repeat it
# hits: [(place, message)]
def _grouped(rel: str, hits: list[tuple[str, str]]) -> list[Finding]:
    first: dict[str, list] = {}
    for place, message in hits:
        first.setdefault(message, [place, 0])[1] += 1
    return [Finding("warn", rel, place, "potential-ai-leftover",
                    message + (f" (and {n - 1} more places)" if n > 1 else "")) for message, (place, n) in first.items()]


# (pattern, entry as written) for each line of leftover_files.txt; an entry ending in .md also matches the same name
# with the other common extensions
def _leftover_patterns() -> list[tuple[str, str]]:
    out = []
    for entry in _data_lines("leftover_files.txt", "#"):
        pats = [entry.lower()]
        if entry.lower().endswith(".md") and not any(c in entry for c in "*/"):
            pats += [entry[:-3].lower() + ext for ext in (".txt", ".yaml", ".yml", ".json", ".toml")]
        out += [(p, entry) for p in pats]
    return out


LEFTOVER = _leftover_patterns()


# the leftover_files.txt entry that `name`, found at project-relative path `rel`, matches; None if it matches none
def _leftover_match(name: str, rel: str) -> str | None:
    n, r = name.lower(), rel.lower()
    for pat, entry in LEFTOVER:
        if "/" in pat:
            if fnmatch.fnmatchcase(r, pat) or fnmatch.fnmatchcase(r, "*/" + pat):
                return entry
        elif fnmatch.fnmatchcase(n, pat):
            return entry
    return None


# True if relative path `s`, written in a file inside folder `rel_dir`, climbs out of the checked folder
def _escapes(s: str, rel_dir: str) -> bool:
    p = s.replace("\\", "/")
    if "/" not in p or ".." not in p.split("/"):
        return False
    out = posixpath.normpath(posixpath.join(rel_dir, p))
    return out == ".." or out.startswith("../")


# normalize a package name: lowercase, runs of - _ . become -
def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


# package name from a requirement line ("pandas[x]>=2 ; python_version" -> "pandas")
def _req_name(line: str) -> str:
    return _norm(re.split(r"[\s<>=!~;\[@(]", line.strip(), maxsplit=1)[0])


# text of a requirements-style file, decoded by its byte-order mark: Windows PowerShell 5 writes UTF-16 for
# `pip freeze > requirements.txt`, which pip reads but a plain UTF-8 read would turn into garbage
def _read_req(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8-sig", errors="replace")


# package names in a requirements file, following `-r other.txt` includes (relative to the including file)
def _req_file_names(path: Path, seen: set[Path]) -> set[str]:
    path = path.resolve()
    if path in seen or not path.is_file():
        return set()  # a cycle, or an include that is not in the copy
    seen.add(path)
    names: set[str] = set()
    for line in _read_req(path).splitlines():
        line = line.split("#", 1)[0].strip()
        include = re.match(r"(?:-r|--requirement)\s*=?\s*(\S+)", line)
        if include:
            names |= _req_file_names(path.parent / include.group(1), seen)
        elif line and not line.startswith("-"):  # other options (-e, --index-url, -c ...) declare nothing
            names.add(_req_name(line))
    return names


# normalized names declared in requirements.txt, pyproject.toml, setup.py and environment.yml; also whether anything
# is declared
def declared_packages(root: Path) -> tuple[set[str], bool]:
    names: set[str] = set()
    found = False
    req = root / "requirements.txt"
    if req.is_file():
        found = True
        names |= _req_file_names(req, set())
    reqs, conda = _other_declared(root)
    if reqs or conda:
        found = True
    names.update(_req_name(r) for r in reqs)
    names.update(_norm(CONDA_TO_PIP.get(c, c)) for c in conda)
    return names, found


# pyproject.toml as a dict ({} if missing or broken)
def _pyproject(root: Path) -> dict:
    pp = root / "pyproject.toml"
    if not pp.is_file():
        return {}
    try:
        return tomllib.loads(pp.read_text(errors="replace"))
    except tomllib.TOMLDecodeError:
        return {}


# the project's own importable name from pyproject.toml ([project] name, with - as _), or ""
def _own_name(root: Path) -> str:
    return str(_pyproject(root).get("project", {}).get("name", "")).replace("-", "_")


# requirement strings from pyproject.toml: [project] dependencies and [dependency-groups]
def _pyproject_reqs(root: Path) -> list[str]:
    data = _pyproject(root)
    reqs = list(data.get("project", {}).get("dependencies", []))
    for group in data.get("dependency-groups", {}).values():
        reqs += [r for r in group if isinstance(r, str)]  # skip {include-group = ...}
    return reqs


# package names under [tool.poetry] (dependencies, dev-dependencies and groups); the python version is not a package
def _poetry_names(root: Path) -> list[str]:
    poetry = _pyproject(root).get("tool", {}).get("poetry", {})
    tables = [poetry.get("dependencies", {}), poetry.get("dev-dependencies", {})]
    tables += [g.get("dependencies", {}) for g in poetry.get("group", {}).values() if isinstance(g, dict)]
    return [k for t in tables if isinstance(t, dict) for k in t if k.lower() != "python"]


# the strings of `install_requires=[...]` in a setup(...) call in setup.py (read by parsing, never executed)
def _setup_py_reqs(root: Path) -> list[str]:
    path = root / "setup.py"
    if not path.is_file():
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError):
        return []
    reqs: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and (getattr(n.func, "attr", None) or getattr(n.func, "id", None)) == "setup":
            for kw in n.keywords:
                if kw.arg == "install_requires" and isinstance(kw.value, (ast.List, ast.Tuple)):
                    reqs += [e.value for e in kw.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return reqs


# environment.yml (or .yaml): (conda package names, requirement strings of the nested `pip:` list). A tiny line reader,
# enough for the usual layout: a `dependencies:` key whose list items are packages, one of them `- pip:` with its own list
def _env_yml(root: Path) -> tuple[list[str], list[str]]:
    path = next((root / n for n in ("environment.yml", "environment.yaml") if (root / n).is_file()), None)
    if path is None:
        return [], []
    conda: list[str] = []
    pip: list[str] = []
    in_deps = in_pip = False
    pip_indent = 0
    for raw in _read_req(path).splitlines():
        line = raw.split("#", 1)[0].rstrip()
        text = line.strip()
        if not text:
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0 and not text.startswith("-"):  # a top-level key
            in_deps, in_pip = text.startswith("dependencies:"), False
        elif in_deps and text.startswith("-"):
            item = text[1:].strip().strip("'\"")
            if item.startswith("pip:"):
                in_pip, pip_indent = True, indent
            elif in_pip and indent > pip_indent:
                if item and not item.startswith("-"):  # `-r file` style lines declare nothing by name
                    pip.append(item)
            else:
                in_pip = False
                name = re.split(r"[=<>!~ ]", item.split("::")[-1], maxsplit=1)[0]
                if name and name.lower() not in ("python", "pip"):
                    conda.append(name)
    return conda, pip


# (requirement strings, conda names) declared outside requirements.txt: pyproject.toml ([project], dependency groups,
# poetry), setup.py and environment.yml
def _other_declared(root: Path) -> tuple[list[str], list[str]]:
    conda, env_pip = _env_yml(root)
    return _pyproject_reqs(root) + _poetry_names(root) + _setup_py_reqs(root) + env_pip, conda


# pip arguments that install everything the project declares ([] when it declares nothing). Conda names from
# environment.yml are only installed if they are well-known PyPI packages (conda also lists compilers and libraries).
def install_args(root: Path) -> list[str]:
    args: list[str] = []
    req = root / "requirements.txt"
    if req.is_file() and any(l.split("#", 1)[0].strip() for l in _read_req(req).splitlines()):
        args += ["-r", "requirements.txt"]
    reqs, conda = _other_declared(root)
    pip_names = [CONDA_TO_PIP.get(c, c) for c in conda]
    return args + reqs + [n for n in pip_names if _norm(n) in KNOWN_PACKAGES]


# the file as (kind, location, text) units: kind is "code", "text" (markdown) or "out"
def _units(path: Path) -> list[tuple[str, str, str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".py":
        return [("code", "", raw)]
    try:
        cells = json.loads(raw).get("cells", [])
    except (json.JSONDecodeError, AttributeError):
        return []
    units = []
    for i, c in enumerate(cells, 1):
        loc = f"cell {i}"
        src = c.get("source", "")
        src = "".join(src) if isinstance(src, list) else str(src)
        units.append(("code" if c.get("cell_type") == "code" else "text", loc, src))
        for o in c.get("outputs", []):
            parts = [o.get("text", ""), o.get("data", {}).get("text/plain", ""), o.get("traceback", "")]
            units.append(("out", loc, "\n".join("".join(p) if isinstance(p, list) else str(p) for p in parts)))
    return units


# top-level modules imported by `src` with a line number; unparsable source yields nothing
def _imports(src: str, notebook: bool) -> list[tuple[str, int]]:
    if notebook:
        src = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith(("%", "!")))
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return []
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [(a.name.split(".")[0], n.lineno) for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            out.append((n.module.split(".")[0], n.lineno))
    return out


# True if `node` is os.environ (or a bare `environ` imported from os)
def _is_environ(node) -> bool:
    return ((isinstance(node, ast.Attribute) and node.attr == "environ" and isinstance(node.value, ast.Name)
             and node.value.id == "os") or (isinstance(node, ast.Name) and node.id == "environ"))


# (name, line number) of environment variables read by literal name: os.environ["X"], os.environ.get("X"),
# os.getenv("X"); the variables every machine has (SYSTEM_VARS) are skipped. Unparsable source yields nothing.
def _env_vars(src: str, notebook: bool) -> list[tuple[str, int]]:
    if notebook:
        src = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith(("%", "!")))
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return []
    found = []
    for n in ast.walk(tree):
        key = None
        if isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load) and _is_environ(n.value):
            key = n.slice
        elif isinstance(n, ast.Call) and n.args:
            f = n.func
            if ((isinstance(f, ast.Attribute) and f.attr == "get" and _is_environ(f.value))
                    or (isinstance(f, ast.Attribute) and f.attr == "getenv" and isinstance(f.value, ast.Name) and f.value.id == "os")
                    or (isinstance(f, ast.Name) and f.id == "getenv")):
                key = n.args[0]
        if isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value.upper() not in SYSTEM_VARS:
            found.append((key.value, n.lineno))
    return found


# the comment at the end of a line of Python: the text after the first # that is not inside a string, or ""
def _comment(line: str) -> str:
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == "#":
            return line[i + 1:]
        i += 1
    return ""


DATA_EXT = (".csv", ".tsv", ".parquet", ".feather", ".json", ".xlsx", ".xls", ".pkl", ".pickle", ".npy", ".npz",
            ".pt", ".pth", ".h5", ".hdf5", ".db", ".sqlite", ".zip", ".gz")


# data files the code names that exist in `source` (the folder as it is on disk) but not in the copy `root`:
# (file, location, path relative to root). Paths resolve against the file's own folder, like the `outside` rule
def find_local_data(root: Path, source: Path) -> list[tuple[str, str, str]]:
    found = []
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not is_junk(Path(dirpath) / d)]
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        for name in names:
            path = Path(dirpath) / name
            if path.suffix not in (".py", ".ipynb"):
                continue
            rel = path.relative_to(root).as_posix()
            for kind, loc, text in _units(path):
                if kind != "code":
                    continue
                for i, line in enumerate(text.splitlines(), 1):
                    for s in (m.group(2) for m in STRING.finditer(line)):
                        if not s.lower().endswith(DATA_EXT) or ABS_PATH.search(s) or s.startswith(("~", "/")) or "://" in s:
                            continue
                        target = posixpath.normpath(posixpath.join(rel_dir, s.replace("\\", "/")))
                        if target == ".." or target.startswith("../"):
                            continue  # the `outside` rule reports these
                        if not (root / target).exists() and (source / target).is_file():
                            found.append((rel, loc or f"line {i}", target))
    return found


# absolute paths, paths that leave the folder, and forbidden words in a plain-text file that is not Python or a
# notebook. The same finding on many lines is reported once, with the number of lines.
def _scan_text(path: Path, rel: str, rel_dir: str, forbid_l: list[str]) -> list[Finding]:
    if path.name in SKIP_TEXT_NAMES or path.stat().st_size > MAX_TEXT_BYTES:
        return []
    config = path.suffix.lower() in CONFIG_TYPES
    prose = path.suffix.lower() in (".md", ".txt", ".rst") and not path.name.lower().startswith("requirements")  # no logs, configs or dependency lists
    seen: dict[tuple[str, str], list] = {}  # (rule, what) -> [level, first line, lines, message]
    ai_hits: list[tuple[str, str]] = []  # (place, message) for the AI phrases (prose files only)
    overflow = 0

    def note(rule: str, what: str, level: str, message: str, line_no: int) -> None:
        nonlocal overflow
        entry = seen.get((rule, what))
        if entry:
            entry[2] += 1
        elif len(seen) < MAX_PER_FILE:
            seen[(rule, what)] = [level, line_no, 1, message]
        else:
            overflow += 1

    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        hit = ABS_PATH.search(line)
        if hit:
            token = re.match(r"\S+", line[hit.start():]).group(0).rstrip(",;\"'`)]}")
            if config:
                note("abs-path", token, "fail", f"absolute path: {_shown(token) if config else token} (will not exist on another machine)", i)
            else:
                note("leaked-path", token, "warn", f"text contains a local path: {token}", i)
        for esc in RELATIVE_ESCAPE.finditer(line):
            token = esc.group(0).rstrip(".,;:")
            if _escapes(token, rel_dir):
                note("outside", token, "fail" if config else "warn", f"path leaves the folder: {token}", i)
        low = line.lower()
        for f in forbid_l:
            if f in low:
                note("forbidden", f, "fail", f"mentions '{f}'", i)
        if prose:
            msg = _ai_message(_ai_hits(line))
            if msg:
                ai_hits.append((f"line {i}", msg))

    found = _grouped(rel, ai_hits)
    for (rule, _), (level, line_no, lines, message) in seen.items():
        more = f" (and {lines - 1} more lines)" if lines > 1 else ""
        found.append(Finding(level, rel, f"line {line_no}", rule, message + more))
    if overflow:
        found.append(Finding("warn", rel, "", "more-findings", f"{overflow} more findings in this file are not listed"))
    return found


# all rules over the copy at `root`
def check(root: Path, forbid: list[str]) -> list[Finding]:
    root = Path(root)
    findings: list[Finding] = []
    forbid_l = [f.lower() for f in forbid]
    declared, has_decl = declared_packages(root)
    top_local = {p.stem if p.is_file() else p.name for p in root.iterdir()}
    # src layout: packages under src/ are importable, and so is the project's own declared name
    if (root / "src").is_dir():
        top_local |= {p.stem if p.is_file() else p.name for p in (root / "src").iterdir()}
    top_local.add(_own_name(root))
    third: dict[str, list[tuple[str, str]]] = {}  # module -> [(file, location)] first use per file

    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not is_junk(Path(dirpath) / d)]
        folder = Path(dirpath)
        rel_dir = folder.relative_to(root).as_posix()
        # modules importable from this file: siblings and those in every ancestor folder up to the root
        local = set(top_local)
        for d in [folder, *folder.parents]:
            local |= {p.stem if p.is_file() else p.name for p in d.iterdir()}
            if d == root:
                break

        # names of folders and files: forbidden + leftover-file (a leftover folder is reported once, not searched)
        leftover_dirs = []
        for name in dirs + names:
            rel = (folder / name).relative_to(root).as_posix()
            for f in forbid_l:
                if f in name.lower():
                    findings.append(Finding("fail", rel, "", "forbidden", f"name contains '{f}'"))
            entry = _leftover_match(name, rel)
            if entry:
                kind = "folder" if name in dirs else "file"
                findings.append(Finding("warn", rel, "", "leftover-file",
                                        f"possibly a leftover agent or working-notes {kind} (matches '{entry}')"))
                if kind == "folder" and (entry.startswith(".") or "/" in entry):  # a plain name like PLAN may be real code
                    leftover_dirs.append(name)
        dirs[:] = [d for d in dirs if d not in leftover_dirs]

        for name in names:
            path = folder / name
            if path.suffix.lower() in CONFIG_TYPES | DOC_TYPES:
                findings += _scan_text(path, path.relative_to(root).as_posix(), rel_dir, forbid_l)
                continue
            if path.suffix not in (".py", ".ipynb"):
                continue
            rel = path.relative_to(root).as_posix()
            nb = path.suffix == ".ipynb"
            units = _units(path)
            seen: set[str] = set()
            env_seen: set[str] = set()
            counts = []
            ai_hits: list[tuple[str, str]] = []  # (place, message), grouped into one finding per message after the loop

            for kind, loc, text in units:
                for i, line in enumerate(text.splitlines(), 1):
                    where = loc or f"line {i}"
                    low = line.lower()
                    if kind == "code":
                        for s in (m.group(2) for m in STRING.finditer(line)):
                            if ABS_PATH.search(s) or s.startswith("~/"):
                                findings.append(Finding("fail", rel, where, "abs-path",
                                                        f"absolute path: {_shown(s)} (will not exist on another machine)"))
                            elif _escapes(s, rel_dir):
                                findings.append(Finding("fail", rel, where, "outside", f"path leaves the folder: {_shown(s)}"))
                        # a path in a comment still shows the author's folders and username
                        comment = _comment(line)
                        hit = ABS_PATH.search(comment)
                        if hit:
                            token = re.match(r"\S+", comment[hit.start():]).group(0).rstrip(",;\"'`)]}")
                            findings.append(Finding("warn", rel, where, "leaked-path", f"comment contains a local path: {token}"))
                    for f in forbid_l:
                        if f in low:
                            findings.append(Finding("fail", rel, where, "forbidden", f"mentions '{f}'"))
                    msg = _ai_message(_ai_hits(line), bool(EMOJI.search(line)))
                    if msg:
                        ai_hits.append((where, msg))

                # a local path saved in a notebook's output or markdown reveals the author's folders and username;
                # one finding per cell, however many lines repeat it
                if kind in ("out", "text"):
                    leaks = [(l, ABS_PATH.search(l)) for l in text.splitlines() if ABS_PATH.search(l)]
                    if leaks:
                        line, hit = leaks[0]
                        token = re.match(r"\S+", line[hit.start():]).group(0)
                        more = f" (and {len(leaks) - 1} more lines)" if len(leaks) > 1 else ""
                        what = "saved output" if kind == "out" else "markdown text"
                        findings.append(Finding("warn", rel, loc, "leaked-path",
                                                f"{what} contains a local path: {token}{more}"))

                # a .py file that cannot be parsed would also silently skip its import checks
                if kind == "code" and not nb:
                    try:
                        ast.parse(text)
                    except SyntaxError as e:
                        findings.append(Finding("fail", rel, f"line {e.lineno or 1}", "syntax-error",
                                                f"file cannot run: {e.msg}"))
                    except ValueError as e:
                        findings.append(Finding("fail", rel, "", "syntax-error", f"file cannot run: {e}"))

                # environment variables the code reads by name: someone else has to set them
                if kind == "code":
                    for var, lineno in _env_vars(text, nb):
                        if var not in env_seen:
                            env_seen.add(var)
                            findings.append(Finding("warn", rel, loc or f"line {lineno}", "env-var",
                                                    f"reads the environment variable '{var}', which someone else will have to set"))

                # imports: each code unit parsed on its own
                if kind == "code":
                    for mod, lineno in _imports(text, nb):
                        if mod in sys.stdlib_module_names or mod in local or mod in seen:
                            continue
                        seen.add(mod)
                        third.setdefault(mod, []).append((rel, loc or f"line {lineno}"))

            findings += _grouped(rel, ai_hits)

            # notebook-only rules, from the raw JSON
            if nb:
                try:
                    cells = json.loads(path.read_text(encoding="utf-8", errors="replace")).get("cells", [])
                except (json.JSONDecodeError, AttributeError):
                    cells = []
                last = 0
                for i, c in enumerate(cells, 1):
                    if c.get("cell_type") != "code":
                        continue
                    if any(o.get("output_type") == "error" for o in c.get("outputs", [])):
                        findings.append(Finding("warn", rel, f"cell {i}", "nb-error", "saved output is an error"))
                    n = c.get("execution_count")
                    if n is not None:
                        if n <= last:
                            findings.append(Finding("warn", rel, f"cell {i}", "nb-order",
                                                    f"execution count {n} after {last}"))
                        last = max(last, n)

    # third-party modules: undeclared one by one, or one no-requirements finding
    missing = {m: uses for m, uses in third.items() if _norm(IMPORT_TO_PACKAGE.get(m, m)) not in declared}
    if not has_decl:
        if third:
            pkgs = sorted({_norm(IMPORT_TO_PACKAGE.get(m, m)) for m in third})
            findings.append(Finding("warn", "", "", "no-requirements",
                                    "no requirements.txt, pyproject.toml, environment.yml or setup.py dependencies; add:\n" + "\n".join(pkgs)))
    else:
        for m, uses in missing.items():
            for file, loc in uses:
                findings.append(Finding("fail", file, loc, "undeclared",
                                        f"'{m}' is not in requirements.txt, pyproject.toml, environment.yml or setup.py"))
    return findings

# dscforge

**Check whether your project will work for someone else.**

dscforge is a command-line tool. It makes a temporary copy of a project folder, reads the code in the copy, and reports what would break, leak or look wrong on another machine: hardcoded paths, references outside the folder, undeclared packages, files that exist only on your computer, words you never want to ship, and leftovers from AI coding tools. Optionally it also starts your notebooks and scripts in a brand-new empty environment to see whether they crash. It only reports. It never changes your project.

## Contents

- [The problem](#the-problem)
- [What exists today](#what-exists-today)
- [What dscforge does](#what-dscforge-does)
- [Install](#install)
- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Commands and flags](#commands-and-flags)
- [What it catches](#what-it-catches)
- [The plan file](#the-plan-file)
- [Things to know](#things-to-know)
- [Known limits](#known-limits)

## The problem

A project works on the machine where it was written. Then someone else opens it (a teammate, a reviewer, a CI job, a hiring manager) and it fails, for reasons the recipient cannot see from their own computer:

- **Hardcoded paths.** `/Users/alice/data/sales.csv` or `C:\Users\alice\data.csv` in code, config files or comments. They fail on every other computer. When they are saved in a notebook's output or a log, they also reveal a username and a folder layout.
- **References outside the project.** `../other-repo/prices.csv` points at a folder that is not part of what you share.
- **Undeclared dependencies.** The code imports `pandas`, but `requirements.txt` never lists it. It runs for you because the package happens to be installed.
- **Files that exist only on your machine.** A gitignored dataset, or a file that was never committed, so a clone does not have it.
- **Settings that exist only on your machine.** The code reads `os.environ["API_KEY"]`, and nobody else has that variable.
- **Leftovers you did not mean to ship.** The name of a private repository or a teammate, notes files for coding assistants (`AGENTS.md`, `PLAN.md`), wording that looks assistant-written, saved error output, notebook cells run out of order.

Checking all of this by hand before you share a project is slow and easy to get wrong.

## What exists today

Several kinds of tools cover part of this.

- **Containers and CI** (Docker, GitHub Actions and similar) give you a clean machine. But you have to write down the dependencies first (a Dockerfile, a workflow file), they copy whatever is in the folder (so a file that was never committed works there and fails for everyone else), and a run stops at the first error. They do not look inside the code for paths, usernames or forbidden words.
- **Notebook runners** such as `nbmake` and `papermill` execute notebooks. Setting up the environment is up to you.
- **Dependency checkers** such as `FawltyDeps` and `deptry` find imports that are not declared (and the reverse). They look at dependencies only, not at paths, saved notebook output or forbidden words.

## What dscforge does

- **It looks at what someone else would get.** In a git repository it checks only the committed files and lists the ones that are not committed. For a folder that is not a git repository it checks the files as they are.
- **It needs no setup.** No Dockerfile, no CI file, not even a requirements file: when a project declares nothing, dscforge prints the package list it infers.
- **It reports everything at once**, with the file and line (or the notebook cell) of each finding, in seconds, without building or running anything.
- **It understands notebooks.** It reads their code, their markdown cells and their saved outputs, so it finds a path or a username that a clean-room run would never show.
- **It can forbid words.** `--forbid` names (a private repository, a teammate's folder, a project code name) must appear nowhere: in code, notebooks, docs, configs, logs, or file and folder names.
- **It writes a plan.** `--plan` saves the findings, each with a one-line "how to fix", as a Markdown checklist. You decide what to change.
- **Its startup check is opt-in and small.** With `--run` it starts each notebook and script in a new empty environment for a short time (30 seconds by default). A program still running at the limit counts as passing, so a project that trains for a day is not trained.
- **It is light.** Pure Python with one dependency (`click`). No Docker.

### Main difference

A clean machine (a container or CI) is a stronger test of "does it run" than a 30-second startup check. dscforge is for the fast, broad, everyday check before you share.

## Install

```bash
pip install dscforge
```

Requires Python 3.11 or newer.

## Quick start

```bash
dscforge my-project                       # read pass: seconds, nothing is installed or executed
dscforge my-project --plan PLAN.md        # also write the findings as a Markdown plan
dscforge my-project --run                 # also start notebooks and scripts in an empty environment
dscforge my-project --run --plan PLAN.md  # both: the startup check and the plan
```

`my-project` is any folder, relative or absolute; leave it out to check the folder you are in. Here is the first command on a small demo project (a git repository with one modified file and one untracked file), with `--forbid bob` added:

```
Checked my-project (committed files only)

Problems
  load_data.py:line 9 [abs-path] absolute path: /Users/alice/data/sales.csv (will not exist on another machine)
  load_data.py:line 10 [outside] path leaves the folder: ../other-repo/prices.csv
  notebooks/analysis.ipynb:cell 1 [forbidden] mentions 'bob'
  load_data.py:line 3 [undeclared] 'pandas' is not in requirements.txt, pyproject.toml, environment.yml or setup.py

Warnings
  AGENTS.md [leftover-file] possibly a leftover agent or working-notes file (matches 'AGENTS.md')
  load_data.py:line 5 [env-var] reads the environment variable 'API_KEY', which someone else will have to set
  notebooks/analysis.ipynb:cell 2 [leaked-path] saved output contains a local path: /Users/alice/work/out.csv
  notebooks/analysis.ipynb:cell 3 [nb-error] saved output is an error
  notebooks/analysis.ipynb:cell 3 [nb-order] execution count 2 after 3

Not committed
  modified: load_data.py (others will not get this version)
  untracked: scratch.py (others will not get this file)

Summary
  4 problems        could prevent this project from working elsewhere   (abs-path 1, forbidden 1, outside 1, undeclared 1)
  5 warnings        worth a look; may be fine                           (env-var 1, leaked-path 1, leftover-file 1, nb-error 1, nb-order 1)
  2 not committed   files that won't be included when this project is shared

Result: problems found. (exit status 1)
```

How to read it:

- The first line says what was checked: the committed files of a git repository, or the files as they are on disk.
- **Problems** are things that could stop the project from working elsewhere or that break a rule you set (`--forbid`). **Warnings** are worth a look but may be fine. Each line is `file:place [rule] message`, where the place is a line number or a notebook cell.
- **Not committed** lists the files others would not get (or would get in an older version).
- **Run notes** (only with `--run`) lists what was still running when the time limit ended.
- The **Summary** counts each group and names the rules behind the numbers. The **Result** line ends with the exit status, which scripts and CI can use (see [Commands and flags](#commands-and-flags)).

With `--run`, the same demo also reports what happened when its three items were started (the timeout was 5 seconds here):

```
  load_data.py [run-missing-package] missing package 'pandas'
  notebooks/analysis.ipynb [run-error] ZeroDivisionError: division by zero

Run notes
  1 of 3 were still running after 5 s, so they were not checked further (this counts as passing): train.py
```

## How it works

dscforge makes a temporary copy of the folder, reads it, optionally starts it, prints what it found, and deletes the copy. Your files are never changed.

1. **Snapshot.** In a git repository with at least one commit it runs `git archive HEAD` for that folder only, so the copy holds exactly the committed files: what someone gets from a clone. Modified and untracked files are listed as "not committed". If the folder is not a git repository, has no commits yet, is ignored by git, or git is not installed, it makes a plain copy of the files on disk. Folders that are not project code are never copied or read: `.venv`, `venv`, `.git`, `__pycache__`, `.ipynb_checkpoints`, `node_modules`, and any virtual or conda environment under any name (a folder with `pyvenv.cfg` or `conda-meta`, or called `site-packages`).
2. **Read pass** (always on). It walks the copy using only the Python standard library and applies the rules in [What it catches](#what-it-catches) to:
   - every `.py` file (strings, comments, imports, a syntax check),
   - every `.ipynb` notebook (each code cell parsed on its own, plus markdown cells, saved outputs, execution order and saved errors),
   - config and script files (`.json .toml .yaml .yml .cfg .ini .sh .ps1 .bat`), where a problem is a failure because programs read them, and
   - docs and logs (`.md .txt .log .rst`), where a problem is a warning.

   Text files over 2 MB are treated as data and skipped.
3. **Startup check** (only with `--run`). It builds an empty virtual environment in the temp folder, installs the packages the project declares (if it declares nothing, only well-known packages inferred from its imports), then starts every notebook and every script that is meant to be run, one at a time, each from its own folder, for at most `--timeout` seconds. A crash is classified (missing package, missing file, any other error). A program still running at the limit is stopped, counts as passing and is listed under "Run notes". The whole process tree is killed on a timeout or Ctrl+C, and the environment is deleted afterwards.
4. **Report.** The findings are printed in groups with a summary, the optional plan file is written, the temporary copy is deleted and the exit status is set.

The program started by `--run` gets a clean environment, like a new machine: only `PATH`, the variables the operating system needs, language settings, and throwaway home and temp folders. Your API keys, tokens and other variables are not passed on, so code that needs one fails here the way it would for someone else. The installer additionally keeps proxy, certificate, `PIP_*` and `UV_*` settings so installs still work.

## Commands and flags

There is one command:

```
dscforge [PATH] [OPTIONS]
```

`PATH` is the folder (project) to check. It can be a relative or absolute path, and you do not need to be inside the folder. If you leave it out, the current directory is checked. It can be a subfolder of a git repository, and it does not have to be a repository at all.

| Option | What it does |
|---|---|
| `--run` | Also start every notebook and script in a new empty environment. This installs packages from the internet and executes the project's code; without it nothing is installed or executed |
| `--timeout SECONDS` | With `--run` (an error without it): a whole number of seconds each notebook or script may run. Default 30. A program still running at the limit counts as passing and is listed in the run notes |
| `--forbid WORD` | One word or phrase that must not appear anywhere, in file contents or in file and folder names. Repeat the flag for more words (`--forbid bob --forbid "private repo"`); a comma is an ordinary character. Matching is case-insensitive and by substring, so `bob` also matches `bobby` |
| `--plan FILE` | Also write the findings, grouped by rule with a "how to fix" line each, as a Markdown plan. A file name is required (`--plan PLAN.md`), it must end in `.md`, and it is resolved against the current directory. It refuses to overwrite an existing file (on Windows and macOS also one that differs only in letter case) |
| `--version` | Print the version and exit |
| `--help` | Show the help and exit |

**Exit status**, for scripts and CI: `0` means no problems were found (warnings do not count), `1` means at least one problem was found, `2` means dscforge could not run (a bad option, no usable temp folder, or an unexpected error, which prints the traceback first), and `3` means it was stopped with Ctrl+C. A job that runs `dscforge .` fails exactly when problems are found.

Examples:

```bash
# check the folder you are in: reads the code only
dscforge

# check a teammate's folder, and fail on mentions of a private repository or a name
dscforge alice/checkpoint --forbid private-repo --forbid charlie

# also write the findings and how to fix them to a Markdown file
dscforge my-project --plan PLAN.md

# also start every notebook and script, two minutes each
dscforge my-project --run --timeout 120
```

**Where files go.** The temporary copy lives in the system temp folder (`%TEMP%` on Windows, `TMPDIR` or `/tmp` on Linux and macOS) and is deleted at the end of every run, including after an error; set `TEMP` or `TMPDIR` to move it. The plan file goes exactly where you point it and names only the checked folder, never its full path. It is never written into the temporary copy; if you write it inside the checked folder, the next run will flag a `PLAN.md` there as a leftover file.

## What it catches

### The read pass (always on)

A **problem** (fail) makes the exit status `1`; a **warning** does not.

| Rule | Level | What it finds, with an example |
|---|---|---|
| `abs-path` | problem | An absolute path in a string in code (or anywhere on a line in a config or script file): a drive letter (`C:\`, `D:/`), a network share (`\\server\share`), `~/`, or a unix root folder (`/Users/ /home/ /mnt/ /Volumes/ /tmp/ /var/ /opt/ /root/ /media/ /private/ /content/ /kaggle/ /workspace/`; the last three are cloud notebook folders). `pd.read_csv("/Users/alice/data.csv")` fails even if the path points inside the folder, because it will not exist on another machine |
| `outside` | problem (warning in docs and logs) | A relative path that climbs out of the checked folder. It is joined onto the file's own folder first: in `scripts/a.py`, `../data/x.csv` stays inside and is fine, `../../x.csv` is flagged. Also checked in config files and in Markdown link targets |
| `forbidden` | problem | A `--forbid` word in code, notebook cells or saved outputs, docs, configs, logs, or a file or folder name |
| `undeclared` | problem | An import that is not standard library, not one of the project's own modules, and not declared in `requirements.txt`, `pyproject.toml` (including `[tool.poetry]`), `environment.yml` or the `install_requires` of `setup.py` |
| `syntax-error` | problem | A `.py` file that cannot be parsed (a typo, or Python 2 code). Checks that need to parse it are skipped |
| `leaked-path` | warning | A local path saved where it ships: a notebook's saved output or markdown cell, a comment in code (`# was C:/Users/alice/x.csv`), or a doc or log. It reveals your folders and username. One finding per cell or file |
| `no-requirements` | warning | None of the declaration files lists any package, while the code imports third-party packages. A warning, so a one-file homework folder does not fail; the message includes a ready-to-paste package list |
| `env-var` | warning | The code reads an environment variable by name (`os.environ["KEY"]`, `os.environ.get("KEY")`, `os.getenv("KEY")`), so it fails where that variable is not set. Common system variables (`PATH`, `HOME`, `USER`, ...) are skipped |
| `local-only` | warning | A data file the code names (`.csv`, `.parquet`, `.json`, `.xlsx`, `.pkl`, `.npy`, ...) that exists on your machine but is not in what you share, for example a gitignored one |
| `potential-ai-leftover` | warning | A word or phrase from a list of assistant-style wording, agent file names and AI tool names, or an emoji, in code, notebooks, markdown or text files (see below). Repeats in one file are reported once, with the number of other places |
| `leftover-file` | warning | A file or folder whose name is on a list of agent and working-note names: `AGENTS.md`, `CLAUDE.md`, `PLAN.md`, `.cursor`, `.claude`, and many more, in any folder and any letter case, also as `.txt`, `.yaml`, `.yml`, `.json` or `.toml`. A matching folder is reported once and not searched |
| `nb-error` | warning | A notebook cell with a saved error output |
| `nb-order` | warning | Code cells whose execution counts are not increasing, so the notebook was run out of order |
| `unreadable` | warning | A file that could not be copied (locked, unreadable, a broken link), so nothing in it was checked |
| `more-findings` | warning | A text file has more distinct findings than are listed; the rest are counted in one line |

**About the AI-leftover rules.** They are lists of phrases and names, not a detector. A hit means "possibly AI-written", never "was AI-written", and a file with no hits proves nothing. They prefer false alarms over misses: a person can write `let's explore the data` or use an emoji. The lists are plain text files inside the package (`ai_phrases.txt`, `leftover_files.txt`), with comments that explain the format and a few groups of phrases switched off with `//` because they are common in ordinary writing; delete the slashes to be stricter. To change the lists, edit them in a source checkout (`pip install -e .`); an upgrade replaces edits made inside an installed copy.

### The startup check (`--run`)

| Rule | Level | What it finds |
|---|---|---|
| `install-failed` | problem (warning for a guessed name) | The declared packages, or the notebook runner, could not be installed (the message has the installer's last error line), or the environment could not be created. For a project that declares nothing, a guessed name that is not a well-known package is reported as a warning and not installed |
| `run-missing-package` | problem | The program imported a package that is not installed in the clean environment |
| `run-missing-file` | problem | The program opened a file that is not in what you share |
| `run-error` | problem | Any other crash; the message is the last line of the error. This includes an environment variable that is not set |
| `run-skipped` | warning | The program needs keyboard input or command-line arguments, so it was not run to the end. This is not a failure |

## The plan file

`--plan PLAN.md` writes one Markdown file: a header (the folder checked, whether the startup check ran, the counts), a short "please check these by hand" note, then the findings grouped by rule, each rule with a one-line fix and one tick box per finding, then "Not committed" and "Run notes". It reports and suggests; it changes nothing. An excerpt:

```markdown
## Problems

### abs-path

Replace the absolute path with a path relative to the project folder (for example `pathlib.Path(__file__).parent / ...`).

- [ ] `load_data.py:line 9`: absolute path: /Users/alice/data/sales.csv (will not exist on another machine)

### undeclared

Add the package to requirements.txt (or pyproject.toml dependencies), or remove the import if it is unused.

- [ ] `load_data.py:line 3`: 'pandas' is not in requirements.txt, pyproject.toml, environment.yml or setup.py
```

The "check these by hand" note is there because dscforge is a rule-based checker: some items may be **false alarms** (for example an emoji a person wrote, or a path that is only mentioned in documentation), and some real problems may be **missed** (paths built while the program runs, files of types it does not read). An empty list does not prove the project is clean. Verify each item before you change anything.

## Things to know

- **Relative paths** are resolved against the file's own folder, and the startup check starts every notebook and script from its own folder, so the two passes agree. Python really resolves relative paths from wherever you launch it, so a script that you normally launch from the project root with `../data` would escape in real life and the check would miss it.
- **Local modules.** An import is not reported as undeclared if it names a file or folder next to the importing file, or in any folder above it up to the checked folder, a package under `src/`, or the project's own `[project] name`.
- **Declared packages** are read from `requirements.txt` (following `-r other.txt` includes, relative to the including file; UTF-8 and UTF-16 files both work), from `pyproject.toml` (`[project] dependencies`, `[dependency-groups]`, and `[tool.poetry]` tables), from the `install_requires` list of `setup.py` (read by parsing, never executed), and from `environment.yml`. A `requirements.txt` that exists but is empty counts as "declared", so every third-party import is then reported as `undeclared` instead of one `no-requirements` warning.
- **Import names** that differ from package names are mapped with a small built-in table (`sklearn` is `scikit-learn`, `cv2` is `opencv-python`, `PIL` is `pillow`, `yaml` is `pyyaml`, and a few more). Other names are assumed to match their package.
- **What `--run` installs.** If the project declares packages, exactly those are installed, so anything the code imports but nobody declared fails. If it declares nothing, only well-known packages (the 1,500 most downloaded on PyPI) that its imports suggest are installed, one at a time; any other guessed name could be one of your own modules, so it is reported, not installed. `uv` is used for installing when it is available, otherwise `pip`.
- **What `--run` starts.** Every notebook, top to bottom, and every `.py` file that has an `if __name__ == "__main__"` block or that no other file imports. Other `.py` files are only imported. `setup.py` and `conftest.py` are never run. Items run one at a time in path order, so `01_clean.ipynb` can write a file that `02_model.ipynb` reads. Data files that exist on your machine but not in what you share are copied into the temporary copy (files over 500 MB are not) so the run gets past them; each one is also reported as `local-only`.
- **Notebooks under `--run`** run with a headless plotting backend; nothing can read the keyboard.

## Known limits

- **`--run` executes the project's code for real, and there is no sandbox.** The clean environment hides your variables and gives the code throwaway home and temp folders, but nothing stops code from writing to an absolute path on your real disk, deleting files or reaching the network. Leave `--run` off for code you do not trust. A real sandbox would need a different tool on each operating system, so it is not attempted.
- **The startup check only sees the start.** A crash that happens after the time limit (saving a model after hours of training) is not seen, and neither is a notebook that needs longer than the limit to reach a bad cell. Raise `--timeout` to look deeper.
- **The startup check runs on your machine.** An absolute path to a file that exists on your computer still works there; only the read pass catches it (`abs-path`). A container or CI would catch it at run time.
- **The notebook runner hides a few undeclared imports.** To run notebooks, `ipykernel`, `nbclient` and `nbformat` are installed into the throwaway environment, which brings in IPython, traitlets, tornado and others. A notebook that imports one of those without declaring it passes the startup check; the read pass (`undeclared`) is the authority on declared packages.
- **The environment uses the Python that runs dscforge** (3.11 or newer), and the project's own required Python version is not read. On Debian and Ubuntu, `--run` needs Python's venv module, which is a separate package there (for example `apt install python3.12-venv`); dscforge names the package when it is missing.
- **Paths built while the program runs** (f-strings, `os.path.join` with variables) are not seen, and neither are `Path(__file__).parents[N]` or `sys.path.insert` of a computed path.
- **Not everything is read.** HTML, JavaScript, CSS and data files (`.csv`, `.parquet`, ...) are not scanned. `setup.cfg` and `Pipfile` do not count as declaring packages; `environment.yml` is read by a small line reader that may miss unusual layouts, and only a literal `install_requires=[...]` list in `setup.py` is understood. Git LFS files arrive as pointer files. Network share paths are recognised only at the start of a string.
- **No exclude option.** Checking a folder that holds several projects or many example files reports a lot of noise.
- **False alarms are expected.** A hardcoded default path that an environment variable can override is still flagged, and a web route such as `"/content/"` looks like a cloud notebook folder. That is why the plan asks you to check each item.
- **The AI lists are a starting set.** They will miss some assistant-written text and flag some human writing.

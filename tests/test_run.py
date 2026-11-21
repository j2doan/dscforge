import json
import os
import subprocess
import sys

import pytest

from dscforge.read import Finding, check, find_local_data, install_args
from click.testing import CliRunner

from dscforge import run
from dscforge.cli import main
from dscforge.run import classify, plan_scripts, run_pass


def write(root, files):
    for name, text in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def pairs(findings):
    return sorted((f.rule, f.file) for f in findings)


# --- pure functions ---

def test_classify_missing_package_and_file():
    assert classify(1, "Traceback...\nModuleNotFoundError: No module named 'pandas.core'\n") == (
        "fail", "run-missing-package", "missing package 'pandas'")
    assert classify(1, "FileNotFoundError: [Errno 2] No such file or directory: 'data/x.csv'\n") == (
        "fail", "run-missing-file", "cannot open 'data/x.csv'")


def test_classify_notebook_error_with_ansi_codes():
    text = ("nbclient.exceptions.CellExecutionError: An error occurred while executing the following cell:\n"
            "\x1b[1;31mNameError\x1b[0m: name 'x' is not defined\n")
    assert classify(1, text) == ("fail", "run-error", "NameError: name 'x' is not defined")
    text = "\x1b[0;31mModuleNotFoundError\x1b[0m: No module named 'seaborn'\n"
    assert classify(1, text)[1] == "run-missing-package"


def test_classify_timeout_arguments_and_input():
    # still running when stopped counts as passing: no finding at all
    assert classify(None, "") is None
    assert classify(1, "nbclient.exceptions.CellTimeoutError: ...") is None
    assert classify(2, "usage: tool.py [-h] input\ntool.py: error: the following arguments are required")[1] == "run-skipped"
    assert classify(1, "EOFError: EOF when reading a line\n")[1] == "run-skipped"
    assert classify(3, "") == ("fail", "run-error", "exited with code 3")


def test_plan_scripts_picks_scripts_modules_and_packages(tmp_path):
    write(tmp_path, {
        "run_all.py": "import helpers\nprint(1)\n",             # nothing imports it: script
        "helpers.py": "def f(): pass\n",                         # imported by run_all: only loaded
        "tool.py": "def f(): pass\nif __name__ == '__main__':\n    f()\n",  # has a main block: script even if imported
        "other.py": "import tool\n",
        "pkg/__init__.py": "",
        "pkg/mod.py": "from . import sibling\n",                 # relative import: loaded by dotted name
        "pkg/sibling.py": "x = 1\n",
        "setup.py": "print('install')\n", "conftest.py": "",   # never run
        "broken.py": "def (:\n",                                 # unparsable: left to the syntax-error rule
        ".venv/lib/x.py": "print(1)\n",                          # skipped folder
    })
    assert plan_scripts(tmp_path) == [
        ("helpers.py", "import"), ("other.py", "script"), ("pkg/__init__.py", "import"),
        ("pkg/mod.py", "package"), ("pkg/sibling.py", "import"), ("run_all.py", "script"), ("tool.py", "script")]
    assert ("run_all.py", "script") not in plan_scripts(tmp_path, {"run_all.py"})


def test_install_args(tmp_path):
    assert install_args(tmp_path) == []
    write(tmp_path, {"requirements.txt": "# nothing\n\n"})
    assert install_args(tmp_path) == []
    write(tmp_path, {"requirements.txt": "pandas>=2\n",
                     "pyproject.toml": '[project]\nname="x"\ndependencies=["numpy>=1"]\n[dependency-groups]\ndev=["pytest"]\n'})
    assert install_args(tmp_path) == ["-r", "requirements.txt", "numpy>=1", "pytest"]


def test_find_local_data_only_reports_what_is_missing_from_the_copy(tmp_path):
    source, copy = tmp_path / "source", tmp_path / "copy"
    for root in (source, copy):
        write(root, {"scripts/a.py": ('a = "../data/raw.csv"\nb = "../data/kept.csv"\nc = "../../outside.csv"\n'
                                      'd = "/abs/x.csv"\ne = "http://host/x.csv"\nf = "../data/gone.csv"\n'
                                      'g = "notes.txt"\n')})
    write(source, {"data/raw.csv": "x", "data/kept.csv": "x", "outside.csv": "x"})
    write(copy, {"data/kept.csv": "x"})
    assert find_local_data(copy, source) == [("scripts/a.py", "line 1", "data/raw.csv")]


# --- the run pass, with a real throwaway environment (no network: nothing is installed) ---

def test_run_pass_reports_failures_and_copies_local_data(tmp_path):
    source, copy = tmp_path / "source", tmp_path / "copy"
    files = {
        "requirements.txt": "",
        "ok.py": "print('fine')\n",
        "reads_data.py": "print(open('data/in.csv').read())\n",    # local-only data: copied in, so it passes
        "missing_file.py": "open('data/nope.csv')\n",
        "missing_pkg.py": "import notarealpackage_xyz\n",
        "boom.py": "raise ValueError('bad value')\n",
        "needs_args.py": "import argparse\np = argparse.ArgumentParser(prog='needs_args')\n"
                         "p.add_argument('x')\np.parse_args()\n",
        "sub/uses_parent.py": "print(open('../data/in.csv').read())\n",
    }
    write(source, files)
    write(copy, files)
    write(source, {"data/in.csv": "a,b\n1,2\n"})
    data = find_local_data(copy, source)
    assert ("reads_data.py", "line 1", "data/in.csv") in data
    log = []
    out, notes = run_pass(copy, source, data, [], 1, log.append)
    assert notes == []
    assert sorted((f.rule, f.file, f.level) for f in out) == [
        ("run-error", "boom.py", "fail"), ("run-missing-file", "missing_file.py", "fail"),
        ("run-missing-package", "missing_pkg.py", "fail"),
        ("run-skipped", "needs_args.py", "warn")]  # argparse exits 2 with "usage:"
    assert (copy / "data" / "in.csv").is_file()
    assert any(m.startswith("creating an empty environment (Python 3.") for m in log)
    assert "starting ok.py ..." in log and "7 notebooks and scripts to start, 60 s each" in log


def test_run_pass_timeout_kills_the_process_and_does_not_fail(tmp_path):
    write(tmp_path, {"requirements.txt": "", "forever.py": "while True:\n    pass\n", "quick.py": "print(1)\n"})
    out, notes = run_pass(tmp_path, tmp_path, [], [], 0.05, lambda m: None)  # 3 seconds
    assert out == []  # still running at the limit is not a finding
    assert notes == ["1 of 2 were still running after 3 s, so they were not checked further (this counts as passing): forever.py"]


def test_run_pass_nothing_to_run_and_syntax_error_files_are_skipped(tmp_path):
    write(tmp_path, {"readme.md": "hi"})
    assert run_pass(tmp_path, tmp_path, [], [], 1, lambda m: None) == ([], [])
    write(tmp_path, {"bad.py": "def (:\n"})
    findings = check(tmp_path, [])
    assert [f.rule for f in findings] == ["syntax-error"]
    # the only script has a syntax error, so no environment is even created
    log = []
    assert run_pass(tmp_path, tmp_path, [], findings, 1, log.append) == ([], [])
    assert log == ["nothing to run"]


@pytest.mark.skipif(not os.environ.get("DSCFORGE_SLOW"), reason="installs packages from the network; set DSCFORGE_SLOW=1")
def test_run_pass_notebooks_with_a_real_install(tmp_path):
    def nb(*cells):
        return json.dumps({"cells": [{"cell_type": "code", "source": c, "execution_count": None, "outputs": [],
                                      "metadata": {}} for c in cells],
                           "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"}},
                           "nbformat": 4, "nbformat_minor": 5})
    write(tmp_path, {"requirements.txt": "", "good.ipynb": nb("x = 1", "print(x + 1)", "import sys; assert sys.prefix != sys.base_prefix and 'dscforge_env' in sys.prefix, sys.prefix"),
                     "bad.ipynb": nb("y = 1", "print(undefined_name)"),
                     "sub/path.ipynb": nb("open('../nothere.csv')")})
    out, _ = run_pass(tmp_path, tmp_path, [], [], 2, lambda m: None)
    if any(f.rule == "install-failed" for f in out):
        pytest.skip("could not install the notebook runner: " + out[0].message)
    assert pairs(out) == [("run-error", "bad.ipynb"), ("run-missing-file", "sub/path.ipynb")]
    assert out[0].message.startswith("NameError") or out[1].message.startswith("NameError")


def test_finding_shape_is_unchanged():
    assert Finding("warn", "a.py", "", "run-timeout", "m").rule == "run-timeout"


# --- safety: interruption, other environment names, src layout, the default command ---

def test_exec_kills_the_child_tree_on_ctrl_c(tmp_path, monkeypatch):
    made = []

    class Interrupted(subprocess.Popen):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            made.append(self)
            self.first = True

        def wait(self, timeout=None):
            if self.first:  # what Ctrl+C does to the parent's wait
                self.first = False
                raise KeyboardInterrupt
            return super().wait(timeout)

    monkeypatch.setattr(run.subprocess, "Popen", Interrupted)
    with pytest.raises(KeyboardInterrupt):
        run._exec([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path, os.environ.copy(), 30)
    assert made[0].poll() is not None  # the child is gone, not left running


def test_environment_folders_with_any_name_are_never_read_or_run(tmp_path):
    write(tmp_path, {
        "requirements.txt": "", "main.py": "print(1)\n",
        "myenv/pyvenv.cfg": "home = x\n",
        "myenv/lib/site-packages/dep.py": "import badimport\nraise SystemExit(5)\n",
        "conda_env/conda-meta/history": "", "conda_env/lib/x.py": "raise SystemExit(6)\n",
        "vendor/site-packages/y.py": "raise SystemExit(7)\n",
    })
    assert plan_scripts(tmp_path) == [("main.py", "script")]
    assert check(tmp_path, []) == []
    assert find_local_data(tmp_path, tmp_path) == []


def test_src_layout_and_project_name_are_local_modules(tmp_path):
    write(tmp_path, {"pyproject.toml": '[project]\nname = "my-analysis"\ndependencies = ["rich"]\n',
                     "src/mypkg/__init__.py": "",
                     "notebooks/a.py": "import mypkg\nimport my_analysis\nimport numpy\n"})
    f = check(tmp_path, [])
    assert [(x.rule, x.message.split("'")[1]) for x in f] == [("undeclared", "numpy")]


def test_default_command_runs_the_code_and_exits_1_on_a_crash(tmp_path):
    proj = tmp_path / "proj"
    write(proj, {"requirements.txt": "", "ok.py": "print('fine')\n", "boom.py": "raise RuntimeError('bad')\n"})
    r = CliRunner().invoke(main, [str(proj), "--run"])
    assert r.exit_code == 1
    assert "boom.py [run-error] RuntimeError: bad" in r.output
    assert "ok.py [" not in r.output  # the passing script is not reported
    assert "creating an empty environment" in r.output


def test_plain_command_never_starts_anything(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    write(proj, {"requirements.txt": "", "train.py": "while True:\n    pass\n"})

    def forbidden(*args, **kwargs):
        raise AssertionError("the run pass must not start without --run")

    monkeypatch.setattr("dscforge.cli.run_pass", forbidden)
    r = CliRunner().invoke(main, [str(proj)])
    assert r.exit_code == 0 and "No findings." in r.output


def test_run_notes_are_reported_and_do_not_fail_the_run(tmp_path):
    proj = tmp_path / "proj"
    write(proj, {"requirements.txt": "", "train.py": "while True:\n    pass\n", "ok.py": "print(1)\n"})
    plan = tmp_path / "fix.md"
    r = CliRunner().invoke(main, [str(proj), "--run", "--timeout", "3", "--plan", str(plan)])
    assert r.exit_code == 0
    assert "Run notes" in r.output and "1 of 2 were still running after 3 s" in r.output
    text = plan.read_text()
    assert "## Run notes" in text and "nothing to fix" in text and "train.py" in text


def test_guessed_installs_are_limited_to_well_known_packages(tmp_path, monkeypatch):
    installed = []
    monkeypatch.setattr(run, "_install", lambda py, args, cwd, env: (installed.append(list(args)) or (0, "")))
    write(tmp_path, {"ok.py": "print(1)\n"})
    guess = Finding("warn", "", "", "no-requirements",
                    "no requirements.txt or pyproject.toml dependencies; add:\nbenchmark\nnumpy\nscikit-learn\nanalysis")
    out, _ = run_pass(tmp_path, tmp_path, [], [guess], 1, lambda m: None)
    assert installed == [["numpy"], ["scikit-learn"]]  # `benchmark` and `analysis` were never sent to PyPI
    assert sorted(f.message.split("'")[1] for f in out if f.rule == "install-failed") == ["analysis", "benchmark"]
    assert all(f.level == "warn" for f in out)


def test_declared_packages_are_installed_as_declared(tmp_path, monkeypatch):
    installed = []
    monkeypatch.setattr(run, "_install", lambda py, args, cwd, env: (installed.append(list(args)) or (0, "")))
    write(tmp_path, {"ok.py": "print(1)\n", "requirements.txt": "benchmark==1.0\n"})
    out, _ = run_pass(tmp_path, tmp_path, [], [], 1, lambda m: None)
    assert installed == [["-r", "requirements.txt"]] and out == []  # what a project declares is installed, whatever it is


def test_known_packages_come_from_the_bundled_public_list():
    names = run.KNOWN_PACKAGES
    assert len(names) > 1000 and {"numpy", "pandas", "requests", "scikit-learn"} <= names
    assert all(n == n.lower() and not n.startswith("#") for n in names)


def test_project_code_gets_a_clean_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DSCFORGE_SECRET_TEST", "hunter2")
    code = ("import os, sys\n"
            "assert 'DSCFORGE_SECRET_TEST' not in os.environ, 'secret leaked'\n"
            "assert 'dscforge_env_' in os.path.expanduser('~'), os.path.expanduser('~')\n"
            "assert 'dscforge_env_' in os.environ['TEMP' if os.name == 'nt' else 'TMPDIR']\n"
            "assert os.environ.get('PATH')\n")
    write(tmp_path, {"requirements.txt": "", "clean.py": code})
    out, notes = run_pass(tmp_path, tmp_path, [], [], 1, lambda m: None)
    assert out == [] and notes == []


def test_a_variable_that_is_not_set_fails_like_on_someone_elses_machine(tmp_path, monkeypatch):
    monkeypatch.setenv("DSCFORGE_NEEDS_THIS", "set on this machine")
    write(tmp_path, {"requirements.txt": "", "needs.py": "import os\nprint(os.environ['DSCFORGE_NEEDS_THIS'])\n"})
    out, _ = run_pass(tmp_path, tmp_path, [], [], 1, lambda m: None)
    assert [(f.rule, f.message) for f in out] == [("run-error", "KeyError: 'DSCFORGE_NEEDS_THIS'")]


def test_clean_env_keeps_the_basics_and_the_installer_keeps_network_settings(tmp_path, monkeypatch):
    for k, v in {"PATH": "/bin", "AWS_SECRET_ACCESS_KEY": "x", "HTTPS_PROXY": "http://p", "PIP_INDEX_URL": "https://i",
                 "LC_ALL": "C", "HOME": "/real/home"}.items():
        monkeypatch.setenv(k, v)
    code_env = run._clean_env(tmp_path, installer=False)
    assert code_env["PATH"] == "/bin" and code_env["LC_ALL"] == "C"
    assert "AWS_SECRET_ACCESS_KEY" not in code_env and "HTTPS_PROXY" not in code_env and "PIP_INDEX_URL" not in code_env
    assert code_env["HOME"] == str(tmp_path / "home") and code_env["TEMP"] == str(tmp_path / "tmp")
    inst_env = run._clean_env(tmp_path, installer=True)
    assert inst_env["HTTPS_PROXY"] == "http://p" and inst_env["PIP_INDEX_URL"] == "https://i" and inst_env["HOME"] == "/real/home"
    assert "AWS_SECRET_ACCESS_KEY" not in inst_env


def test_missing_venv_module_names_the_package_for_this_python():
    text = ("The virtual environment was not created successfully because ensurepip is not\navailable.  On Debian/Ubuntu "
            "systems, you need to install the python3-venv\npackage using the following command.\n\n    apt install "
            "python3.14-venv\n\nFailing command: /tmp/x/env/bin/python3\n")
    assert run._venv_failure(text) == ("this Python cannot create environments with pip "
                                       "(on Debian or Ubuntu, run: sudo apt install python3.14-venv)")
    assert "install your system's python venv package" in run._venv_failure("ensurepip is not available")  # no apt hint
    assert run._venv_failure("Error: Permission denied: 'x'\n") == "Error: Permission denied: 'x'"

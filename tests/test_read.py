import json

from click.testing import CliRunner

from dscforge.cli import main
from dscforge.read import check


# write {name: text} into root and run check
def run(root, files, forbid=()):
    for name, text in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return check(root, list(forbid))


def rules(findings):
    return sorted(f.rule for f in findings)


def notebook(cells):
    return json.dumps({"cells": cells, "nbformat": 4, "nbformat_minor": 5})


def code(src, count=None, outputs=()):
    return {"cell_type": "code", "source": src, "execution_count": count, "outputs": list(outputs)}


def test_clean_project_has_no_findings(tmp_path):
    files = {"requirements.txt": "pandas>=2\n", "helper.py": "X = 1\n",
             "a.py": "import os\nimport pandas\nimport helper\nprint('hi')\n",
             "nb.ipynb": notebook([code("%matplotlib inline\nimport helper", 1), code("x = 1", 2)])}
    assert run(tmp_path, files) == []


def test_abs_path(tmp_path):
    f = run(tmp_path, {"a.py": 'p = "C:\\\\data\\\\x.csv"\nq = "/home/me/x"\nr = "~/x"\ns = "data/x.csv"\n'})
    assert [(x.rule, x.location) for x in f] == [("abs-path", "line 1"), ("abs-path", "line 2"), ("abs-path", "line 3")]


def test_abs_path_other_drives_and_roots(tmp_path):
    src = ('a = "D:\\\\data\\\\x.csv"\nb = "E:/data/x.csv"\nc = r"\\\\server\\share\\x.csv"\n'
           'd = "/mnt/data/x"\ne = "/Volumes/usb/x"\nf = "c:\\\\x\\\\y"\n'
           'ok1 = "http://host/home/x"\nok2 = "A:\\n"\nok3 = "data/x.csv"\n')
    f = run(tmp_path, {"a.py": src})
    assert [(x.rule, x.location) for x in f] == [("abs-path", f"line {i}") for i in range(1, 7)]


def test_outside(tmp_path):
    f = run(tmp_path, {"a.py": 'a = "../../other/x.csv"\nb = "../x.csv"\nc = "x/y.csv"\nd = ".."\ne = "..."\n'})
    assert [(x.rule, x.location) for x in f] == [("outside", "line 1"), ("outside", "line 2")]


def test_outside_resolves_against_the_files_folder(tmp_path):
    src = ('a = "../data/x.csv"\nb = "../../x.csv"\nc = "..\\\\data\\\\x.csv"\n'
           'd = "../scripts/../data/y.csv"\ne = "../../../z"\nf = "./data/x.csv"\n')
    f = run(tmp_path, {"scripts/deep/a.py": src, "data/x.csv": "", "scripts/data/x.csv": ""})
    # scripts/deep/ + ../../x.csv stays inside; only the three-level climb leaves the folder
    assert [(x.rule, x.location) for x in f] == [("outside", "line 5")]
    g = run(tmp_path / "flat", {"scripts/a.py": 'a = "../data/x.csv"\nb = "../../x.csv"\n'})
    assert [(x.rule, x.location) for x in g] == [("outside", "line 2")]


def test_forbidden_content_and_names(tmp_path):
    f = run(tmp_path, {"a.py": "# from SecretRepo\n", "secretrepo/b.txt": "x", "ok.py": "x = 1\n"}, ["secretrepo"])
    assert sorted(x.file for x in f) == ["a.py", "secretrepo"]  # the folder name; b.txt is not scanned for content
    assert run(tmp_path / "clean", {"c.py": "x = 1\n"}, ["nothere"]) == []


def test_undeclared(tmp_path):
    f = run(tmp_path, {"requirements.txt": "# c\nscikit_learn==1.0 # x\nPandas[extra]>=2\n-r other.txt\n",
                       "a.py": "import sklearn\nimport pandas\nimport numpy\nimport numpy\nfrom PIL import Image\n"})
    assert [(x.rule, x.file, x.message.split("'")[1]) for x in f] == [
        ("undeclared", "a.py", "numpy"), ("undeclared", "a.py", "PIL")]


def test_undeclared_local_module_next_to_file(tmp_path):
    f = run(tmp_path, {"requirements.txt": "", "sub/a.py": "import util\nimport pkg\n",
                       "sub/util.py": "", "pkg/__init__.py": ""})
    assert f == []


def test_undeclared_from_pyproject(tmp_path):
    toml = '[project]\nname="x"\ndependencies=["numpy>=1"]\n[dependency-groups]\ndev=["pytest", {include-group="x"}]\n'
    assert run(tmp_path, {"pyproject.toml": toml, "a.py": "import numpy, pytest\n"}) == []
    f = run(tmp_path, {"b.py": "import rich\n"})
    assert rules(f) == ["undeclared"]


def test_no_requirements(tmp_path):
    f = run(tmp_path, {"a.py": "import sklearn\nimport pandas\nimport os\n"})
    assert rules(f) == ["no-requirements"] and f[0].level == "warn"
    assert f[0].file == "" and f[0].message.endswith("\npandas\nscikit-learn")
    # nothing third-party: nothing to report
    assert run(tmp_path / "empty", {"a.py": "import os\n"}) == []


def test_unparsable_py_file_is_a_syntax_error_finding(tmp_path):
    f = run(tmp_path, {"requirements.txt": "", "a.py": "import numpy\ndef (:\n", "ok.py": "x = 1\n"})
    assert [(x.rule, x.file, x.location, x.level) for x in f] == [("syntax-error", "a.py", "line 2", "fail")]


def test_notebook_cell_that_cannot_parse_is_not_a_syntax_error(tmp_path):
    nb = notebook([code("%%bash\nls -la | wc", 1), code("x = (", 2)])
    assert run(tmp_path, {"requirements.txt": "", "nb.ipynb": nb}) == []


def test_ai_leftover(tmp_path):
    f = run(tmp_path, {"a.py": "# see PLAN.md\n# As requested, done\nx = 'ok \U0001F600'\ny = 1\n"})
    assert [(x.rule, x.location) for x in f] == [("potential-ai-leftover", "line 1"), ("potential-ai-leftover", "line 2"),
                                                 ("potential-ai-leftover", "line 3")]
    assert run(tmp_path / "clean", {"a.py": "# requested by a user, in a real place\n"}) == []


def test_leftover_file(tmp_path):
    flagged = ["PLAN.md", "agents.md", "sub/CLAUDE.md"]
    fine = ["Untitled1.ipynb", "model_final.py", "x_v2.py", "a copy.py", "results_v2.csv", "ok.py"]
    f = run(tmp_path, {n: "" for n in flagged + fine})
    assert sorted(x.file for x in f if x.rule == "leftover-file") == sorted(flagged)


def test_notebook_error_and_order(tmp_path):
    err = {"output_type": "error", "ename": "E", "evalue": "v", "traceback": ["boom"]}
    nb = notebook([code("x = 1", 3), code("y = 2", 2, [err]), code("z = 3", None), code("w = 4", 4)])
    f = run(tmp_path, {"requirements.txt": "", "nb.ipynb": nb})
    assert [(x.rule, x.location) for x in f] == [("nb-error", "cell 2"), ("nb-order", "cell 2")]


def test_notebook_text_rules(tmp_path):
    out = {"output_type": "stream", "name": "stdout", "text": ["from /home/x\n", "Secret\n"]}
    md = {"cell_type": "markdown", "source": "per the plan we go"}
    nb = notebook([md, code(["p = '/Users/me/x'\n"], 1, [out])])
    f = run(tmp_path, {"requirements.txt": "", "nb.ipynb": nb}, ["secret"])
    assert sorted((x.rule, x.location) for x in f) == [
        ("abs-path", "cell 2"), ("forbidden", "cell 2"), ("leaked-path", "cell 2"),
        ("potential-ai-leftover", "cell 1")]


# end to end through the CLI (no git: plain copy)
def test_cli_exit_codes_and_plan(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("p = '/home/me/x'\n")
    plan = tmp_path / "plan.md"
    r = CliRunner().invoke(main, [str(proj), "--plan", str(plan)])
    assert r.exit_code == 1
    assert "Problems" in r.output and "a.py:line 1 [abs-path]" in r.output
    text = plan.read_text()
    assert "- [ ] `a.py:line 1`: absolute path" in text and "1 problem, 0 warnings" in text

    (proj / "a.py").write_text("x = 1  # as requested\n")
    r = CliRunner().invoke(main, [str(proj)])
    assert r.exit_code == 0 and "Warnings" in r.output

    (proj / "a.py").write_text("x = 1\n")
    r = CliRunner().invoke(main, [str(proj)])
    assert r.exit_code == 0 and "No findings." in r.output


def test_import_of_package_in_ancestor_folder_is_local(tmp_path):
    f = run(tmp_path, {"requirements.txt": "", "proj/src/__init__.py": "", "proj/nb/a.py": "from src import x\n"})
    assert f == []


def test_plan_groups_by_rule_with_fix_and_package_list(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("import pandas\np = '/home/me/x'  # as requested\n")
    out = tmp_path / "fix.md"
    r = CliRunner().invoke(main, [str(proj), "--plan", str(out)])
    assert r.exit_code == 1
    text = out.read_text()
    assert "## Rules" not in text and "re-run `dscforge" not in text  # a plain report, no agent rules block
    assert "### abs-path" in text and "relative to the project folder" in text
    assert "- [ ] `a.py:line 2`: absolute path: /home/me/x" in text
    assert "### no-requirements" in text and "  pandas" in text
    assert "## Warnings" in text and "### potential-ai-leftover" in text
    assert str(tmp_path) not in text and "`proj`" in text  # folder name only, no machine path


def test_plan_is_written_into_the_current_directory_and_bare_plan_is_an_error(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("p = '/home/me/x'\n")
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.chdir(out)
    CliRunner().invoke(main, [str(proj), "--plan", "PLAN.md"])
    text = (out / "PLAN.md").read_text()
    assert str(tmp_path) not in text and "proj" in text
    r = CliRunner().invoke(main, [str(proj), "--plan"])  # no file name
    assert r.exit_code == 2 and "--plan needs a file name ending in .md, for example --plan PLAN.md" in r.output
    assert "Problems" not in r.output  # nothing was checked


def test_plan_never_overwrites_even_with_a_different_letter_case(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("p = '/home/me/x'\n")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "plan.md").write_text("mine")
    same_file = (tmp_path / "PLAN.md").exists()  # Windows and macOS ignore letter case in file names, Linux does not
    for name in ("plan.md", "PLAN.md") if same_file else ("plan.md",):
        r = CliRunner().invoke(main, [str(proj), "--plan", name])
        assert r.exit_code == 2 and "already exists" in r.output and "Problems" not in r.output
        assert (tmp_path / "plan.md").read_text() == "mine"
    r = CliRunner().invoke(main, [str(proj), "--plan", str(tmp_path / "nope" / "p.md")])
    assert r.exit_code == 2 and "does not exist" in r.output


# every bad --plan value stops with exit code 2 before any checking, and writes nothing
def test_plan_value_rules(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("p = '/home/me/x'\n")
    (tmp_path / "docs").mkdir()
    monkeypatch.chdir(tmp_path)
    bad = {"PLAN.txt": ".md", "PLAN": ".md", "PLAN.md.txt": ".md", ".md": "needs a name", "  ": "needs a file name",
           "docs": "is a folder", ".": "is a folder", "out/": "is a folder", "nope/PLAN.md": "does not exist",
           "proj": "is a folder, not a file"}
    for value, text in bad.items():
        r = CliRunner().invoke(main, [str(proj), "--plan", value])
        assert r.exit_code == 2 and text in r.output and "Summary" not in r.output, value
    for ok in ("BANANA.md", "shout.MD", "docs/x.md", "../" + tmp_path.name + "/up.md"):
        r = CliRunner().invoke(main, [str(proj), "--plan", ok])
        assert r.exit_code == 1 and "Summary" in r.output, ok
        assert (tmp_path / ok).exists() or (tmp_path / ok).resolve().exists()


def test_missing_temp_folder_stops_with_a_clear_message(tmp_path, monkeypatch):
    import tempfile
    (tmp_path / "a.py").write_text("x = 1\n")
    def broken(*a, **k):
        raise FileNotFoundError("no temp")
    monkeypatch.setattr(tempfile, "mkdtemp", broken)
    r = CliRunner().invoke(main, [str(tmp_path)])
    assert r.exit_code == 2 and "usable temporary folder" in r.output and "TEMP" in r.output


def test_plan_has_the_check_by_hand_notice(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("x = 1\n")
    out = tmp_path / "PLAN.md"
    CliRunner().invoke(main, [str(proj), "--plan", str(out)])
    text = out.read_text()
    assert "## Please check these by hand" in text and "False alarms" in text and "Misses" in text


def test_summary_is_plain_language_with_per_rule_counts(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("p = '/home/me/x'\nq = '/home/me/y'\nz = 1  # as requested\n")
    r = CliRunner().invoke(main, [str(proj)])
    assert "2 problems" in r.output and "could prevent this project from working elsewhere" in r.output
    assert "abs-path 2" in r.output and "1 warning" in r.output and "potential-ai-leftover 1" in r.output
    assert "files that won't be included when this project is shared" in r.output
    assert "Result: problems found. (exit status 1)" in r.output
    (proj / "a.py").write_text("x = 1\n")
    r = CliRunner().invoke(main, [str(proj)])
    assert "Result: no problems found." in r.output and r.exit_code == 0


def test_ai_phrases_come_from_the_text_file(tmp_path):
    f = run(tmp_path, {"a.py": "# Import necessary libraries\n# feel free to change\nx = 1  # hope this helps\ny = 2\n"})
    assert [(x.rule, x.location) for x in f] == [("potential-ai-leftover", "line 1"), ("potential-ai-leftover", "line 2"),
                                                 ("potential-ai-leftover", "line 3")]
    # "step 1:" was dropped from the list: ordinary prose is not flagged
    assert run(tmp_path / "clean", {"a.py": "# Step 1: load the data\n"}) == []
    from dscforge.read import AI_PHRASES
    assert "as requested" in AI_PHRASES and not any(p.startswith("//") or not p for p in AI_PHRASES)
    assert "# initialize variables" in AI_PHRASES  # an entry may start with #
    assert "this function is designed to" not in AI_PHRASES  # switched off with //


def ai(findings):
    return [x.message for x in findings if x.rule == "potential-ai-leftover"]


def test_ai_phrases_match_whole_words_and_name_the_phrase(tmp_path):
    f = run(tmp_path, {"a.py": "x = 1  # a sample of the data, no decline\ny = 2  # the amp is on\nz = 3  # I hope this helps\n"})
    assert ai(f) == ["possibly AI: 'amp'", "possibly AI: 'i hope this helps'"]  # not 'sample' or 'decline'; longest phrase only
    assert [x.location for x in f] == ["line 2", "line 3"]


def test_ai_phrases_in_markdown_and_text_but_not_in_logs_or_configs(tmp_path):
    f = run(tmp_path, {"requirements.txt": "", "a.md": "I've added a section.\n", "b.txt": "Let's explore the data\n",
                       "c.log": "I've added a section\n", "d.json": '{"n": "I\'ve added it"}\n'})
    assert sorted((x.file, x.message) for x in f) == [("a.md", "possibly AI: 'i've added'"),
                                                      ("b.txt", "possibly AI: 'let's explore'")]


def test_repeated_ai_hits_are_grouped_per_file(tmp_path):
    cells = [code(["# hope this helps\n"], 1, []), code(["x = 1  # hope this helps\n"], 2, []),
             code(["print('ok')\n"], 3, [])]
    f = run(tmp_path, {"requirements.txt": "", "nb.ipynb": notebook(cells), "a.py": "# hope this helps\n# hope this helps\n"})
    got = {(x.file, x.location): x.message for x in f if x.rule == "potential-ai-leftover"}
    assert got == {("nb.ipynb", "cell 1"): "possibly AI: 'hope this helps' (and 1 more places)",
                   ("a.py", "line 1"): "possibly AI: 'hope this helps' (and 1 more places)"}


def test_ai_phrase_file_switched_off_sections_and_long_lines(tmp_path):
    f = run(tmp_path, {"a.py": "# in conclusion we win\n# this function is designed to add\n"})
    assert ai(f) == []  # sections 2 and 6 are commented out
    f = run(tmp_path / "long", {"a.py": "x = '" + "word " * 600 + "I hope this helps'\n"})
    assert ai(f) == []  # a line over 2,000 characters is not scanned


def hits(tmp_path, names):
    for n in names:
        p = tmp_path / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n" if n.endswith(".py") else "text\n")
    return sorted((x.file, x.message) for x in check(tmp_path, []) if x.rule == "leftover-file")


def test_leftover_names_ignore_case_and_extension_and_are_found_anywhere(tmp_path):
    got = hits(tmp_path, ["Agents.md", "sub/agents.txt", "sub/deep/AGENTS.YAML", ".github/instructions/a.md",
                          "rules/x.instructions.md", "rules/y.mdc", "PLAN", "Notes.json", "session.toml"])
    assert [f for f, _ in got] == [".github/instructions", "Agents.md", "Notes.json", "PLAN", "rules/x.instructions.md",
                                   "rules/y.mdc", "session.toml", "sub/agents.txt", "sub/deep/AGENTS.YAML"]
    assert "(matches 'AGENTS.md')" in dict(got)["sub/agents.txt"]
    assert dict(got)[".github/instructions"].startswith("possibly a leftover agent or working-notes folder")


def test_leftover_folder_is_reported_once_and_not_searched(tmp_path):
    p = tmp_path / ".claude"
    p.mkdir()
    (p / "settings.json").write_text('{"p": "/Users/me/x"}\n')
    (p / "CLAUDE.md").write_text("x\n")
    f = check(tmp_path, [])
    assert [(x.file, x.rule) for x in f] == [(".claude", "leftover-file")]


def test_plain_named_leftover_folder_is_still_searched(tmp_path):
    (tmp_path / "plan").mkdir()
    (tmp_path / "plan" / "train.py").write_text("p = '/home/me/data.csv'\n")
    f = check(tmp_path, [])
    assert sorted((x.file, x.rule) for x in f) == [("plan", "leftover-file"), ("plan/train.py", "abs-path")]


def test_ai_phrases_in_notebook_markdown_with_curly_apostrophe(tmp_path):
    md = {"cell_type": "markdown", "source": ["Let’s explore the data. In this notebook we load it."]}
    f = run(tmp_path, {"requirements.txt": "", "nb.ipynb": notebook([md])})
    assert ai(f) == ["possibly AI: 'in this notebook', 'let's explore'"]  # one place, one finding


def test_requirements_files_are_not_scanned_for_ai_phrases(tmp_path):
    f = run(tmp_path, {"requirements.txt": "openai\nanthropic\n", "requirements-dev.txt": "codex\n"})
    assert ai(f) == []


def test_leftover_does_not_flag_ordinary_names(tmp_path):
    assert hits(tmp_path, ["agents/policy.py", "notes.csv", "main.py", "data/summary.csv", "src/plan_utils.py"]) == []


def test_forbid_is_one_word_or_phrase_per_flag_and_commas_are_ordinary(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("# claude wrote this\nx = 1  # as referenced in the plan\ny = 'gpt'\nz = 'hello, world'\n")
    r = CliRunner().invoke(main, [str(proj), "--forbid", " claude ", "--forbid", "as referenced", "--forbid", "gpt",
                                  "--forbid", "hello, world", "--forbid", "  "])
    assert r.exit_code == 1
    for w in ("claude", "as referenced", "gpt", "hello, world"):
        assert f"mentions '{w}'" in r.output
    assert "mentions ''" not in r.output
    r = CliRunner().invoke(main, [str(proj), "--forbid", "gpt,claude"])  # one phrase "gpt,claude": found nowhere
    assert "mentions" not in r.output


def test_no_requirements_alone_does_not_fail_the_run(tmp_path):
    proj = tmp_path / "hw"
    proj.mkdir()
    (proj / "a.py").write_text("import numpy\n")
    r = CliRunner().invoke(main, [str(proj)])
    assert r.exit_code == 0 and "Warnings" in r.output and "[no-requirements]" in r.output


def test_requirements_includes_are_followed(tmp_path):
    # an include chain: requirements.txt includes requirements/extra.txt, which includes base.txt
    f = run(tmp_path, {
        "requirements.txt": "# setup\n-r requirements/extra.txt\n",
        "requirements/extra.txt": "-r base.txt\npandas>=2,<3\nrequests>=2.31\n",
        "requirements/base.txt": "scipy>=1.11\nnumpy>=1.26\n-r ../requirements.txt\n",  # a cycle is harmless
        "a.py": "import scipy, numpy, pandas, requests\nimport torch\n",
    })
    assert [(x.rule, x.message.split("'")[1]) for x in f] == [("undeclared", "torch")]


def test_requirements_include_forms_and_missing_files(tmp_path):
    f = run(tmp_path, {
        "requirements.txt": "-r base.txt\n--requirement=extra.txt\n-rtight.txt\n-r gone.txt\n-e .\n--index-url https://x\n",
        "base.txt": "numpy\n", "extra.txt": "pandas\n", "tight.txt": "rich\n",
        "a.py": "import numpy, pandas, rich\n",
    })
    assert f == []


def test_leaked_path_in_notebook_outputs_and_markdown(tmp_path):
    out = {"output_type": "stream", "name": "stderr", "text": [
        "C:\\Users\\Alex Smith\\AppData\\Local\\Temp\\ipykernel_1\\x.py:3: Warning\n",
        "C:\\Users\\Alex Smith\\AppData\\Local\\Temp\\ipykernel_1\\x.py:9: Warning\n", "A:\nnothing\n"]}
    fine = {"output_type": "stream", "name": "stdout", "text": ["see http://host/home/page and D:\n"]}
    md = {"cell_type": "markdown", "source": "Data lives in /Users/me/project/data"}
    nb = notebook([md, code("x = 1", 1, [out]), code("y = 2", 2, [fine])])
    f = run(tmp_path, {"requirements.txt": "", "nb.ipynb": nb})
    assert [(x.rule, x.level, x.location) for x in f] == [
        ("leaked-path", "warn", "cell 1"), ("leaked-path", "warn", "cell 2")]
    assert "C:\\Users\\Alex" in f[1].message and "(and 1 more lines)" in f[1].message  # one finding per cell
    assert "markdown text" in f[0].message and "saved output" in f[1].message


def test_text_files_configs_fail_and_docs_warn(tmp_path):
    f = run(tmp_path, {
        "requirements.txt": "",
        "run.json": '{"policy": "/Users/me/code/x/policy.pt", "ok": "data/x.csv", "up": "../../lab/x.csv"}\n',
        "go.sh": "cd /private/tmp/job || exit 1\nSTATUS=/home/me/status.log\n",
        "usage.md": "Run it from /Users/me/code.\nSee [the lab](../../lab/README.md) and [mine](docs/a.md).\n",
        "docs/a.md": "Back to [home](../README.md) and ../.venv/bin/python\n",
        "run.log": "wrote /Users/me/out.txt\nwrote /Users/me/out.txt\nwrote /Users/me/out.txt\n",
    })
    got = sorted((x.file, x.rule, x.level, x.location) for x in f)
    assert got == [
        ("go.sh", "abs-path", "fail", "line 1"), ("go.sh", "abs-path", "fail", "line 2"),
        ("run.json", "abs-path", "fail", "line 1"), ("run.json", "outside", "fail", "line 1"),
        ("run.log", "leaked-path", "warn", "line 1"),
        ("usage.md", "leaked-path", "warn", "line 1"), ("usage.md", "outside", "warn", "line 2")]
    log = next(x for x in f if x.file == "run.log")
    assert log.message.endswith("(and 2 more lines)")  # one finding, not three


def test_text_files_forbidden_words_are_counted_once_per_file(tmp_path):
    f = run(tmp_path, {"requirements.txt": "", "a.md": "Alex did this\nand alex that\nfine\n",
                       "b.json": '{"owner": "alex"}\n', "c.csv": "alex,1\n"}, ["alex"])
    assert sorted((x.file, x.rule, x.location) for x in f) == [("a.md", "forbidden", "line 1"),
                                                               ("b.json", "forbidden", "line 1")]
    assert "(and 1 more lines)" in next(x for x in f if x.file == "a.md").message


def test_text_files_ignore_urls_data_and_big_files(tmp_path):
    big = '{"p": "/Users/me/x"}\n' + "x" * (3 * 1024 * 1024)
    f = run(tmp_path, {"requirements.txt": "", "links.md": "see https://github.com/home/x and https://a.b/Users/c\n",
                       "package-lock.json": '{"p": "/Users/me/x"}\n', "big.json": big, "data.csv": "/Users/me/x\n"})
    assert f == []


def test_help_has_examples_and_exit_codes():
    r = CliRunner().invoke(main, ["--help"])
    assert "Examples:" in r.output and "dscforge my-project --plan" in r.output and "Exit status:" in r.output
    assert "--workdir" not in r.output and "--forbid-file" not in r.output


def test_usage_line_puts_the_path_first_and_errors_share_one_style(tmp_path):
    r = CliRunner().invoke(main, ["--help"])
    assert "Usage: main [PATH] [OPTIONS]" in r.output or "[PATH] [OPTIONS]" in r.output
    assert "--timeout SECONDS" in r.output and "RANGE" not in r.output and "FLOAT" not in r.output
    for args, text in ((["no-such-folder"], "no-such-folder"), (["--nope"], "No such option"),
                       ([str(tmp_path), "--timeout", "0.5"], "not a valid integer")):
        r = CliRunner().invoke(main, args)
        assert r.exit_code == 2 and r.output.startswith("Error: ") and text in r.output, args
        assert "Usage:" not in r.output and "dscforge --help" in r.output


def test_timeout_needs_run_and_a_whole_positive_number(tmp_path):
    for args, text in ((["--timeout", "5"], "only works together with --run"),
                       (["--run", "--timeout", "0"], "1 or more"), (["--run", "--timeout", "-3"], "1 or more")):
        r = CliRunner().invoke(main, [str(tmp_path), *args])
        assert r.exit_code == 2 and text in r.output, args
        assert "Summary" not in r.output


def test_more_findings_line_has_its_own_rule(tmp_path):
    lines = "".join(f"/Users/me/file{i}.txt\n" for i in range(40))
    f = run(tmp_path, {"requirements.txt": "", "log.md": lines})
    assert [x.rule for x in f].count("more-findings") == 1 and not any("not listed" in x.message for x in f if x.rule == "leaked-path")


def test_phrases_in_one_place_make_one_finding_and_emoji_joins_them(tmp_path):
    f = run(tmp_path, {"a.py": "# note that this is a placeholder implementation \U0001F680\n"})
    assert ai(f) == ["possibly AI: 'note that this', 'placeholder implementation', 'this is a placeholder', an emoji"]


def test_paths_are_shown_the_way_a_person_reads_them(tmp_path):
    # the file text has doubled backslashes (Python source and JSON escape them); the message has single ones
    f = run(tmp_path, {"a.py": "p = 'C:\\\\Users\\\\sam\\\\data.csv'\n", "b.json": '{"p": "C:\\\\Users\\\\sam\\\\x"}\n'})
    msgs = sorted(x.message for x in f if x.rule == "abs-path")
    assert msgs == ["absolute path: C:\\Users\\sam\\data.csv (will not exist on another machine)",
                    "absolute path: C:\\Users\\sam\\x (will not exist on another machine)"]


def test_findings_without_a_file_say_so_and_the_plan_has_a_legend(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("import numpy\n")
    out = tmp_path / "p.md"
    r = CliRunner().invoke(main, [str(proj), "--plan", str(out)])
    assert "(no specific file) [no-requirements]" in r.output and "(project)" not in r.output
    assert "`cell N` is the Nth cell of a notebook" in out.read_text()


def test_report_says_which_mode_was_checked_and_names_files_that_could_not_be_copied(tmp_path, monkeypatch):
    import shutil
    from dscforge import snapshot
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("x = 1\n")
    r = CliRunner().invoke(main, [str(proj)])
    assert "Checked proj (files as they are on disk)" in r.output

    def partial(src, dst, **k):
        raise shutil.Error([(str(proj / "locked.xlsx"), str(dst), "permission denied")])
    monkeypatch.setattr(snapshot.shutil, "copytree", partial)
    r = CliRunner().invoke(main, [str(proj)])
    assert "locked.xlsx [unreadable] could not copy it, so it was not checked: permission denied" in r.output


def test_version_option():
    from importlib.metadata import version
    r = CliRunner().invoke(main, ["--version"])
    assert r.exit_code == 0 and r.output.strip() == f"dscforge, version {version('dscforge')}"


def test_env_var_rule_names_variables_read_by_literal_name(tmp_path):
    src = ("import os\nfrom os import getenv\n"
           "a = os.environ['API_KEY']\nb = os.environ.get('TOKEN', '')\nc = os.getenv('DB_URL')\nd = getenv('EXTRA')\n"
           "e = os.environ['HOME']\nf = os.environ.get('path')\nos.environ['SET_BY_US'] = '1'\ng = os.getenv(name)\n"
           "h = os.environ['API_KEY']\n")
    f = run(tmp_path, {"a.py": src})
    got = [(x.location, x.message) for x in f if x.rule == "env-var"]
    assert got == [("line 3", "reads the environment variable 'API_KEY', which someone else will have to set"),
                   ("line 4", "reads the environment variable 'TOKEN', which someone else will have to set"),
                   ("line 5", "reads the environment variable 'DB_URL', which someone else will have to set"),
                   ("line 6", "reads the environment variable 'EXTRA', which someone else will have to set")]


def test_colab_and_kaggle_paths_and_paths_in_comments(tmp_path):
    src = ("a = '/content/drive/MyDrive/x.csv'\nb = '/kaggle/input/data/y.csv'\nc = 1  # was C:\\Users\\sam\\data.csv\n"
           "d = 'text # not a comment /home/x/y'\ne = 2  # fine, relative: data/x.csv\n")
    f = run(tmp_path, {"a.py": src})
    assert [(x.rule, x.location) for x in f] == [("abs-path", "line 1"), ("abs-path", "line 2"),
                                                 ("leaked-path", "line 3"), ("abs-path", "line 4")]
    assert [x.message for x in f if x.rule == "leaked-path"] == ["comment contains a local path: C:\\Users\\sam\\data.csv"]


def test_environment_yml_poetry_and_setup_py_declare_packages(tmp_path):
    f = run(tmp_path / "conda", {"environment.yml": (
        "name: demo\nchannels:\n  - conda-forge\ndependencies:\n  - python=3.11\n  - numpy=1.26\n"
        "  - conda-forge::pytorch\n  - pip\n  - pip:\n    - requests==2.31\n    - -r other.txt\n  - pandas  # comment\n"
        "variables:\n  - ignored\n"), "a.py": "import numpy, torch, requests, pandas, scipy\n"})
    assert [(x.rule, x.message.split("'")[1]) for x in f] == [("undeclared", "scipy")]
    f = run(tmp_path / "poetry", {"pyproject.toml": (
        "[tool.poetry.dependencies]\npython = '^3.11'\nnumpy = '^1.26'\n[tool.poetry.group.dev.dependencies]\npytest = '*'\n"),
        "a.py": "import numpy, pytest, scipy\n"})
    assert [(x.rule, x.message.split("'")[1]) for x in f] == [("undeclared", "scipy")]
    f = run(tmp_path / "setup", {"setup.py": "from setuptools import setup\nsetup(name='x', install_requires=['numpy>=1', 'requests'])\n",
                                 "a.py": "import numpy, requests, scipy\n"})
    assert [(x.rule, x.message.split("'")[1]) for x in f if x.file == "a.py"] == [("undeclared", "scipy")]


def test_install_args_cover_the_other_declaration_files(tmp_path):
    run(tmp_path / "conda", {"environment.yml": "dependencies:\n  - python=3.11\n  - numpy=1\n  - libgdal\n  - pip:\n    - requests==2.31\n"})
    from dscforge.read import install_args
    assert install_args(tmp_path / "conda") == ["requests==2.31", "numpy"]  # libgdal is not a well-known PyPI package
    run(tmp_path / "poetry", {"pyproject.toml": "[tool.poetry.dependencies]\npython = '^3.11'\nrich = '*'\n"})
    assert install_args(tmp_path / "poetry") == ["rich"]


def test_requirements_encodings_utf16_and_bom(tmp_path):
    from dscforge.read import declared_packages
    (tmp_path / "u16").mkdir()
    (tmp_path / "u16" / "requirements.txt").write_bytes("numpy==1.26\r\npandas\r\n".encode("utf-16"))
    assert declared_packages(tmp_path / "u16") == ({"numpy", "pandas"}, True)
    (tmp_path / "bom").mkdir()
    (tmp_path / "bom" / "requirements.txt").write_bytes(b"\xef\xbb\xbfnumpy\n")
    assert declared_packages(tmp_path / "bom") == ({"numpy"}, True)


def test_names_the_console_cannot_encode_do_not_crash_the_report(tmp_path, monkeypatch):
    import io
    import sys
    from dscforge.cli import _safe_output
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252", newline=""))  # a Windows pipe or file
    _safe_output()
    import click
    click.echo("analyse_\U0001f4ca_données.py")
    sys.stdout.flush()
    assert raw.getvalue() == "analyse_\\U0001f4ca_données.py\n".encode("cp1252")  # the emoji as an escape, é as itself


def test_a_crash_exits_2_not_1_and_ctrl_c_exits_3(tmp_path, monkeypatch):
    from dscforge import cli
    (tmp_path / "a.py").write_text("x = 1")

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(cli, "check", boom)
    r = CliRunner().invoke(main, [str(tmp_path)])
    assert r.exit_code == 2 and "Error: dscforge stopped unexpectedly: RuntimeError: boom (details above)" in r.output

    def interrupted(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli, "check", interrupted)
    r = CliRunner().invoke(main, [str(tmp_path)])
    assert r.exit_code == 3 and "Stopped (Ctrl+C)." in r.output


def test_the_package_list_lines_up_under_its_message(tmp_path):
    (tmp_path / "a.py").write_text("import numpy\nimport pandas\n")
    r = CliRunner().invoke(main, [str(tmp_path)])
    assert "add:\n      numpy\n      pandas\n" in r.output


def test_pyproject_description_is_the_first_line_of_help():
    import tomllib
    from pathlib import Path
    description = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8"))["project"]["description"]
    assert CliRunner().invoke(main, ["--help"]).output.split("\n\n")[1].strip() == description

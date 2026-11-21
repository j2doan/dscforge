import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import click

from .read import Finding, check, find_local_data
from .run import run_pass
from .snapshot import make_snapshot

DEFAULT_TIMEOUT = 30  # seconds each notebook or script may run with --run


# "file:location" for a finding ("(no specific file)" when it has no file)
def _where(f) -> str:
    return (f.file or "(no specific file)") + (f":{f.location}" if f.location else "")


# one-line "how to fix" per rule, for the plan
FIX = {
    "abs-path": "Replace the absolute path with a path relative to the project folder (for example `pathlib.Path(__file__).parent / ...`).",
    "outside": "The path leaves the project folder. Move what it points to into the project and use a relative path, or remove the dependency.",
    "forbidden": "Remove or rename it. If it is a file or folder name, rename it and update everything that refers to it.",
    "syntax-error": "Fix the syntax error so the file can run. Checks that need to parse this file were skipped; run dscforge again afterwards.",
    "local-only": "This data file exists only on your machine. Commit it, or document where to get it.",
    "install-failed": "A package could not be installed in a fresh environment. Fix the package list (a misspelt name, a missing version pin, or a package that needs a system library). If the message says a name was only a guess from an import, add it to requirements.txt if it is a package; if it is one of your own modules, make sure it is in the project.",
    "leaked-path": "Remove the local path so it does not ship: for a notebook, run all cells again from a folder that does not reveal your username, or clear the outputs; for text, replace the path with a relative one or a placeholder.",
    "run-missing-package": "Add the package to requirements.txt (or pyproject.toml dependencies).",
    "run-missing-file": "A file the code opens is not in what you ship. Add it to the project, or change the code so it does not need it.",
    "run-error": "Read the error and fix the cause in the code, then run dscforge again.",
    "run-skipped": "It needs input or arguments, so it was not run to the end. This is not a failure.",
    "undeclared": "Add the package to requirements.txt (or pyproject.toml dependencies), or remove the import if it is unused.",
    "no-requirements": "Create requirements.txt containing the package names listed in the finding (one per line).",
    "potential-ai-leftover": "This may or may not be AI-written; decide for yourself. If it is, rewrite it in your own words.",
    "leftover-file": "Delete it, or keep it on purpose if it belongs to the project.",
    "nb-error": "Fix the cause, re-run the notebook top to bottom, and save it so the error output is gone.",
    "nb-order": "Restart the kernel and run all cells top to bottom, then save.",
    "unreadable": "dscforge could not copy this file (it may be locked, a broken link or unreadable), so nothing in it was checked. Close or fix it and run dscforge again.",
    "env-var": "The code reads this environment variable, so it fails on a machine where it is not set. Document it (for example in the README), or give the code a default.",
    "more-findings": "Some findings are not listed, to keep this list short. Fix the ones above and run dscforge again.",
}


# "1 problem", "2 problems"
def _count(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


# Markdown plan: findings grouped by rule, each rule with how to fix it
def _plan(path, source, run, fails, warns, notes, run_notes) -> str:
    out = ["# dscforge plan", "",
           f"Checked `{Path(path).resolve().name}` ({source}; "
           f"{'with the startup check' if run else 'read pass only'}). "
           f"{_count(len(fails), 'problem')}, {_count(len(warns), 'warning')}.", "",
           "## Please check these by hand", "",
           "dscforge is a rule-based checker, so treat this list as a starting point, not a verdict.", "",
           "- **False alarms:** some items may not be real problems (for example an emoji or an em dash a person "
           "wrote, or a path that is only mentioned in documentation).",
           "- **Misses:** some real problems may not be listed (for example paths built while the program runs, "
           "or files of types dscforge does not read). An empty section does not prove the project is clean.", "",
           "Verify each item before changing anything. In a location, `line N` is a line number and `cell N` is "
           "the Nth cell of a notebook, counting from 1.", ""]
    for items, title in ((fails, "Problems"), (warns, "Warnings")):
        by_rule: dict[str, list] = {}
        for f in items:
            by_rule.setdefault(f.rule, []).append(f)
        if by_rule:
            out += [f"## {title}", ""]
        for rule, group in by_rule.items():
            out += [f"### {rule}", "", FIX.get(rule, "Review and fix."), ""]
            for f in group:
                first, _, rest = f.message.partition("\n")
                out.append(f"- [ ] `{_where(f)}`: {first}")
                if rest:
                    out += ["", "  ```", *[f"  {line}" for line in rest.splitlines()], "  ```"]
            out.append("")
    if notes:
        out += ["## Not committed", "", "Someone who clones the repo will not get these versions.", ""]
        out += [f"- [ ] {n}" for n in notes] + [""]
    if run_notes:
        out += ["## Run notes", "", "For information only; nothing to fix.", ""]
        out += [f"- {n}" for n in run_notes] + [""]
    return "\n".join(out)


# forbidden words: each --forbid value is one word or phrase (commas are ordinary characters)
def _forbidden(values) -> list[str]:
    return [v.strip() for v in values if v.strip()]


# stop with exit code 2 and a plain message (exit code 1 is reserved for "problems found")
def _stop(message: str) -> None:
    click.echo(f"Error: {message}", err=True)
    sys.exit(2)


# Output that cannot be encoded (an emoji or Japanese file name when output goes to a file or a pipe on Windows,
# which uses a legacy code page) is written as an escape such as \U0001f4ca instead of crashing the report
def _safe_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass


# the system temp folder must exist and be writable: the copy being checked lives there
def _check_temp_folder() -> None:
    try:
        Path(tempfile.mkdtemp(prefix="dscforge_probe_")).rmdir()
    except OSError:
        _stop("dscforge cannot find a usable temporary folder. Set the TEMP (Windows) or TMPDIR (Mac/Linux) "
              "environment variable to a folder that exists and that you can write to.")


# validate the --plan value before any work is done; returns the file to write
def _plan_path(value: str) -> Path:
    p = Path(value).expanduser()
    if not value.strip():  # a bare --plan, or an empty name
        _stop("--plan needs a file name ending in .md, for example --plan PLAN.md.")
    if p.is_dir() or value.endswith(("/", "\\")):
        _stop(f"'{value}' is a folder, not a file. --plan needs a file name ending in .md, for example --plan PLAN.md.")
    if p.name.lower() == ".md":
        _stop("the plan file needs a name before .md, for example PLAN.md.")
    if p.suffix.lower() != ".md":
        _stop(f"the plan file must be a Markdown file ending in .md (got '{value}').")
    if not p.resolve().parent.is_dir():
        _stop(f"the folder '{p.parent}' does not exist. Please give a valid path.")
    if p.exists():  # exists() ignores letter case on Windows, so plan.md blocks PLAN.md
        _stop(f"{value} already exists: delete it or choose another name.")
    return p


# "abs-path 20, undeclared 12": findings per rule, most frequent first
def _by_rule(items) -> str:
    counts: dict[str, int] = {}
    for f in items:
        counts[f.rule] = counts.get(f.rule, 0) + 1
    return ", ".join(f"{r} {n}" for r, n in sorted(counts.items(), key=lambda x: (-x[1], x[0])))


# the closing summary, in plain words
def _summary(fails, warns, notes) -> list[str]:
    rows = [(_count(len(fails), "problem"), "could prevent this project from working elsewhere", _by_rule(fails)),
            (_count(len(warns), "warning"), "worth a look; may be fine", _by_rule(warns)),
            (f"{len(notes)} not committed", "files that won't be included when this project is shared", "")]
    w1 = max(len(r[0]) for r in rows)
    w2 = max(len(r[1]) for r in rows if r[2]) if any(r[2] for r in rows) else 0
    lines = ["", "Summary"]
    for name, meaning, rules in rows:
        lines.append(f"  {name.ljust(w1)}   " + (meaning.ljust(w2) + f"   ({rules})" if rules else meaning))
    lines += ["", "Result: problems found. (exit status 1)"
              if fails else "Result: no problems found. (exit status 0)"]
    return lines


# the whole check: validate the options, copy, read, optionally run, report, write the plan, set the exit code
def _check_folder(path, run, timeout, forbid, plan) -> None:
    # refuse bad input before doing any work
    plan_file = _plan_path(plan) if plan is not None else None
    if timeout is not None and not run:
        _stop("--timeout only works together with --run.")
    if timeout is not None and timeout < 1:
        _stop("--timeout must be a whole number of seconds, 1 or more.")
    seconds = DEFAULT_TIMEOUT if timeout is None else timeout
    _check_temp_folder()
    folder = Path(path).resolve()
    snap, notes, is_git, skipped = make_snapshot(path)
    run_notes: list[str] = []
    try:
        findings = [Finding("warn", file, "", "unreadable", f"could not copy it, so it was not checked: {why}")
                    for file, why in skipped]
        findings += check(snap, _forbidden(forbid))
        data = find_local_data(snap, folder)
        findings += [Finding("warn", file, loc, "local-only",
                             f"{target} exists on this machine but is not in what you ship") for file, loc, target in data]
        if run:
            ran, run_notes = run_pass(snap, folder, data, findings, seconds / 60, lambda m: click.echo(m, err=True))
            findings += ran
    finally:
        shutil.rmtree(snap, ignore_errors=True)

    fails = [f for f in findings if f.level == "fail"]
    warns = [f for f in findings if f.level == "warn"]
    click.echo(f"Checked {folder.name} ({'committed files only' if is_git else 'files as they are on disk'})")
    groups = [("Problems", [f"{_where(f)} [{f.rule}] {f.message}" for f in fails]),
              ("Warnings", [f"{_where(f)} [{f.rule}] {f.message}" for f in warns]),
              ("Not committed", notes),
              ("Run notes", run_notes)]
    for title, lines in groups:
        if lines:
            click.echo(f"\n{title}")
            for line in lines:
                click.echo("  " + line.replace("\n", "\n      "))  # a package list lines up under its message
    if not (findings or notes or run_notes):
        click.echo("No findings.")
    for line in _summary(fails, warns, notes):
        click.echo(line)

    # optional Markdown plan ("x" mode: never overwrite, even if the file appeared during the run)
    if plan_file:
        source = "committed files only" if is_git else "files as they are on disk"
        try:
            with open(plan_file, "x", encoding="utf-8") as f:
                f.write(_plan(path, source, run, fails, warns, notes, run_notes) + "\n")
        except OSError as e:
            _stop(f"could not write {plan_file}: {e.strerror or e} (the report above was still printed).")

    sys.exit(1 if fails else 0)



class _Command(click.Command):
    # the usage line reads "dscforge [PATH] [OPTIONS]" (Click puts the options first)
    def collect_usage_pieces(self, ctx):
        pieces = super().collect_usage_pieces(ctx)
        return pieces[1:] + pieces[:1]

    # Click's own errors (a missing folder, a bad number, an unknown option) in dscforge's error style
    def parse_args(self, ctx, args):
        _safe_output()  # before anything can print, including these errors
        try:
            return super().parse_args(ctx, args)
        except click.UsageError as e:
            _stop(f"{e.format_message()} Run 'dscforge --help' for usage.")


@click.command(cls=_Command)
@click.version_option(package_name="dscforge", prog_name="dscforge")
@click.argument("path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--run", is_flag=True,
              help="Also start every notebook and script in a new empty environment (installs packages, executes code).")
@click.option("--timeout", type=int, metavar="SECONDS",
              help=f"With --run: seconds each notebook or script may run (default {DEFAULT_TIMEOUT}). "
                   "One still running at the limit counts as passing.")
@click.option("--forbid", multiple=True, metavar="WORD",
              help="One word or phrase that must not appear anywhere (file contents, file and folder names). "
                   'Repeat the flag for more. Example: --forbid "old project"')
@click.option("--plan", is_flag=False, flag_value="", default=None, metavar="FILE",
              help="Also write the findings and how to fix them as a Markdown (.md) plan. Needs a file name, resolved "
                   "against the current directory. Refuses to overwrite an existing file. Example: --plan PLAN.md")
def main(path, run, timeout, forbid, plan):
    """Check whether your project will work for someone else

    PATH is the folder (project) to check. It can be a relative or absolute
    path, and you don't need to be inside the folder. For example, run
    `dscforge my-project` to check a folder named `my-project` in your current
    directory. If you omit PATH, dscforge automatically checks your current
    directory as the project to check.

    dscforge makes a temporary copy of the folder, reads the code in that
    copy, and reports anything that could break, leak or look wrong on another
    machine: hardcoded paths, undeclared packages, forbidden words, possible
    AI leftovers, and more. Your original folder is never changed.

    With --run, it also starts each notebook and script briefly in a clean
    environment.

    \b
    Examples:
      dscforge                                    check the folder you are in
      dscforge my-project                         check my-project, a folder inside the current one
      dscforge my-project --plan PLAN.md          also write the findings to PLAN.md
      dscforge my-project --forbid alex --forbid "old project"
                                                  flag these words anywhere
      dscforge my-project --run --timeout 60      also start each notebook and script for up to 60 seconds

    \b
    Exit status: 0 no problems, 1 problems found, 2 could not run (bad option, no temp folder,
    or an unexpected error), 3 stopped with Ctrl+C.
    """
    _safe_output()
    try:
        _check_folder(path, run, timeout, forbid, plan)
    except KeyboardInterrupt:
        click.echo("Stopped (Ctrl+C).", err=True)
        sys.exit(3)
    except Exception as e:  # a bug: exit 2, not 1 (1 means "problems found")
        traceback.print_exc()
        _stop(f"dscforge stopped unexpectedly: {type(e).__name__}: {e} (details above)")

if __name__ == "__main__":
    main()

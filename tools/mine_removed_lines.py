"""List phrases that were deleted from comments and notebook text in a git repo's history.

Usage: python tools/mine_removed_lines.py PATH_TO_REPO [--top N]

Read-only: it only runs `git log -p` in that repo. Use the output to pick phrases for
dscforge/ai_phrases.txt.
"""
import re
import subprocess
import sys
from collections import Counter


# text of a removed diff line if it is a python comment or a notebook JSON string, else ""
def _text(line: str) -> str:
    s = line[1:].strip()
    if s.startswith('"') and s.rstrip(",").endswith('"'):  # a line inside an .ipynb "source" list
        s = s.rstrip(",")[1:-1].replace("\\n", "").replace('\\"', '"')
        return s.lstrip("# ").strip() if s.lstrip().startswith("#") else ""
    if "#" in s:
        return s.split("#", 1)[1].strip()
    return ""


# (counter of 3-4 word phrases, counter of whole removed lines) over all history
def mine(repo: str) -> tuple[Counter, Counter]:
    out = subprocess.run(
        ["git", "log", "-p", "-U0", "--no-color", "--format=", "--", "*.py", "*.ipynb"],
        cwd=repo, capture_output=True, check=True,
    ).stdout.decode("utf-8", errors="replace")
    phrases: Counter = Counter()
    lines: Counter = Counter()
    for line in out.splitlines():
        if not line.startswith("-") or line.startswith(("--- a/", "--- /dev/null")):
            continue
        text = _text(line).lower()
        if not text:
            continue
        lines[text] += 1
        words = re.findall(r"[a-z0-9']+", text)
        seen = {" ".join(words[i:i + n]) for n in (3, 4) for i in range(len(words) - n + 1)}
        phrases.update(seen)  # each phrase counted once per removed line
    return phrases, lines


def main() -> None:
    args = sys.argv[1:]
    top = int(args[args.index("--top") + 1]) if "--top" in args else 40
    repo = next((a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] != "--top")), None)
    if not repo:
        sys.exit(__doc__)
    phrases, lines = mine(repo)
    print("Phrases removed more than once (3-4 words):")
    for p, n in [x for x in phrases.most_common() if x[1] > 1][:top]:
        print(f"  {n:3d}  {p}")
    print("\nMost often removed whole comment lines:")
    for t, n in lines.most_common(top):
        print(f"  {n:3d}  {t}")


if __name__ == "__main__":
    main()

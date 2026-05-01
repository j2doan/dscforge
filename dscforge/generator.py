from pathlib import Path
import shutil
import subprocess
import sys

BASE_DEPS = ["numpy", "pandas", "matplotlib", "ipykernel"]


def create_project(name: str, template: str, git: bool):
    base = Path(name)

    if base.exists():
        raise FileExistsError(f"{name} already exists")

    base.mkdir()

    # --- TEMPLATE COPY ---
    template_dir = Path(__file__).parent / "templates" / template

    if not template_dir.exists():
        raise ValueError(f"Template '{template}' does not exist")

    shutil.copytree(template_dir, base, dirs_exist_ok=True)

    # --- GIT ---
    if git:
        try:
            subprocess.run(
                ["git", "init"],
                cwd=base,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            print("Initialized git repository")
        except FileNotFoundError:
            print("Warning: git is not installed. Skipping git initialization.")

    # --- VENV ---
    venv_python = None

    if template in ["standard", "premium"]:
        try:
            subprocess.run(
                [sys.executable, "-m", "venv", ".venv"],
                cwd=base,
                check=True
            )
            print("Created virtual environment (.venv)")

            # detect venv python path (cross-platform)
            venv_python = base / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

        except subprocess.CalledProcessError:
            print("Warning: failed to create virtual environment")

    # --- DEPENDENCIES ---
    if template in ["standard", "premium"]:

        deps = BASE_DEPS.copy()

        # install into venv if available, otherwise fallback to system python
        python_exec = venv_python if venv_python and venv_python.exists() else sys.executable

        try:
            subprocess.run(
                [str(python_exec), "-m", "pip", "install", *deps],
                check=True
            )
            print("Installed starter libraries (numpy, pandas, matplotlib, ipykernel)")
        except subprocess.CalledProcessError:
            print("Warning: failed to install base libraries")

        # --- requirements.txt ---
        result = subprocess.run(
            [str(python_exec), "-m", "pip", "freeze"],
            capture_output=True,
            text=True,
            check=True
        )

        req_file = base / "requirements.txt"
        req_file.write_text(result.stdout)

        print("Generated requirements.txt (pinned versions)")

        # --- pyproject.toml ---
        toml_file = base / "pyproject.toml"

        toml_content = f"""[project]
name = "{name}"
version = "0.1.0"

dependencies = [
{chr(10).join(f'    "{d}",' for d in deps)}
]
"""

        toml_file.write_text(toml_content)
        print("Generated pyproject.toml")

    print(f"Successfully created project: {name} ({template.upper()} EDITION)")
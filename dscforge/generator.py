# Imports
from pathlib import Path
import shutil
import subprocess
import sys

# Base Dependencies
# The packages that will be installed if a venv comes with the template 
BASE_DEPS = ["numpy", "pandas", "matplotlib", "ipykernel"]

# Function to create a project template
def create_project(name: str, template: str, git: bool):
    """
    Function to create a project template.
    Inputs:
        - name (str): the project/root directory name
        - template (str): the type of template that will be made
        - git (bool): whether to initialize git or not
    """
    base = Path(name)

    # Failsafe if a project name already exists in the current directory
    if base.exists():
        raise FileExistsError(f"{name} already exists in the current \
directory. \nPlease select another project name.")

    # Creates the empty project folder in the current directory
    base.mkdir()

    # Copies the desired template (if avaliable)
    template_dir = Path(__file__).parent / "templates" / template

    if not template_dir.exists():
        raise ValueError(f"Template '{template}' is not avaliable in \
the templates directory.")

    # Applies the desired template onto the empty project folder 
    shutil.copytree(template_dir, base, dirs_exist_ok=True)

    # Initializes git (if applicable)
    if git:
        try:
            subprocess.run(
                ["git", "init"],
                cwd=base,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            print("Successfully initialized a git repository.")
        except FileNotFoundError:
            print("Warning: git is not installed. \
\nSkipping git initialization.")

    # Creates a virtual environment (if applicable)
    venv_python = None

    if template in ["standard", "premium"]:
        try:
            subprocess.run(
                [sys.executable, "-m", "venv", ".venv"],
                cwd=base,
                check=True
            )
            print("Successfully created a virtual environment (.venv)")

            # detect venv python path (cross-platform)
            if sys.platform == "win32":
                venv_bin = "Scripts/python.exe"
            else:
                venv_bin = "bin/python"
            venv_python = base / ".venv" / venv_bin

        except subprocess.CalledProcessError:
            print("Warning: failed to create virtual environment. \
\nSkipping virtual environment setup.")

    # Setting up dependencies (if applicable)
    if template in ["standard", "premium"]:

        deps = BASE_DEPS.copy()

        # install into venv if available, 
        # otherwise fallback to system python
        if venv_python and venv_python.exists():
            python_exec = venv_python
        else:
            python_exec = sys.executable

        try:
            subprocess.run(
                [str(python_exec), "-m", "pip", "install", *deps],
                check=True
            )
            print("Successfully installed starter libraries: \
\n(numpy, pandas, matplotlib, ipykernel)")
        except subprocess.CalledProcessError:
            print("Warning: failed to install starter libraries.")

        # Creating requirements.txt
        result = subprocess.run(
            [str(python_exec), "-m", "pip", "freeze"],
            capture_output=True,
            text=True,
            check=True
        )

        req_file = base / "requirements.txt"
        req_file.write_text(result.stdout)

        print("Successfully generated requirements.txt")

        # Creating pyproject.toml
        toml_file = base / "pyproject.toml"

        toml_content = f"""[project]
name = "{name}"
version = "0.1.0"

dependencies = [
{chr(10).join(f'    "{d}",' for d in deps)}
]
"""

        toml_file.write_text(toml_content)
        print("Successfully generated pyproject.toml")

    print(f"Successfully created project: \
{name} ({template.upper()} EDITION)")
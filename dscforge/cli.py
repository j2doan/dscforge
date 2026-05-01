# Imports
import click
from .generator import create_project

@click.command()
@click.argument("name")

# Click option for --template
@click.option(
    "--template",
    type=click.Choice(["basic", "standard", "premium"]),
    default="basic",
    show_default=True,
    help="Select a project template"
)

# Click option for --git
@click.option(
    "--git",
    is_flag=True,
    default=False,
    help="Initialize a git repository in the project"
)

# Creating the template
def main(name, template, git):
    create_project(name, template, git)

if __name__ == "__main__":
    main()
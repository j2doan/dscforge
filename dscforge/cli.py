import click
from .generator import create_project

@click.command()
@click.argument("name")
@click.option(
    "--template",
    type=click.Choice(["basic", "standard", "premium"]),
    default="basic",
    show_default=True
)
@click.option(
    "--git",
    is_flag=True,
    default=False,
    help="Initialize a git repository"
)
def main(name, template, git):
    create_project(name, template, git)

if __name__ == "__main__":
    main()
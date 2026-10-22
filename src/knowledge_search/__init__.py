"""Knowledge Search: CPU-first, retrieval-only enterprise document search."""


def main() -> None:
    from .cli import main as cli_main

    cli_main()

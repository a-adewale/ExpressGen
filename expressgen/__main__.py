"""Allow `python -m expressgen` to run the command-line interface."""

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())

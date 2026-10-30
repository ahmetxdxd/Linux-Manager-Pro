import sys


def main() -> int:
    from lmp.ui.app import run
    return run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())

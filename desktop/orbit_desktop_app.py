"""Compatibility entrypoint. The former independent Tk service manager is retired."""
from desktop.startup import main


if __name__ == '__main__':
    raise SystemExit(main())

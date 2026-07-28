#!/usr/bin/env python3
"""Verify release defaults that every DarkExec install must preserve."""

import json
import os
import runpy
import sys
from pathlib import Path


def main() -> int:
    runtime = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / 'bin/darkexec')
    os.environ.pop('DARKEXEC_TURN_TIMEOUT', None)
    values = runpy.run_path(str(runtime), run_name='darkexec_install_contract')
    turn_timeout = values.get('TURN_TIMEOUT')
    if turn_timeout != 0:
        print(f'DarkExec install rejected: default turn timeout is {turn_timeout!r}, expected 0.', file=sys.stderr)
        return 1
    print(json.dumps({
        'schemaVersion': 1,
        'runtime': str(runtime),
        'turnTimeoutDefault': turn_timeout,
    }, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

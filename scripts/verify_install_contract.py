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
    os.environ.pop('DARKEXEC_EXECUTION_ROOT', None)
    os.environ.pop('DARKEXEC_DOCTRINE_REFRESH', None)
    values = runpy.run_path(str(runtime), run_name='darkexec_install_contract')
    turn_timeout = values.get('TURN_TIMEOUT')
    if turn_timeout != 0:
        print(f'DarkExec install rejected: default turn timeout is {turn_timeout!r}, expected 0.', file=sys.stderr)
        return 1
    execution_root = values.get('EXECUTION_ROOT')
    if execution_root != Path('/var/lib/darkexec/executives'):
        print(
            f'DarkExec install rejected: execution state root is {execution_root!r}, '
            "expected PosixPath('/var/lib/darkexec/executives').",
            file=sys.stderr,
        )
        return 1
    doctrine_refresh = values.get('DOCTRINE_REFRESH')
    if doctrine_refresh != '/usr/local/libexec/darkexec-refresh-harness-ops':
        print(
            f'DarkExec install rejected: doctrine refresh is {doctrine_refresh!r}, '
            "expected the Runtime-owned immutable distribution helper.",
            file=sys.stderr,
        )
        return 1
    for function in ('continue_target', 'stop_execution'):
        if not callable(values.get(function)):
            print(f'DarkExec install rejected: missing {function} stop-control contract.', file=sys.stderr)
            return 1
    print(json.dumps({
        'schemaVersion': 1,
        'runtime': str(runtime),
        'turnTimeoutDefault': turn_timeout,
        'executionRootDefault': str(execution_root),
        'doctrineRefreshDefault': doctrine_refresh,
        'stopControl': True,
    }, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

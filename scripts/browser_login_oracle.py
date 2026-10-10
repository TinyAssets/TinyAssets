"""Production-image browser proof using the existing migration/bootstrap oracle.

python scripts/browser_login_oracle.py --image browser-sign-in:mvp
The fixture server is real HTTP. Only the test relay maps its two public names
to loopback; the production relay has no private-network switch.
"""
from __future__ import annotations

import sys
from pathlib import Path

import role_image_oracle as oracle


def main():
    scenario = Path(__file__).resolve().parents[1] / 'tests' / 'browser_login_scenario.py'
    proof = scenario.read_text()
    oracle.LEG_NAMES = (*oracle.LEG_NAMES, 'browser')
    oracle.CELLS = oracle.CELLS.replace(
        'report, failed = {}, []', proof + "\nLEGS['browser'] = lambda: browser_proof(DATA, "
        "'alice', center_of('alice'), 'bob', center_of('bob'))\nreport, failed = {}, []")
    return oracle.main([*sys.argv[1:], '--prefix', 'browser-login-oracle',
                        '--stages', 'migrate,cells', '--legs', 'bootstrap,browser'])


if __name__ == '__main__':
    raise SystemExit(main())

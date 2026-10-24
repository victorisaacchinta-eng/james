"""Check the JAMES ledger's hash chain (iteration 10). Read-only: it never writes to the database.

    python -m tools.verify_ledger                          # the app's database (config.DB)
    python -m tools.verify_ledger path/to/james.db
    python -m tools.verify_ledger --expect 42:<hash>       # also check a head saved from a repair record

Exit code 0 = intact, 1 = broken (or the saved head no longer matches), 2 = no database or not a ledger, 3 = no chain yet
(an older database: open it once with this JAMES and it is sealed then)."""
from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    import config
    from james_core.ledger import Ledger
    expect = None
    args = list(argv)
    if "--expect" in args:
        i = args.index("--expect")
        try:
            seq_s, h = args[i + 1].split(":", 1)
            expect = (int(seq_s), h.strip())
        except (IndexError, ValueError):
            print("--expect needs SEQ:HASH, as printed at the end of a repair record")
            return 1
        del args[i:i + 2]
    path = Path(args[0]) if args else Path(config.DB)
    if not path.exists():
        print(f"No ledger at {path}")
        return 2
    import sqlite3
    try:
        led = Ledger(str(path), readonly=True)
        v = led.verify()
    except sqlite3.DatabaseError as e:
        print(f"Not a readable JAMES ledger: {path} ({e})")
        return 2
    print(f"Ledger {path}")
    if v.get("not_a_ledger"):
        print("  not a JAMES ledger (no events table)")
        return 2
    if v.get("unchained"):
        print(f"  events: {v['events']}   NO CHAIN YET: open it once with this version of JAMES")
        return 3
    if v["sealed_at_upgrade"]:
        a, b, n = v["sealed_at_upgrade"]
        print(f"  sealed at upgrade: {n} events (#{a} to #{b}), chained when this JAMES first opened the file "
              f"({v.get('legacy_sealed_at') or 'time unknown'}); edits made to them before that cannot be seen. "
              f"A recent date on an old ledger means the chain was rebuilt: check a saved head with --expect")
    print(f"  events: {v['events']}   head: #{v['head'][0]} {v['head'][1]}")
    rc = 0
    if not v["ok"]:
        print(f"  BROKEN at event #{v['first_bad']}")
        for p in v["problems"]:
            print(f"   - {p}")
        rc = 1
    if expect is not None:
        if led.check_head(*expect):
            print(f"  saved head #{expect[0]}: MATCHES")
        else:
            print(f"  saved head #{expect[0]}: DOES NOT MATCH (the event or its link changed, or it was removed)")
            rc = 1
    if rc == 0:
        print("  INTACT")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Container entry point: ``python -m tinyassets.serve``.

Importing this module does nothing, on purpose.

Every credential-broker and workspace child is a ``multiprocessing`` *spawn*
child, and a spawn child re-imports the parent's ``__main__`` module by name
before it runs anything. With ``python -m tinyassets.universe_server`` that is
the whole server, about 5 s of imports on the production box. A served round
spawns about three broker children (catalogue, benchmarks, inference), so every
round waited about 18 s before its model was asked. Measured live 2026-09-29:
19-22 s between every round's reservations on the free account, against 0.25 s
to start the same child under a light ``__main__``.

Under this launcher a spawn child re-imports only this file, and the server is
imported under its own name, so it is loaded once rather than also as
``__main__``. The children stay spawn children: nothing is inherited that was
not before.
"""

if __name__ == "__main__":
    # The production daemon starts here (the image CMD), so the SQLite floor is
    # asserted here (target-architecture S1a.1): before the server, its storage
    # and Litestream's replicated WAL are ever touched by an older library. This
    # guards the serving daemon only; other entry points (the console script,
    # `python -m tinyassets.universe_server`) and processes in the node sandbox,
    # which binds /usr without ld.so.cache, are outside this check.
    from tinyassets.sqlite_floor import require_sqlite_floor

    require_sqlite_floor()

    from tinyassets.universe_server import main

    main()

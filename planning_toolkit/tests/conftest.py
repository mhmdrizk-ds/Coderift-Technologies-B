"""Registers the `db` marker used by test_dynamic_decomposition_acyclicity.py
to separate pure model-level acyclicity tests (no DB, instant) from the
three that drive run_dynamic_decomposition() against the real local
Coderift DB (fast, local, but requires `python db/init_db.py` to have been
run at least once)."""


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "db: test drives real code against the local Coderift SQLite DB "
                   "(run `python db/init_db.py` first if it doesn't exist yet)."
    )

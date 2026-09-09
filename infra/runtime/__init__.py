"""Makes `runtime` a regular package.

Without this, `mypy --strict app.py stacks runtime tests` finds `runtime/migration_handler.py`
under two different module names -- "migration_handler" (via the bare `runtime` path argument) and
"runtime.migration_handler" (via `infra/tests/test_migration_handler.py`'s
`from runtime import migration_handler`) -- and refuses to check either. This file does not change
how the Lambda runtime imports the handler (`lambda_.Code.from_asset` bundles this directory as
the zip root, and the handler string `migration_handler.on_event` resolves there regardless of an
`__init__.py` sitting next to it).
"""

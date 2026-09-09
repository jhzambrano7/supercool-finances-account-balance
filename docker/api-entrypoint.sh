#!/usr/bin/env bash
# Brings the schema up to date, then hands the container over to whatever CMD asked for.
#
# Migrations run here, in the `dev` target only, because `docker compose up` is meant to be the one
# command that produces a working stack -- a developer who must remember a separate `alembic
# upgrade head` will eventually not, and will then debug a "relation does not exist" that is not a
# bug. The `runtime` target deliberately does not do this: mutating a schema as a side effect of a
# process booting is a production incident waiting for its first bad rollout.
set -euo pipefail

echo "[entrypoint] applying migrations…"
alembic upgrade head

echo "[entrypoint] starting: $*"
exec "$@"

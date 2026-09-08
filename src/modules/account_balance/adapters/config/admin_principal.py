from uuid import UUID

# The platform's one operator principal, authorized to invoke revert's operator-only operation
# (R1) -- a fixed, readable id rather than one minted by `IdGenerator`, mirroring
# `seeded_accounts.py`'s own reasoning for its two SYSTEM account ids. Documented in README.md so
# it is discoverable without reading this file.
ADMIN_PRINCIPAL_ID = UUID("00000000-0000-0000-0000-000000000003")

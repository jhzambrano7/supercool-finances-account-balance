from uuid import UUID

# T8: the two platform-seeded SYSTEM accounts (migration `5bf582a92358`) --
# fixed, readable ids rather than ones minted by `IdGenerator`, since nothing
# else in this codebase creates a SYSTEM account (account-opening only opens
# USER accounts, AO1). Both are owned by `PLATFORM_OWNER_ID`
# (domain/identifiers.py's nil UUID) and hold USD.
FUNDING_ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
SETTLEMENT_ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000002")

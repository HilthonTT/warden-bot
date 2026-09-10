# Contributing

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env               # then fill in DISCORD_TOKEN
```

Python 3.11 or newer is required (the playback loop uses `asyncio.timeout`).
Voice playback also needs `ffmpeg` on `PATH`.

Run the bot:

```bash
python src/bot.py
```

Set `DEV_GUILD_ID` while developing — slash commands sync to that guild
instantly instead of taking up to an hour to propagate globally.

## Checks

CI runs exactly these, so run them before pushing:

```bash
ruff check src tests
ruff format --check src tests
mypy
pytest
```

## Architecture

The dependency arrow points one way:

```
cogs  ->  services  ->  core / data
```

- `core/` — framework plumbing: settings, the bot class, permission
  decorators, embed builders, interaction replies, the error handler.
- `data/` — SQLite storage and the records it returns. Cogs never see a raw
  database row.
- `services/` — cross-cutting concerns owned by nobody in particular: guild
  config caching, mod-log delivery, warning escalation, the word filter, and
  the spam and raid trackers.
- `cogs/` — one Discord feature each. A cog may depend on services; it must
  never reach into another cog with `bot.get_cog(...)`.

### Adding a cog

1. Create `src/cogs/<name>.py` (or a package with an `__init__.py` that
   exports `setup`). It is auto-discovered at startup — no registration list.
2. Subclass `WardenCog` to get typed `self.db`, `self.config`,
   `self.modlog`, and `self.escalation`.
3. Gate privileged commands with `@guild_permissions(...)`, which applies the
   Discord-side default permission, the server-side check, and guild-only
   scoping together.
4. Reply through `core.responses.reply` / `fail` so deferred interactions are
   handled correctly.
5. `/documentation` picks the command up automatically.

### Changing the schema

Append a step to `MIGRATIONS` in `src/data/db.py`, update `BASE_SCHEMA` to
match (fresh databases are built from it and stamped, never migrated), and
bump `SCHEMA_VERSION` — all in the same commit. Never edit a migration that
has already shipped, and add a case to `tests/test_migration.py`, which builds
a database at the old schema and asserts nothing is lost.

## Commit and PR conventions

- Conventional-commit prefixes (`feat:`, `fix:`, `refactor:`, `docs:`,
  `chore:`), matching the existing history.
- One logical change per PR.
- New environment variables go in `.env.example`, `core/settings.py`, and the
  README together.

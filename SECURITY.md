# Security policy

## Reporting a vulnerability

Report privately through
[GitHub security advisories](https://github.com/HilthonTT/TheMonitorBot/security/advisories/new).
Please do not open a public issue for anything that could be used to bypass
moderation, escalate permissions, or expose a token.

Expect an initial response within seven days.

## Supported versions

The latest release on `main` is the only supported version.

## Operational notes

- **Never commit `.env`.** It is git-ignored; if a token reaches a commit,
  reset it in the Discord developer portal — rewriting history is not enough,
  because the old commit may already be mirrored.
- The bot needs Manage Messages, Kick Members, Ban Members, and Manage
  Channels. Grant nothing beyond that; it does not use Administrator.
- Privileged gateway intents (members, presence, message content) must be
  enabled for the application. Message content is what the automod and
  honeypot listeners read.
- Ticket transcripts contain the full contents of a private channel. They are
  delivered to the configured mod-log channel — restrict who can read it.
- The SQLite database holds warning reasons and user IDs. Back up and store
  it with the same care as any moderation record.

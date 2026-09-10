# Warden

[![CI](https://github.com/HilthonTT/warden-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/HilthonTT/warden-bot/actions/workflows/ci.yml)
[![CodeQL](https://github.com/HilthonTT/warden-bot/actions/workflows/codeql.yml/badge.svg)](https://github.com/HilthonTT/warden-bot/actions/workflows/codeql.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A `discord.py` 2.x bot with rich `/userinfo`, full moderation, automod with a
leet-speak-aware language filter, a spam-bot honeypot, a persistent-button
support-ticket system, voice playback, and trivia. State is stored in SQLite
via `aiosqlite` (WAL mode).

> **Terminology:** Discord's API and every library call them **guilds**.
> The user-facing UI calls them **servers**. They're the same thing.
> This README and the code use both interchangeably.

## Features

### User info (`src/cogs/user_info.py`)

- `/userinfo` — rich profile embed (account age, join date, roles, status, badges, banner)
- `/avatar` — avatar with PNG / WEBP / GIF download buttons
- "User Info" right-click context menu
- Per-user cooldowns guard the underlying Discord REST calls

### Moderation (`src/cogs/moderation.py`)

- `/kick`, `/ban`, `/unban` with reason + DM-before-action
- `/timeout` and `/untimeout` — Discord's native timeout, with durations
  written the way moderators think (`10m`, `2h30m`, `7d`)
- `/tempban` — a ban that lifts itself; a background sweeper unbans on expiry
  and retries after a restart rather than leaving someone banned forever
- `/purge` — bulk delete, optionally filtered to one member or a substring
- `/warn` with DM and persistent record
- `/warnings` (paginated), `/clearwarnings`, `/delwarn`
- **Case log** — every action gets a per-server case number. `/case <n>` looks
  one up; `/cases @user` shows a member's full history, paginated
- **Warning expiry** — `/set_warn_expiry` makes warnings stop counting toward
  auto-kick and auto-ban after N days, so stale history can't remove someone
- Hierarchy & self-protection checks (no banning the owner, no banning above the bot's role, etc.)
- Auto-escalation: configurable kick and ban warning thresholds
- All commands defer the interaction before slow REST/DM work, so they never
  time out on the 3-second ack window

### Automod (`src/cogs/automod.py`)

- Bad-language filter with normalization that defeats common bypasses:
  zero-width chars stripped, leet substitutions (`f4ck`, `sh!t`, `b1tch`),
  punctuation/spacing squashed (`f.u.c.k`)
- Whole-word matching via `\b` boundaries to minimize false positives
- Staff (Manage Messages or the configured staff role) are exempt
- **Per-server word lists** — `src/data/bad_words.txt` seeds every server and
  `/words add|remove|list|reset` tailors it per community; `/automod_reload`
  picks up edits to the shipped defaults live
- **Rate-based spam filter** (`/antispam`) — message flooding, repeated
  messages, and mass mentions, none of which a word list can see. One flood
  produces one warning, not one per message
- **Invite filter** — optional blocking of links to other Discord servers
- `/automod` toggle per server
- Per-guild config is cached in memory and invalidated on writes, so the
  filter doesn't hit SQLite on every message

### Channel controls (`src/cogs/channels.py`)

- `/lock` and `/unlock` — stop and restore `@everyone` posting, preserving
  every other permission overwrite on the channel
- `/slowmode` — rate-limit a channel, with the same duration syntax

### Event logging (`src/cogs/eventlog.py`)

- Message deletions and edits (before/after), bulk deletes, joins and leaves
- Goes to its own channel (`/set_eventlog`), kept separate from the mod log so
  a busy edit feed doesn't bury the record of what moderators did
- New accounts are flagged on join

### Gatekeeper (`src/cogs/gatekeeper.py`)

- **Minimum account age** — turn away accounts younger than a threshold. It
  kicks rather than bans, because a false positive should be recoverable
- **Raid alerts** — watches the join rate and reports a spike to the mod log,
  with a cooldown so a sustained raid produces one alert, not one per joiner
- Both configured with `/gatekeeper`, both off by default

### Honeypot (`src/cogs/automod.py`)

- Set any channel as a honeypot via `/set_honeypot`
- Anyone without moderator permissions (Admin, Manage Server, Manage Messages,
  Kick Members, Ban Members) or the configured staff role who posts there is
  banned instantly with 24h of message cleanup
- Detailed report posted to mod log: account age, join date, message preview
- Catches scraping spam bots that post in every channel they can read

### Tickets (`src/cogs/tickets.py`)

- `/ticket_panel` posts an embed with a persistent "Open Ticket" button
- Each ticket creates a private text channel (user + staff role + bot only)
- One open ticket per user per server — enforced via per-user lock **and** a
  UNIQUE partial index in SQLite (defence in depth against double-click races)
- Atomic per-guild ticket numbering — no `MAX()+1` race
- "Close Ticket" button or `/ticket_close` saves a plaintext transcript to the
  mod-log channel and deletes the ticket channel. If the mod log is missing or
  the send fails, the channel is **kept open** and the transcript file is
  posted in-channel so it is never silently lost
- `/ticket_add` and `/ticket_remove` for adding extra participants
- Persistent views — buttons keep working across bot restarts

### Music (`src/cogs/music/music.py`)

- `/join` — bot joins your voice channel (or one you specify)
- `/play <search>` — search YouTube and queue a song (auto-joins if not in VC)
- `/pause`, `/resume`, `/skip`
- `/queue` — show the next 5 queued songs
- `/nowplaying` — show the currently playing song
- `/volume <1-100>` — adjust playback volume
- `/stop` — clear the queue and disconnect
- One queue + playback loop per guild; auto-disconnects after 5 min idle
  **and** as soon as the last human leaves the voice channel
- Streams via `yt-dlp` and `FFmpeg` — no files are downloaded
- Stream URLs are re-resolved when a track reaches the front of the queue,
  because they expire minutes after they are issued
- Requires **FFmpeg** on `PATH` (already installed in the Docker image)

### Trivia (`src/cogs/trivia.py`)

- `/trivia` — a multiple-choice science & computing question from the Open
  Trivia Database; first answer wins, buttons reveal the answer on timeout

### Help (`src/cogs/documentation.py`)

- `/documentation` — every command the invoker can actually run, grouped by
  permission tier and generated from the live command tree, so it never
  drifts from what is installed

### Configuration (`src/cogs/admin.py`)

- `/set_modlog` — channel for moderation embeds and transcripts
- `/set_eventlog` — channel for message and member event logging
- `/set_staff_role` — the role exempt from automod and granted ticket access
- `/set_honeypot` / `/clear_honeypot`
- `/ticket_config` — set the ticket category and staff role
- `/set_warn_thresholds` — tune auto-kick / auto-ban
- `/set_warn_expiry` — how long a warning counts
- `/antispam`, `/gatekeeper` — spam and join-time protection
- `/config` — show current settings

---

## Setup

The setup splits into two phases: getting the bot **online** (the developer
portal, environment, and Python side), then getting it **configured** (the
slash commands you run inside your server once the bot is in it).

### Phase 1 — Get the bot online

#### 1. Create the application and bot

1. Go to <https://discord.com/developers/applications> and click **New Application**. Name it whatever you want.
2. In the left sidebar, open the **Bot** tab.
3. Under **Privileged Gateway Intents**, enable all three:
   - ✅ **Presence Intent** — needed for `/userinfo` to show online status
   - ✅ **Server Members Intent** — needed for member events and role lists
   - ✅ **Message Content Intent** — needed for automod + honeypot
4. Click **Reset Token** → **Copy**. This is your `DISCORD_TOKEN`. Treat it
   like a password — anyone with it can control the bot.

#### 2. Invite the bot to your server

Still in the developer portal:

1. Open the **OAuth2** tab → **URL Generator**.
2. Under **Scopes**, check `bot` and `applications.commands`.
3. Under **Bot Permissions**, check at minimum:
   `Manage Channels`, `Manage Roles`, `Kick Members`, `Ban Members`,
   `View Channels`, `Send Messages`, `Manage Messages`,
   `Embed Links`, `Attach Files`, `Read Message History`.
4. Copy the generated URL at the bottom, paste it into your browser, pick
   your server, and authorize.

> **Heads up on role hierarchy.** Once the bot joins, drag its auto-created
> role **above** any role it should be able to moderate. Discord forbids
> moderating users whose top role is at or above the bot's. The cog enforces
> this and tells you if you try.

#### 3. Install Python dependencies

Requires **Python 3.11 or newer** (the playback loop uses `asyncio.timeout`).
From the project root:

```bash
python -m venv venv
venv\Scripts\activate           # Windows (PowerShell or cmd)
# source venv/bin/activate      # Linux/macOS
pip install -r requirements.txt
```

`requirements.txt` pins:

| Package         | Why                                                       |
| --------------- | --------------------------------------------------------- |
| `discord.py`    | Discord gateway + slash-command framework                 |
| `python-dotenv` | Loads `.env` at startup                                   |
| `aiosqlite`     | Async SQLite for config / warnings / tickets              |
| `aiohttp`       | Non-blocking outbound HTTP (the trivia API)               |
| `yt-dlp`        | Resolves YouTube (and other site) audio streams for music |

`discord.py[voice]` pulls in `PyNaCl` for voice packet encryption.

##### Music cog system requirements

The music cog also needs two things **outside** of `pip`:

1. **FFmpeg** must be on `PATH`. Test with `ffmpeg -version`.
   - **Windows:** `winget install Gyan.FFmpeg` (or download from
     <https://www.gyan.dev/ffmpeg/builds/> and add `bin/` to `PATH`) or download using choco
     `choco install ffmpeg`
   - **macOS:** `brew install ffmpeg`
   - **Debian/Ubuntu:** `sudo apt install ffmpeg`
2. **libopus** — bundled with `PyNaCl` wheels on Windows/macOS. On
   minimal Linux images you may need `sudo apt install libopus0`. The
   provided Docker image installs both already.

If you don't intend to use voice, none of the above matters — the cog
will simply fail to load and the rest of the bot will run.

#### 4. Configure environment variables

Copy `.env.example` to `.env` and fill in at least the token:

```
DISCORD_TOKEN=paste_the_token_from_step_1_here
DEV_GUILD_ID=                    # optional — see below
ACTIVITY_STATUS=Always monitoring your behavior     # optional
BOT_DB_PATH=                     # optional — defaults to data/bot.sqlite3
LOG_LEVEL=                       # optional — DEBUG/INFO/WARNING/ERROR
```

##### How to get a `DEV_GUILD_ID` (and why you want one)

`DEV_GUILD_ID` is your Discord **server's** ID. Slash commands sync
**globally by default**, which Discord can take **up to an hour** to
propagate. Setting `DEV_GUILD_ID` makes the bot register commands directly
on that one server, which is **near-instant** — invaluable while iterating.

To copy a server ID:

1. In Discord, go to **User Settings → Advanced** and turn on
   **Developer Mode**.
2. Right-click your server's icon in the sidebar → **Copy Server ID**.
3. Paste it into `.env` as `DEV_GUILD_ID=...` (a long number, no quotes).

The same right-click trick also gives you channel IDs and role IDs if you
ever need them — though the in-server commands below let you pick channels
and roles via Discord's autocomplete UI without touching IDs.

> When you go to production, **clear `DEV_GUILD_ID`** so commands sync
> globally and work in every server the bot is in.

#### 5. Run the bot

```bash
cd src
python bot.py
```

A successful startup looks like:

```
[INFO] data.db: Database ready at data/bot.sqlite3 (schema v1)
[INFO] bot: Loaded extension cogs.admin
[INFO] bot: Loaded extension cogs.automod
[INFO] bot: Loaded extension cogs.documentation
[INFO] bot: Loaded extension cogs.moderation
[INFO] bot: Loaded extension cogs.tickets
[INFO] bot: Loaded extension cogs.user_info
[INFO] bot: Loaded extension cogs.music.music
[INFO] bot: Synced 31 commands to dev guild 123456789012345678
[INFO] bot: Logged in as YourBot#1234 (id=...)
```

Leave it running. Ctrl+C to stop. On Linux/macOS, SIGTERM is also handled
gracefully so the bot closes its DB connection cleanly under Docker/systemd.

### Phase 2 — Configure the server

With the bot online and in your server, run these slash commands. You need
**Manage Server** for all of them. Discord autocomplete will help — type `/`
and the command name and it'll prompt you for channels/roles.

#### Required for all features

```
/set_modlog channel:#mod-log
```

Pick (or create) a private staff-only channel. All moderation actions,
auto-warn reports, honeypot bans, and ticket transcripts get posted here.
Without this set, those events still happen but transcripts get posted
back in the ticket channel as a fallback.

#### Required for tickets

```
/ticket_config category:Tickets staff_role:@Staff
```

`category` is a Discord **channel category** under which new ticket
channels will be created. `staff_role` is the role that should be granted
access to every ticket. Create both first if you don't have them.

```
/ticket_panel
```

Run this **in the channel where you want users to open tickets from** (e.g.
`#support`). It posts an embed with an "Open Ticket" button. Users click it
to spawn a private channel.

#### Optional — honeypot anti-spam

Create a channel like `#・do-not-post` first. **Important:** in the channel
settings, deny `View Channel` for `@everyone` and for any role that should
never get banned. The point is that no human should ever see or post there;
only spam bots that scrape every readable channel via the API will find it.

```
/set_honeypot channel:#・do-not-post
```

To disable later: `/clear_honeypot`.

#### Optional — warning thresholds

Defaults are auto-kick at 3 warnings, auto-ban at 5. Change with:

```
/set_warn_thresholds kick_threshold:3 ban_threshold:5
```

`ban_threshold` must be ≥ `kick_threshold`.

#### Verify

```
/config
```

Shows everything currently set for this server.

---

## Deployment

### Docker (recommended)

A `Dockerfile` and `docker-compose.yml` are included. The image is
multi-stage, runs as a non-root user, ships FFmpeg and libopus for voice, and
persists state to a `/data` volume.

Every push to `main` and every `v*.*.*` tag publishes a multi-architecture
image (amd64 + arm64) with build provenance to GitHub Container Registry, so
you can skip the build entirely:

```bash
docker pull ghcr.io/hilthontt/warden-bot:latest
```

To build locally instead:

```bash
# Build and start. .env in the repo root is read for credentials.
docker compose up -d --build

# Tail logs
docker compose logs -f bot

# Stop (gracefully closes the gateway and DB)
docker compose down
```

The compose file maps a named volume `bot-data` to `/data` inside the
container; the bot writes its SQLite database to `/data/bot.sqlite3`.
The `restart: unless-stopped` policy auto-restarts on crash.

### Systemd / process manager

For a non-containerised install on Linux, a minimal unit file looks like:

```ini
[Unit]
Description=Warden Discord bot
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/srv/warden-bot/src
EnvironmentFile=/srv/warden-bot/.env
ExecStart=/srv/warden-bot/venv/bin/python bot.py
Restart=on-failure
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=30
User=bot

[Install]
WantedBy=multi-user.target
```

### Backups

The SQLite database is the only persistent state. Back up `data/bot.sqlite3`
(or the Docker volume) before upgrades. WAL mode means an online
`sqlite3 bot.sqlite3 ".backup '/path/to/backup.sqlite3'"` is safe while the
bot is running.

---

## Project layout

```
warden-bot/
├── src/
│   ├── bot.py                    entry point: env, signals, exit codes
│   ├── core/                     framework layer (no Discord features)
│   │   ├── bot.py                WardenBot: wiring, lifecycle, cog discovery
│   │   ├── settings.py           environment parsed and validated once
│   │   ├── checks.py             permission decorators, hierarchy guards
│   │   ├── duration.py           "2h30m" -> timedelta, and back
│   │   ├── pagination.py         paginated embed view
│   │   ├── embeds.py             embed builders with Discord-safe truncation
│   │   ├── responses.py          reply/fail/DM helpers
│   │   ├── errors.py             global app-command error handler
│   │   ├── constants.py          Discord API limits
│   │   └── cog.py                WardenCog base with typed services
│   ├── services/                 cross-cutting concerns shared by cogs
│   │   ├── guild_config.py       cached per-guild config (single owner)
│   │   ├── modlog.py             mod-log and event-log delivery
│   │   ├── escalation.py         warning-threshold auto-kick / auto-ban
│   │   ├── spam.py               flood / repetition / mass-mention detection
│   │   ├── raid.py               join-rate tracking
│   │   └── word_filter.py        obfuscation-tolerant per-guild matching
│   ├── data/
│   │   ├── db.py                 aiosqlite repository + migrations
│   │   ├── models.py             GuildConfig / Case / Ticket
│   │   └── bad_words.txt         filter word list — edit freely
│   └── cogs/                     one Discord feature each, auto-discovered
│       ├── admin.py              per-server config
│       ├── moderation.py         kick / ban / timeout / warn / purge / cases
│       ├── automod.py            language, spam and invite filters + honeypot
│       ├── channels.py           lock / unlock / slowmode
│       ├── eventlog.py           message and member event logging
│       ├── gatekeeper.py         account-age gate and raid alerts
│       ├── tickets.py            ticket panel + private channels
│       ├── user_info.py          /userinfo, /avatar, context menu
│       ├── documentation.py      /documentation, generated from the tree
│       ├── trivia.py             /trivia
│       └── music/                voice playback (package)
│           ├── music.py          slash commands
│           ├── music_player.py   per-guild queue + playback loop
│           ├── track.py          queued-track record
│           └── music_utils/      yt-dlp resolution, FFmpeg options, errors
├── tests/                        pytest suite (no network, no Discord)
├── .github/workflows/            CI, release, CodeQL
├── Dockerfile                    multi-stage, non-root, ffmpeg included
├── docker-compose.yml
├── pyproject.toml                python>=3.11, ruff, mypy, pytest config
├── requirements.txt              runtime dependencies
├── requirements-dev.txt          runtime + lint/type/test tooling
├── CONTRIBUTING.md
├── SECURITY.md
├── .env.example
└── README.md
```

### Layering

```
cogs  ->  services  ->  core / data
```

A cog may use any service; no cog reaches into another cog. Shared state
(guild config, mod-log delivery, escalation) is owned by a service, so there
is no load-order coupling and no cache to invalidate by hand.

---

## Development

```bash
pip install -r requirements-dev.txt

ruff check src tests          # lint
ruff format --check src tests # formatting
mypy                          # type check (config in pyproject.toml)
pytest                        # unit tests + a cog-loading smoke test
```

CI runs exactly these four commands on Python 3.11, 3.12, and 3.13, plus a
Docker build. See [CONTRIBUTING.md](CONTRIBUTING.md) for the architecture
rules and how to add a cog or a schema migration.

The test suite never touches the network or Discord: it exercises the storage
layer against a temporary SQLite file, the word filter, settings parsing,
embed truncation, and loads every cog into a real `WardenBot` instance to
catch broken commands before deploy.

---

## Troubleshooting

**"Disallowed intents" / bot won't start.** You didn't enable all three
privileged intents in the developer portal. Go back to **Phase 1, step 1**.

**Slash commands don't appear in Discord.** Either `DEV_GUILD_ID` is wrong
(double-check by right-clicking the server icon → Copy Server ID), or it's
unset and you're waiting on global sync — give it up to an hour, or set
`DEV_GUILD_ID` to your test server for instant updates.

**`Extension 'cogs.X' has no 'setup' function`.** Every cog must end with:

```python
async def setup(bot: WardenBot) -> None:
    await bot.add_cog(MyCog(bot))
```

A cog **package** needs an `__init__.py` that re-exports `setup`; without one
it is skipped with a warning at startup.

If you're authoring a new cog, copy that pattern from any existing one.

**Bot can't kick/ban "user with higher role".** Drag the bot's role above
the target's role in **Server Settings → Roles**. The bot's hierarchy
checks are enforcing a real Discord restriction, not being picky.

**Honeypot didn't ban anyone.** Any member with a moderator-tier permission
(Admin, Manage Server, Manage Messages, Kick Members, Ban Members) or the
configured staff role is exempt. Test with an alt account that has no
special permissions.

**Music: `/play` errors with "Couldn't fetch that song".** Almost always
either FFmpeg is not on `PATH` (run `ffmpeg -version` to confirm) or your
`yt-dlp` is outdated against YouTube's latest changes
(`pip install -U yt-dlp`).

**Music: bot joins VC but plays nothing.** Missing `PyNaCl` or libopus.
Reinstall with `pip install --force-reinstall "discord.py[voice]"`.

**Upgrading from an older version.** The database migrates itself on start.
v1 warnings become numbered cases; nothing is lost, and the upgrade is covered
by `tests/test_migration.py`. Back the file up first anyway.

**Someone stayed banned after their `/tempban` expired.** Discord has no
native expiring ban, so the bot lifts it from a background sweep every minute.
Check that the bot still has **Ban Members** and is still in the server; the
case stays active until the unban succeeds, so it retries.

**Reset everything.** Stop the bot and delete `data/bot.sqlite3` (plus the
`-wal` and `-shm` sidecar files if present). All per-server config,
warnings, and ticket records will be wiped on next start.

---

## Notes

- Automod warnings use the bot as the moderator and count toward the same
  auto-escalation thresholds as manual warnings.
- Every moderation action is a numbered case in one table, which is what lets
  a warning expire and lets `/case` explain a kick months later.
- The honeypot exempts every member with mod permissions or the configured
  staff role, so a typo from staff never bans them.
- Persistent ticket buttons survive restarts because their `custom_id`s are
  stable and `bot.add_view()` is called in `setup_hook`.
- SQLite is created on first run at `data/bot.sqlite3` (or `BOT_DB_PATH`
  if set). WAL mode + a 5-second `busy_timeout` are enabled for resilience.
- A `schema_version` table tracks DB upgrades so future migrations stay
  idempotent.

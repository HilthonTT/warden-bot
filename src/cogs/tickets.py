"""Support tickets.

Flow:
  1. An admin runs ``/ticket_config`` to set the category and staff role.
  2. An admin runs ``/ticket_panel`` where users should open tickets; that
     posts an embed with an "Open Ticket" button.
  3. A user clicks it and the bot creates a private channel under the
     category, visible to the user, the staff role, and the bot.
  4. Either the opener or staff clicks "Close Ticket": the bot saves a
     plaintext transcript, delivers it, and deletes the channel.

Both views are **persistent** — stable ``custom_id``s, ``timeout=None``, and
``bot.add_view`` in :meth:`~core.bot.MonitorBot.setup_hook` — so buttons on
messages posted months ago still work after a restart.

Concurrency: a per-(guild, user) lock serialises simultaneous clicks, and the
DB layer backs it with a UNIQUE partial index over open tickets.
"""

from __future__ import annotations

import asyncio
import io
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from core.checks import guild_permissions
from core.cog import MonitorCog
from core.constants import truncate
from core.embeds import base_embed, info_embed
from core.responses import fail, reply
from data.models import Ticket

if TYPE_CHECKING:
    from core.bot import MonitorBot

log = logging.getLogger(__name__)

TICKET_OPEN_ID = "ticket:open"
TICKET_CLOSE_ID = "ticket:close"
COG_NAME = "Tickets"

TRANSCRIPT_MESSAGE_LIMIT = 5_000
TRANSCRIPT_BYTE_LIMIT = 7 * 1024 * 1024

PANEL_DEFAULT_DESCRIPTION = (
    "Click the button below to open a private support ticket.\n"
    "A staff member will be with you shortly."
)


def _get_cog(interaction: discord.Interaction) -> Tickets | None:
    client = interaction.client
    if not isinstance(client, commands.Bot):
        return None
    cog = client.get_cog(COG_NAME)
    return cog if isinstance(cog, Tickets) else None


class TicketPanelView(discord.ui.View):
    """Persistent "Open Ticket" button posted on a public panel message."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Open Ticket",
        emoji="🎫",
        style=discord.ButtonStyle.primary,
        custom_id=TICKET_OPEN_ID,
    )
    async def open_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        cog = _get_cog(interaction)
        if cog is None:
            await fail(interaction, "The ticket system is unavailable right now.")
            return
        await cog.open_ticket(interaction)


class TicketCloseView(discord.ui.View):
    """Persistent "Close Ticket" button placed inside each ticket channel."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close Ticket",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id=TICKET_CLOSE_ID,
    )
    async def close_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        cog = _get_cog(interaction)
        if cog is None:
            await fail(interaction, "The ticket system is unavailable right now.")
            return
        await cog.close_ticket(interaction)


class Tickets(MonitorCog):
    """Support ticket commands and persistent button handlers."""

    def __init__(self, bot: MonitorBot) -> None:
        super().__init__(bot)
        self._open_locks: dict[tuple[int, int], asyncio.Lock] = {}

    @app_commands.command(
        name="ticket_panel",
        description="Post an Open-Ticket panel in this channel.",
    )
    @app_commands.describe(
        title="Panel title.",
        description="Panel description shown above the button.",
    )
    @guild_permissions(manage_guild=True)
    async def ticket_panel(
        self,
        interaction: discord.Interaction,
        title: str = "Need help?",
        description: str = PANEL_DEFAULT_DESCRIPTION,
    ) -> None:
        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            await fail(interaction, "Run this in a server text channel.")
            return

        embed = info_embed(title, description)
        embed.set_footer(text="Abuse of the ticket system may result in moderation action.")
        try:
            await channel.send(embed=embed, view=TicketPanelView())
        except discord.Forbidden:
            await fail(interaction, "I can't post in this channel.")
            return
        except discord.HTTPException as exc:
            await fail(interaction, f"Could not post the panel: {exc}")
            return
        await reply(interaction, "✅ Panel posted.", ephemeral=True)

    @app_commands.command(
        name="ticket_config",
        description="Configure the ticket system (category & staff role).",
    )
    @app_commands.describe(
        category="Category where ticket channels will be created.",
        staff_role="Role granted access to all tickets.",
    )
    @guild_permissions(manage_guild=True)
    async def ticket_config(
        self,
        interaction: discord.Interaction,
        category: discord.CategoryChannel,
        staff_role: discord.Role,
    ) -> None:
        guild = interaction.guild
        assert guild is not None

        me = guild.me
        if me is not None and not category.permissions_for(me).manage_channels:
            await fail(
                interaction,
                f"I need **Manage Channels** in **{category.name}** to open tickets there.",
            )
            return

        await self.config.update(
            guild.id,
            ticket_category_id=category.id,
            staff_role_id=staff_role.id,
        )
        await reply(
            interaction,
            f"✅ Tickets will open under **{category.name}** and grant access to "
            f"**@{staff_role.name}**.",
            ephemeral=True,
        )

    @app_commands.command(
        name="ticket_open",
        description="Open a support ticket (alternative to the panel button).",
    )
    @app_commands.guild_only()
    async def ticket_open_slash(self, interaction: discord.Interaction) -> None:
        await self.open_ticket(interaction)

    @app_commands.command(
        name="ticket_close",
        description="Close the current ticket channel.",
    )
    @app_commands.guild_only()
    async def ticket_close_slash(self, interaction: discord.Interaction) -> None:
        await self.close_ticket(interaction)

    @app_commands.command(
        name="ticket_add",
        description="Add a user to the current ticket channel.",
    )
    @app_commands.describe(user="The user to add.")
    @guild_permissions(manage_channels=True)
    async def ticket_add(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        resolved = await self._resolve_ticket(interaction)
        if resolved is None:
            return
        channel, _ = resolved

        try:
            await channel.set_permissions(
                user,
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
                reason=f"Added to ticket by {interaction.user}",
            )
        except discord.Forbidden:
            await fail(interaction, "I lack permission to edit this channel.")
            return
        await reply(interaction, f"✅ Added {user.mention} to the ticket.")

    @app_commands.command(
        name="ticket_remove",
        description="Remove a user from the current ticket channel.",
    )
    @app_commands.describe(user="The user to remove.")
    @guild_permissions(manage_channels=True)
    async def ticket_remove(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        resolved = await self._resolve_ticket(interaction)
        if resolved is None:
            return
        channel, ticket = resolved

        if user.id == ticket.user_id:
            await fail(interaction, "The ticket opener can't be removed.")
            return
        try:
            await channel.set_permissions(
                user,
                overwrite=None,
                reason=f"Removed from ticket by {interaction.user}",
            )
        except discord.Forbidden:
            await fail(interaction, "I lack permission to edit this channel.")
            return
        await reply(interaction, f"✅ Removed {user.mention} from the ticket.")

    async def _resolve_ticket(
        self,
        interaction: discord.Interaction,
    ) -> tuple[discord.TextChannel, Ticket] | None:
        """Return the channel and its ticket row, or explain why not."""
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await fail(interaction, "Run this inside a ticket channel.")
            return None
        ticket = await self.db.get_ticket(channel.id)
        if ticket is None:
            await fail(interaction, "This isn't a tracked ticket channel.")
            return None
        return channel, ticket

    def _lock_for(self, guild_id: int, user_id: int) -> asyncio.Lock:
        return self._open_locks.setdefault((guild_id, user_id), asyncio.Lock())

    def _release_lock(self, guild_id: int, user_id: int) -> None:
        """Drop an idle lock so the map doesn't grow once per user forever."""
        key = (guild_id, user_id)
        lock = self._open_locks.get(key)
        if lock is not None and not lock.locked():
            self._open_locks.pop(key, None)

    async def open_ticket(self, interaction: discord.Interaction) -> None:
        """Create a private ticket channel for the invoking user."""
        guild = interaction.guild
        if guild is None:
            await fail(interaction, "Tickets can only be opened in a server.")
            return

        cfg = await self.config.get(guild.id)
        if cfg.ticket_category_id is None or cfg.staff_role_id is None:
            await fail(
                interaction,
                "Tickets aren't configured yet — an admin must run `/ticket_config`.",
            )
            return

        category = guild.get_channel(cfg.ticket_category_id)
        staff_role = guild.get_role(cfg.staff_role_id)
        if not isinstance(category, discord.CategoryChannel) or staff_role is None:
            await fail(
                interaction,
                "The configured category or staff role no longer exists. "
                "Ask an admin to re-run `/ticket_config`.",
            )
            return

        opener = interaction.user
        if not isinstance(opener, discord.Member):
            await fail(interaction, "Tickets can only be opened in a server.")
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        lock = self._lock_for(guild.id, opener.id)
        try:
            async with lock:
                await self._create_ticket(interaction, guild, opener, category, staff_role)
        finally:
            self._release_lock(guild.id, opener.id)

    async def _create_ticket(
        self,
        interaction: discord.Interaction,
        guild: discord.Guild,
        opener: discord.Member,
        category: discord.CategoryChannel,
        staff_role: discord.Role,
    ) -> None:
        existing = await self.db.get_open_ticket(guild.id, opener.id)
        if existing is not None:
            channel = guild.get_channel(existing)
            if channel is not None:
                await reply(
                    interaction,
                    f"You already have an open ticket: {channel.mention}",
                    ephemeral=True,
                )
                return
            await self.db.close_ticket(existing)

        overwrites: dict[
            discord.Role | discord.Member | discord.Object,
            discord.PermissionOverwrite,
        ] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            opener: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
            staff_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
                attach_files=True,
                embed_links=True,
            ),
        }
        if guild.me is not None:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
                embed_links=True,
            )

        try:
            channel = await guild.create_text_channel(
                name="ticket-pending",
                category=category,
                overwrites=overwrites,
                topic=truncate(f"Ticket for {opener} ({opener.id})", 1024),
                reason=f"Ticket opened by {opener}",
            )
        except discord.Forbidden:
            await fail(interaction, "I lack permission to create channels in that category.")
            return
        except discord.HTTPException as exc:
            await fail(interaction, f"Discord error creating the channel: {exc}")
            return

        try:
            number = await self.db.create_ticket(channel.id, guild.id, opener.id)
        except Exception:
            log.exception("Failed to persist ticket; removing channel %s", channel.id)
            try:
                await channel.delete(reason="Failed to persist ticket")
            except discord.HTTPException:
                log.warning("Could not clean up channel %s", channel.id, exc_info=True)
            await fail(interaction, "Internal error opening your ticket. Please try again.")
            return

        ticket = Ticket(
            channel_id=channel.id,
            guild_id=guild.id,
            user_id=opener.id,
            number=number,
            status="open",
            created_at=int(datetime.now(tz=UTC).timestamp()),
        )

        try:
            await channel.edit(name=f"ticket-{ticket.label}")
        except discord.HTTPException:
            log.debug("Could not rename ticket channel %s", channel.id, exc_info=True)

        welcome = info_embed(
            f"Ticket #{ticket.label}",
            f"Hi {opener.mention}, a member of {staff_role.mention} will be "
            f"with you shortly.\n\nPlease describe your issue in as much detail as "
            f"possible.\nClick **Close Ticket** below when you're done.",
        )
        welcome.set_footer(text=f"Opened by {opener}")
        try:
            await channel.send(
                content=f"{opener.mention} {staff_role.mention}",
                embed=welcome,
                view=TicketCloseView(),
                allowed_mentions=discord.AllowedMentions(users=True, roles=True),
            )
        except discord.HTTPException:
            log.warning("Could not post the welcome message in %s", channel.id, exc_info=True)

        await reply(interaction, f"✅ Your ticket is open: {channel.mention}", ephemeral=True)

    async def close_ticket(self, interaction: discord.Interaction) -> None:
        """Archive and delete the current ticket channel."""
        resolved = await self._resolve_ticket(interaction)
        if resolved is None:
            return
        channel, ticket = resolved

        is_owner = interaction.user.id == ticket.user_id
        is_staff_member = (
            isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.manage_channels
        )
        if not (is_owner or is_staff_member):
            await fail(interaction, "Only the ticket opener or staff can close this.")
            return

        await interaction.response.send_message("🔒 Closing ticket and saving transcript…")

        transcript = await self._build_transcript(channel, ticket, interaction.user)
        delivered = await self._deliver_transcript(channel, ticket, interaction.user, transcript)

        if not delivered:
            try:
                await channel.send(
                    "⚠️ I couldn't deliver the transcript to the mod log or by DM, so "
                    "this channel is being kept. Set a mod log with `/set_modlog` and "
                    "close the ticket again.",
                    file=discord.File(
                        io.BytesIO(transcript),
                        filename=f"ticket-{ticket.label}.txt",
                    ),
                )
            except discord.HTTPException:
                log.warning("Could not attach the fallback transcript", exc_info=True)
            return

        await self.db.close_ticket(channel.id)
        try:
            await channel.delete(reason=f"Ticket closed by {interaction.user}")
        except discord.HTTPException:
            log.warning("Could not delete ticket channel %s", channel.id, exc_info=True)

    async def _build_transcript(
        self,
        channel: discord.TextChannel,
        ticket: Ticket,
        closer: discord.User | discord.Member,
    ) -> bytes:
        opened = datetime.fromtimestamp(ticket.created_at, tz=UTC).isoformat()
        closed = datetime.now(tz=UTC).isoformat()
        lines = [
            f"# Ticket #{ticket.label}",
            f"# Opened by user id {ticket.user_id} on {opened}",
            f"# Closed by {closer} on {closed}",
            "",
        ]

        size = 0
        try:
            async for message in channel.history(
                limit=TRANSCRIPT_MESSAGE_LIMIT,
                oldest_first=True,
            ):
                stamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
                attachments = " ".join(a.url for a in message.attachments)
                line = f"[{stamp}] {message.author}: {message.content} {attachments}".rstrip()
                size += len(line) + 1
                if size > TRANSCRIPT_BYTE_LIMIT:
                    lines.append("(transcript truncated: size limit reached)")
                    break
                lines.append(line)
        except discord.HTTPException as exc:
            lines.append(f"(transcript truncated: {exc})")

        return "\n".join(lines).encode("utf-8")

    async def _deliver_transcript(
        self,
        channel: discord.TextChannel,
        ticket: Ticket,
        closer: discord.User | discord.Member,
        transcript: bytes,
    ) -> bool:
        """Send the transcript to the mod log, falling back to a DM."""
        opener = channel.guild.get_member(ticket.user_id)
        summary = base_embed(
            f"Ticket #{ticket.label} closed",
            color=discord.Color.dark_grey(),
            description=(
                f"**Opener:** {opener.mention if opener else f'`{ticket.user_id}`'}\n"
                f"**Closed by:** {closer.mention}\n"
                f"**Channel:** `{channel.name}`"
            ),
        )

        if await self.modlog.send(
            channel.guild,
            summary,
            file=discord.File(io.BytesIO(transcript), filename=f"ticket-{ticket.label}.txt"),
        ):
            return True

        try:
            await closer.send(
                embed=summary,
                file=discord.File(
                    io.BytesIO(transcript),
                    filename=f"ticket-{ticket.label}.txt",
                ),
            )
        except discord.HTTPException:
            log.warning("Could not DM the transcript to %s", closer.id, exc_info=True)
            return False
        return True


async def setup(bot: MonitorBot) -> None:
    await bot.add_cog(Tickets(bot))

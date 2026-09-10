"""User-info commands.

/userinfo [user] [ephemeral]  — full profile embed
/avatar [user]                — avatar with per-format download links
"User Info" context menu      — right-click a user → Apps → User Info
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from core.cog import MonitorCog
from core.constants import EMBED_FIELD_VALUE_MAX, truncate
from core.embeds import add_field, base_embed, info_embed
from core.responses import fail, reply

if TYPE_CHECKING:
    from core.bot import MonitorBot

log = logging.getLogger(__name__)

USERNAME_SEARCH_LIMIT = 5_000
ROLE_DISPLAY_LIMIT = 20
ASSET_SIZE = 4096

USER_MENTION_RE = re.compile(r"^<@!?(?P<id>\d{15,25})>$")

FLAG_LABELS: dict[str, tuple[str, str]] = {
    "staff": ("Discord Staff", "🛡️"),
    "partner": ("Partnered Server Owner", "🤝"),
    "hypesquad": ("HypeSquad Events", "🎉"),
    "bug_hunter": ("Bug Hunter (Lvl 1)", "🐛"),
    "bug_hunter_level_2": ("Bug Hunter (Lvl 2)", "🐞"),
    "hypesquad_bravery": ("HypeSquad Bravery", "💜"),
    "hypesquad_brilliance": ("HypeSquad Brilliance", "💗"),
    "hypesquad_balance": ("HypeSquad Balance", "💚"),
    "early_supporter": ("Early Supporter", "🥇"),
    "verified_bot_developer": ("Early Verified Bot Dev", "🤖"),
    "discord_certified_moderator": ("Certified Moderator", "🎓"),
    "active_developer": ("Active Developer", "🛠️"),
}

STATUS_EMOJI: dict[discord.Status, str] = {
    discord.Status.online: "🟢",
    discord.Status.idle: "🌙",
    discord.Status.dnd: "⛔",
    discord.Status.offline: "⚫",
    discord.Status.invisible: "⚫",
}

AnyUser = discord.User | discord.Member


def humanize_flags(user: AnyUser) -> str:
    flags = user.public_flags
    badges = [
        f"{emoji} {label}"
        for attribute, (label, emoji) in FLAG_LABELS.items()
        if getattr(flags, attribute, False)
    ]
    return "\n".join(badges) if badges else "—"


def format_activity(activity: discord.BaseActivity | discord.Spotify | None) -> str:
    if activity is None:
        return "—"
    if isinstance(activity, discord.Spotify):
        return f"🎵 Listening to **{activity.title}** by {', '.join(activity.artists)}"
    if isinstance(activity, discord.Game):
        return f"🎮 Playing **{activity.name}**"
    if isinstance(activity, discord.Streaming):
        return f"📺 Streaming **{activity.name}**"
    if isinstance(activity, discord.CustomActivity):
        emoji = f"{activity.emoji} " if activity.emoji else ""
        return f"💭 {emoji}{(activity.name or '').strip()}".strip() or "—"
    if isinstance(activity, discord.Activity):
        return f"📝 {activity.name}"
    return str(activity)


def timestamps(moment: object) -> str:
    """Render a datetime as absolute + relative Discord timestamps."""
    stamp = int(moment.timestamp())  # type: ignore[attr-defined]
    return f"<t:{stamp}:F>\n(<t:{stamp}:R>)"


async def fetch_full_user(bot: MonitorBot, user: AnyUser) -> discord.User | None:
    """Fetch the full user object, which alone carries banner/accent colour.

    Returns None when the fetch fails; the embed then simply omits those
    extras instead of the whole command failing.
    """
    try:
        return await bot.fetch_user(user.id)
    except discord.HTTPException:
        log.debug("Could not fetch full user %s", user.id, exc_info=True)
        return None


def build_user_embed(target: AnyUser, full_user: discord.User | None) -> discord.Embed:
    """Rich profile embed for a User or Member."""
    if isinstance(target, discord.Member) and target.color != discord.Color.default():
        color = target.color
    elif full_user is not None and full_user.accent_color is not None:
        color = full_user.accent_color
    else:
        color = discord.Color.blurple()

    embed = base_embed(str(target), color=color)
    embed.set_thumbnail(url=target.display_avatar.url)
    if full_user is not None and full_user.banner is not None:
        embed.set_image(url=full_user.banner.url)

    add_field(embed, "ID", f"`{target.id}`", inline=True)
    add_field(embed, "Username", f"@{target.name}", inline=True)
    if target.global_name and target.global_name != target.name:
        add_field(embed, "Display Name", target.global_name, inline=True)

    add_field(embed, "Account Created", timestamps(target.created_at), inline=True)

    if isinstance(target, discord.Member):
        _add_member_fields(embed, target)

    tags = []
    if target.bot:
        tags.append("🤖 Bot")
    if target.system:
        tags.append("⚙️ System")
    if tags:
        add_field(embed, "Account Type", " ".join(tags), inline=True)

    add_field(embed, "Badges", humanize_flags(target))
    embed.set_footer(text="TheMonitorBot")
    return embed


def _add_member_fields(embed: discord.Embed, member: discord.Member) -> None:
    if member.joined_at is not None:
        add_field(embed, "Joined Server", timestamps(member.joined_at), inline=True)
    if member.premium_since is not None:
        add_field(
            embed,
            "Boosting Since",
            f"<t:{int(member.premium_since.timestamp())}:R>",
            inline=True,
        )

    roles = [role for role in reversed(member.roles) if not role.is_default()]
    if roles:
        shown = " ".join(role.mention for role in roles[:ROLE_DISPLAY_LIMIT])
        if len(roles) > ROLE_DISPLAY_LIMIT:
            shown += f" *(+{len(roles) - ROLE_DISPLAY_LIMIT} more)*"
        add_field(embed, f"Roles ({len(roles)})", truncate(shown, EMBED_FIELD_VALUE_MAX))

    add_field(
        embed,
        "Status",
        f"{STATUS_EMOJI.get(member.status, '⚫')} {member.status.name.title()}",
        inline=True,
    )
    primary = next(
        (a for a in member.activities if not isinstance(a, discord.CustomActivity)),
        member.activity,
    )
    add_field(embed, "Activity", format_activity(primary), inline=True)

    if member.guild_permissions.administrator:
        add_field(embed, "Key Permission", "👑 Administrator", inline=True)


def build_asset_view(target: AnyUser, full_user: discord.User | None) -> discord.ui.View:
    """Link buttons to the raw avatar / server avatar / banner."""
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="Avatar",
            style=discord.ButtonStyle.link,
            url=target.display_avatar.url,
        )
    )
    if isinstance(target, discord.Member) and target.guild_avatar is not None:
        view.add_item(
            discord.ui.Button(
                label="Server Avatar",
                style=discord.ButtonStyle.link,
                url=target.guild_avatar.url,
            )
        )
    if full_user is not None and full_user.banner is not None:
        view.add_item(
            discord.ui.Button(
                label="Banner",
                style=discord.ButtonStyle.link,
                url=full_user.banner.url,
            )
        )
    return view


class UserInfo(MonitorCog):
    """User information commands."""

    def __init__(self, bot: MonitorBot) -> None:
        super().__init__(bot)
        self.ctx_menu = app_commands.ContextMenu(
            name="User Info",
            callback=self.user_info_context,
        )
        self.bot.tree.add_command(self.ctx_menu)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.ctx_menu.name, type=self.ctx_menu.type)

    async def _resolve_target(
        self,
        interaction: discord.Interaction,
        query: str | None,
    ) -> AnyUser | None:
        """Resolve a mention, numeric ID, or username to a User/Member.

        Order: no query -> the invoker; ``<@id>`` -> that ID; all digits ->
        that ID (member first, then a REST fetch); otherwise a name match
        against guild members.
        """
        if query is None:
            return interaction.user

        query = query.strip()
        if (match := USER_MENTION_RE.match(query)) is not None:
            query = match.group("id")

        if query.isdigit():
            return await self._resolve_id(interaction, int(query))

        wanted = query.lower().lstrip("@")
        if wanted and interaction.guild is not None:
            for index, member in enumerate(interaction.guild.members):
                if index >= USERNAME_SEARCH_LIMIT:
                    log.debug("Username search hit the scan limit in %s", interaction.guild.id)
                    break
                if wanted in {
                    member.name.lower(),
                    (member.global_name or "").lower(),
                    (member.nick or "").lower(),
                }:
                    return member
        return None

    async def _resolve_id(
        self,
        interaction: discord.Interaction,
        user_id: int,
    ) -> AnyUser | None:
        if interaction.guild is not None:
            member = interaction.guild.get_member(user_id)
            if member is not None:
                return member
        try:
            return await self.bot.fetch_user(user_id)
        except discord.NotFound:
            return None
        except discord.HTTPException:
            log.warning("fetch_user failed for %s", user_id, exc_info=True)
            return None

    @app_commands.command(
        name="userinfo",
        description="Show detailed info about a user (mention, ID, or username).",
    )
    @app_commands.describe(
        user="A mention, user ID, or username. Defaults to yourself.",
        ephemeral="If true, only you will see the response.",
    )
    @app_commands.checks.cooldown(3, 10.0, key=lambda i: i.user.id)
    async def userinfo(
        self,
        interaction: discord.Interaction,
        user: str | None = None,
        ephemeral: bool = False,
    ) -> None:
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)

        target = await self._resolve_target(interaction, user)
        if target is None:
            await fail(interaction, f"Could not find a user matching `{truncate(user or '', 80)}`.")
            return

        full_user = await fetch_full_user(self.bot, target)
        await reply(
            interaction,
            embed=build_user_embed(target, full_user),
            view=build_asset_view(target, full_user),
            ephemeral=ephemeral,
        )

    @app_commands.command(
        name="avatar",
        description="Show a user's avatar with download links for each format.",
    )
    @app_commands.describe(user="A mention, user ID, or username. Defaults to yourself.")
    @app_commands.checks.cooldown(3, 10.0, key=lambda i: i.user.id)
    async def avatar(
        self,
        interaction: discord.Interaction,
        user: str | None = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        target = await self._resolve_target(interaction, user)
        if target is None:
            await fail(interaction, f"Could not find a user matching `{truncate(user or '', 80)}`.")
            return

        embed = info_embed(f"{target}'s avatar")
        embed.set_image(url=target.display_avatar.url)

        view = discord.ui.View(timeout=None)
        asset = target.display_avatar
        for fmt in ("png", "webp"):
            view.add_item(
                discord.ui.Button(
                    label=fmt.upper(),
                    style=discord.ButtonStyle.link,
                    url=asset.replace(format=fmt, size=ASSET_SIZE).url,
                )
            )
        if asset.is_animated():
            view.add_item(
                discord.ui.Button(
                    label="GIF",
                    style=discord.ButtonStyle.link,
                    url=asset.replace(format="gif", size=ASSET_SIZE).url,
                )
            )

        await reply(interaction, embed=embed, view=view)

    async def user_info_context(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        full_user = await fetch_full_user(self.bot, member)
        await reply(
            interaction,
            embed=build_user_embed(member, full_user),
            view=build_asset_view(member, full_user),
            ephemeral=True,
        )


async def setup(bot: MonitorBot) -> None:
    await bot.add_cog(UserInfo(bot))

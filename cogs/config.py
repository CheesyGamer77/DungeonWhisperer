import discord
from discord.ext import commands
from cheesyutils.discord_bots import Context, DiscordBot, bot_owner_or_guild_moderator, is_guild_moderator, Embed
from cheesyutils.discord_bots.types import NameConvertibleEnum


class ConfigKey(NameConvertibleEnum):
    radio_text_channel_id = "radio_text_channel_id"
    radio_message_id = "radio_message_id"
    check_reactions = "check_reactions"
    notification_channel_id = "notification_channel_id"
    minimum_reaction_count = "minimum_reaction_count"

    def get_expected_type(self) -> type:
        if self is ConfigKey.radio_text_channel_id:
            return int
        elif self is ConfigKey.radio_message_id:
            return int
        elif self is ConfigKey.check_reactions:
            return bool
        elif self is ConfigKey.notification_channel_id:
            return int
        elif self is ConfigKey.minimum_reaction_count:
            return int

        raise ValueError(f"Can't get the expected type of {self}")


class Config(commands.Cog):
    """Commands for bot configuration"""

    def __init__(self, bot: DiscordBot):
        self.bot = bot
    
    @commands.guild_only()
    @bot_owner_or_guild_moderator()
    @commands.group(name="config", aliases=["configuration", "settings"])
    async def config_group(self, ctx: Context):
        if ctx.invoked_subcommand is None:
            await ctx.send_help(self.config_group)
    
    @commands.guild_only()
    @bot_owner_or_guild_moderator()
    @config_group.command(name="set")
    async def config_set_command(self, ctx: Context, key: ConfigKey, value: str):
        """
        Sets a particular configuration setting.

        You can get a list of valid settings by running the `config keys` command
        """

        # convert argument to expected type
        try:
            t = key.get_expected_type()
            value = t(value)

            await self.bot.database.execute(
                f"INSERT INTO config (server_id, {key.name}) VALUES (?, ?) ON CONFLICT (server_id) DO UPDATE SET {key.name} = ? WHERE server_id = ?",
                parameters=(ctx.guild.id, value, value, ctx.guild.id)
            )

            await ctx.reply_success(f"Set {key.name} to {value!r} ({type(value)})")
        except ValueError:
            # couldn't convert
            # TODO: This always puts "type" instead of the actual type it expects
            await ctx.reply_fail(f"{key.name} expects a valid `{t.__name__}`")

    @is_guild_moderator()
    @config_group.command(name="keys")
    async def config_keys_command(self, ctx: Context):
        """
        Returns a list of valid config keys
        """

        await ctx.send(
            embed=Embed(
                color=self.bot.color
            ).add_field(
                name="**Valid Config Keys**",
                value="\n".join([f"• `{key.name}` - `{key.get_expected_type().__name__}`" for key in sorted(ConfigKey, key = lambda k: k.name)])
            )
        )

    @is_guild_moderator()
    @config_group.command(name="get")
    async def config_get_command(self, ctx: Context, key: ConfigKey):
        """
        Returns the value set for a particular setting
        """

        row = await self.bot.database.query_first(f"SELECT {key.name} FROM config WHERE server_id = ?", parameters=(ctx.guild.id,))
        if row:
            row = dict(row)
            await ctx.send(f"`{key.name}` is currently set to `{row[key.name]}` ({key.get_expected_type().__name__})")
        else:
            await ctx.reply_fail("Missing configuration. Contact bot owner.")

def setup(bot: DiscordBot):
    bot.add_cog(Config(bot))

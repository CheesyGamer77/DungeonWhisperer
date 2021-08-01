import datetime
import discord
import logging
from discord.ext import commands
from cheesyutils.discord_bots import DiscordBot, Embed
from cheesyutils.discord_bots.bot import Context
from enum import Enum
from typing import List, Optional, Union


logger = logging.getLogger("reactions")
logger.setLevel(logging.DEBUG)
handler = logging.FileHandler(filename="DungeonWhisperer.log", encoding="utf-8", mode="a")
handler.setFormatter(logging.Formatter("%(asctime)s: [%(levelname)s]: (%(name)s)): %(message)s"))
logger.addHandler(handler)


class ReactionEmoji(Enum):
    PIN = "\U0001F4CC"
    DENIED = "\U0000274C"
    QUEUED = "\U0001F440"


class ReadOnlyConfigProxy:
    def __init__(self, d: dict):
        self.server_id: int = d["server_id"]
        self.check_reactions: bool = d["check_reactions"]
        self.notification_channel_id: int = d["notification_channel_id"]
        self.minimum_reaction_count: int = d["minimum_reaction_count"]


class CommunityPins(commands.Cog):
    """
    Commands and event listeners for community pinning stuffs
    """

    def __init__(self, bot: DiscordBot):
        self.bot = bot
    
    async def force_react(self, message: discord.Message, emoji: ReactionEmoji) -> bool:
        """Forces a reaction onto a message, assuming the bot has the proper permissions to do so

        This coroutine attempts to add the reaction first before "forcing" the reaction

        Forcing a reaction is when, if a message has too many reactions applied (20), the reaction with the lowest
        count that isn't used by the bot is cleared from the message.

        NOTE: This requires `add_reactions` permissions and, if the reaction needs to be forced, `manage_messages` as well

        Parameters
        ----------
        message : discord.Message
            The message to add the reaction to
        emoji : ReactionEmoji
            The emoji to add as a reaction to the `message`

        Returns
        -------
        `True` if the reaction was successfully added, `False` otherwise
        """

        logger.debug(f"Forcing reaction {str(emoji)!r} onto message {message.id} from guild {message.guild.id} in channel {message.channel.id}")
        reactions = message.reactions
        if len(reactions) == 20:
            # we're gonna have to force the reaction
            for i, reaction in enumerate(reactions):
                try:
                    # try to get a marker emoji and delete it from the
                    # emojis to consider to remove
                    ReactionEmoji(str(reaction))
                    del reactions[i]
                except ValueError:
                    # not a marker emoji, ignore
                    pass
            
                # now find the reaction with the smallest count
                minimum: discord.Reaction = reactions[0]
                for reaction in reactions:
                    if reaction.count < minimum.count:
                        minimum = reaction
                
                # try to clear smallest reaction
                try:
                    await minimum.clear()
                except discord.HTTPException:
                    # couldn't clear it
                    return False
        
        # add the reaction
        try:
            await message.add_reaction(emoji)
            return True
        except discord.HTTPException:
            return False
    
    async def fetch_guild_message_from_payload(
        self,
        payload: Union[discord.RawReactionActionEvent, discord.RawReactionClearEmojiEvent, discord.RawReactionClearEvent]
    ) -> Optional[discord.Message]:
        """Retrieves a guild message from an incoming raw reaction payload

        This returns None if the message does not have a guild associated with it

        Parameters
        ----------
        payload : Union[discord.RawReactionActionEvent, discord.RawReactionClearEmojiEvent, discord.RawReactionClearEvent]
            The incoming reaction payload to fetch the message from
        
        Returns
        -------
        The `discord.Message` guild message associated with the `payload`, `None` if the message has no guild
        
        """

        m = await self.bot.retrieve_message(channel_id=payload.channel_id, message_id=payload.message_id)
        return m if m.guild else None
    
    async def retrieve_config(self, guild: discord.Guild) -> Optional[ReadOnlyConfigProxy]:
        """Retrieves the config for a particular guild
        
        Parameters
        ----------
        guild : discord.Guild
            The guild to retrieve the config for
        
        Returns
        -------
        The `ReadOnlyConfigProxy` associated with `guild`, or `None` if not found
        """

        row = await self.bot.database.query_first("SELECT * FROM config WHERE server_id = ?", parameters=(guild.id,))
        return ReadOnlyConfigProxy(row) if row else row
    
    async def fetch_adjusted_reaction_count(self, message: discord.Message, reaction: discord.Reaction) -> int:
        """Fetches the "adjusted" count of a particular reaction in a message

        All this does is subtract one from the count if the message author used the reaction themselves

        Parameters
        ----------
        message : discord.Message
            The message to get the adjusted reaction count of
        reaction : discord.Reacion
            The reaction to get the adjusted count of
        
        Returns
        -------
        An integer corresponding to the adjusted reaction count as described above
        """

        count = reaction.count
        users: List[Union[discord.Member, discord.User]] = await reaction.users().flatten()
        ids: List[int] = [user.id for user in users]
        return count - 1 if message.author.id in ids else count
    
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id or not payload.guild_id:
            return

        if str(payload.emoji) == ReactionEmoji.PIN.value:
            message = await self.fetch_guild_message_from_payload(payload)
            config = await self.retrieve_config(message.guild)
            if config and config.check_reactions and config.notification_channel_id and config.minimum_reaction_count > 0:
                for reaction in message.reactions:
                    # check if the message has already been marked as queued for review
                    if str(reaction) == ReactionEmoji.QUEUED.value and reaction.me:
                        return
                
                for reaction in message.reactions:
                    if str(reaction) == ReactionEmoji.PIN.value:
                        count = await self.fetch_adjusted_reaction_count(message, reaction)

                        if count >= config.minimum_reaction_count:
                            channel = await self.bot.retrieve_channel(config.notification_channel_id)
                            if channel and isinstance(channel, discord.TextChannel):
                                marked = await self.force_react(message, ReactionEmoji.QUEUED.value)

                                if marked:
                                    await channel.send(
                                        message.jump_url,
                                        embed=Embed(
                                            title=":pushpin: Pin Request",
                                            description=f"Community members are requesting for a message to be pinned in {message.channel.mention}",
                                            color=self.bot.color,
                                            url=message.jump_url,
                                            timestamp=datetime.datetime.utcnow()
                                        ).add_fields(
                                            "Message Content",
                                            "Message Content (cont.)",
                                            message.content if message.content else "*No message content provided*"
                                        ).set_footer(
                                            text=f"Message ID: {message.id} | Author ID: {message.author.id}"
                                        )
                                    )


def setup(bot: DiscordBot):
    bot.add_cog(CommunityPins(bot))

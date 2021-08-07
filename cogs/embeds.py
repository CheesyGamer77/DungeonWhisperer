import discord
import logging
from dataclasses import dataclass, field
from discord import message
from discord.ext import commands
from cheesyutils.discord_bots import DiscordBot, Context, Embed
from cheesyutils.discord_bots.checks import is_guild_moderator
import json
from io import StringIO
from typing import Any, Generator, List, Optional


class Embeds(commands.Cog):
    """
    Commands for creating embeds
    """

    def __init__(self, bot: DiscordBot):
        self.bot = bot

        self.logger = logging.getLogger("embeds")
        self.logger.setLevel(logging.DEBUG)
        handler = logging.FileHandler(filename="DungeonWhisperer.log", encoding="utf-8", mode="a")
        handler.setFormatter(logging.Formatter("%(asctime)s: [%(levelname)s]: (%(name)s): %(message)s"))
        self.logger.addHandler(handler)

    def __remove_all_dict_keys_except(self, d: dict, key: Any) -> dict:
        # no, you cannot just iterate over each dict key and delete it on the fly
        # python gets very angry at you and raises a RuntimeError if you try to do that

        data = d
        to_remove = []
        for k in data.keys():
            if k != key:
                to_remove.append(k)
        
        # now we can actually delete the keys
        for k in to_remove:
            del data[k]

        return data

    def __clean_embed_dict(self, d: dict) -> dict:
        """Returns a "cleaned" embed dictionary object
        
        This needs to take place when we download embeds due to discord.py appending additional data
        in the embed's dictionary

        Parameters
        ----------
        d : dict
            The embed dictionary from `discord.Embed.to_dict()`
        
        Returns
        -------
        The given embed dict, without the garbage stuff
        """

        if d.get("thumbnail"):
            d["thumbnail"] = self.__remove_all_dict_keys_except(d["thumbnail"], "url")

        d.pop("type")

        return d

    @commands.guild_only()
    @is_guild_moderator()
    @commands.command(name="copy")
    async def copy_command(self, ctx: Context, source_message: discord.Message, out_channel: Optional[discord.TextChannel]):
        """
        Copy's a message and sends it in another channel
        """

        if not out_channel:
            out_channel = ctx.channel

        for i, embed in enumerate(source_message.embeds):
            await out_channel.send(
                source_message.content if i == 0 else None,
                embed=embed
            )

        await ctx.reply_success(f"Embed(s) posted in {out_channel.mention}")

    @commands.guild_only()
    @is_guild_moderator()
    @commands.command(name="upload")
    async def upload_command(
        self,
        ctx: Context,
        text_channel: Optional[discord.TextChannel]
    ):
        """
        Sends an embed into a particular channel, given an attached JSON file
        """

        if not text_channel:
            text_channel = ctx.channel

        msg: discord.Message = ctx.message
        if msg.attachments and msg.attachments[0].filename.endswith(".json"):
            # try to read the json file
            document = msg.attachments[0]

            try:
                data: bytes = await document.read()
                data: dict = json.loads(data.decode("utf-8"))
            except discord.HTTPException as e:
                await ctx.reply_fail(f"Couldn't read attachment data: {e.__class__.__name__}")
            except json.JSONDecodeError as e:
                await ctx.reply_fail(f"Failed to decode JSON in attachment at line {e.lineno}")
            else:
                # check which syntax is being used
                keys = data.keys()
                if "version" in keys and "backups" in keys and isinstance(data["backups"], list):
                    # this is probably discohook's alternative syntax
                    self.logger.debug(f"Using discohook backup syntax for upload command message {ctx.message.jump_url}")

                    # NOTE: We only look at the first backup
                    for message in data["backups"][0]["messages"]:
                        message = message["data"]
                        for i, embed_json in enumerate(message["embeds"]):
                            embed_json["type"] = "rich"
                            await text_channel.send(
                                message["content"] if i == 0 else None,
                                embed=Embed.from_dict(embed_json)
                            )
                    
                    await ctx.reply_success(f"Embed(s) sent in {text_channel.mention}")

                elif "content" in keys and "embeds" in keys and isinstance(data["embeds"], list):
                    # this is most likely the standard format discord expects
                    self.logger.debug(f"Using standard syntax for upload command message {ctx.message.jump_url}")
                    
                    for i, embed_json in enumerate(data["embeds"]):
                        embed_json["type"] = "rich"

                        await text_channel.send(
                            data["content"] if i == 0 else None,
                            embed=Embed.from_dict(embed_json)
                        )
                    
                    await ctx.reply_success(f"Embed(s) sent in {text_channel.mention}")
                else:
                    self.logger.error(f"Undefined schema for upload command message {ctx.message.jump_url} with root keys {keys}")
                    await ctx.reply_fail("Invalid JSON schema provided")
        else:
            await ctx.reply_fail("Missing JSON file attachment to post")
    
    @commands.guild_only()
    @is_guild_moderator()
    @commands.command(name="download")
    async def download_command(self, ctx: Context, message: discord.Message):
        """
        Generates a JSON file given a link to a message containing embeds

        This *does not* include message attachments
        """

        data = {
            "content": message.content if message.content != "" else None,
            "embeds": None
        }

        for embed in message.embeds:
            if data["embeds"] is None:
                data["embeds"] = []

            # we need to do some cleanup, since discord.py
            # includes additional information in the embed dict
            embed_data = self.__clean_embed_dict(embed.to_dict())

            data["embeds"].append(embed_data)
        
        await ctx.send(
            file=discord.File(
                StringIO(json.dumps(data, indent=4)),
                filename="message_json.json"
            )
        )
        



def setup(bot: DiscordBot):
    bot.add_cog(Embeds(bot))

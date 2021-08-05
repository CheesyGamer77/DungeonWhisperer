import asyncio
import csv
import discord
import datetime
import json
import logging
import random
import spotipy
import os
from cheesyutils.discord_bots import DiscordBot, Context, Embed
from cheesyutils.discord_bots.checks import is_guild_moderator, bot_owner_or_guild_moderator
from dataclasses import dataclass
from discord.ext import commands
from io import StringIO
from spotipy.oauth2 import SpotifyClientCredentials
from typing import List, Optional


@dataclass
class MusicTrackProxy:
    title: str
    artists: List[str]
    island_realm: str
    duration: str
    source: discord.FFmpegPCMAudio
    track_url: str
    album_url: str
    thumbnail_url: str
    color: discord.Color

    @property
    def embed(self) -> Embed:
        # find the urls for each track
        urls = {
            "Spotify": {
                "emoji": "<:Spotify:869112865615937576>",
                "url": self.track_url
            }
        }

        return Embed(
            color=self.color
        ).set_thumbnail(
            url=self.thumbnail_url
        ).set_author(
            name="Now Playing ♪"
        ).add_field(
            name=f"**{self.title} - {self.island_realm}**",
            value=f"Artists: {', '.join([artist for artist in self.artists])}"
        ).add_field(
            name="Track URLS",
            value="\n".join(['• {} [{}]({})'.format(item["emoji"], key, item['url']) for key, item in urls.items()])
        ).set_footer(
            text=f"Length: {self.duration}"
        )


def bot_has_voice_state(*, connected: bool = True, playing: bool = None, paused: bool = None):
    """
    A check that determines if the bot has a particular voice state

    Parameters
    ----------
    connected : bool
        Whether the bot should be connected to a voice channel.
        This defaults to `True`.
    playing : bool
        Whether the bot should be currently playing audio.
        This defaults to `None` (indifferent).
    paused : bool
        Whether the bot's playback should be paused.
        This defaults to `None` (indifferent)
    """

    async def predicate(ctx: Context):
        client: discord.VoiceClient = ctx.guild.voice_client
        return client is not None
    
    return commands.check(predicate)


class Music(commands.Cog):
    """
    Event listeners and commands for music
    """

    def __init__(self, bot: DiscordBot):
        self.bot = bot

        self.logger = logging.getLogger("music")
        self.logger.setLevel(logging.DEBUG)
        handler = logging.FileHandler(filename="DungeonWhisperer.log", encoding="utf-8", mode="a")
        handler.setFormatter(logging.Formatter("%(asctime)s: [%(levelname)s]: (%(name)s): %(message)s"))
        self.logger.addHandler(handler)

    def _get_spotify_credentials_manager(self, fp: str="spotify_credentials.json") -> SpotifyClientCredentials:
        data = json.load(open(fp, "r"))
        return SpotifyClientCredentials(
            client_id=data["client_id"],
            client_secret=data["client_secret"]
        )

    def get_spotify_album(self, album_url: str) -> dict:
        client = spotipy.Spotify(client_credentials_manager=self._get_spotify_credentials_manager())
        return client.album(album_url)

    async def retrieve_radio_text_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        row = await self.bot.database.query_first(
            "SELECT radio_text_channel_id FROM config WHERE server_id = ?",
            parameters=(guild.id,)
        )

        if row:
            channel_id: Optional[int] = row["radio_text_channel_id"]
            if channel_id:
                channel = await self.bot.retrieve_channel(channel_id)
                if isinstance(channel, discord.TextChannel):
                    self.logger.debug(f"Fetched radio text channel for guild {guild.id}: {channel.id} ({channel.name})")
                    return channel
        
        return None
    
    async def retrieve_radio_message(self, guild: discord.Guild) -> Optional[discord.VoiceChannel]:
        # we first need to retrieve the radio text channel

        text_channel = await self.retrieve_radio_text_channel(guild)
        if text_channel:
            row = await self.bot.database.query_first(
                "SELECT radio_message_id FROM config WHERE server_id = ?",
                parameters=(guild.id,)
            )

            if row:
                message_id = row["radio_message_id"]

                if message_id:
                    message = await self.bot.retrieve_message(text_channel.id, message_id)
                    if message:
                        self.logger.debug(f"Fetched radio message for guild {guild.id}: {message.jump_url}")
                        return message
        
        return None

    @property
    def default_base_embed(self) -> Embed:
        return Embed(
            color=discord.Color.from_rgb(162, 162, 162)
        ).set_thumbnail(
            url="https://cdn.discordapp.com/attachments/728166911686344755/866689668434100264/Not_Playing.png"
        )

    
    @bot_owner_or_guild_moderator()
    @commands.command(name="ping")
    async def ping_command(self, ctx: Context):
        """
        Pong! Returns the websocket, API, and voice client latency
        """

        now = datetime.datetime.now()
        m: discord.Message = await ctx.send("Pinging...")

        embed = Embed(
            title=":ping_pong: Pong!",
            color=self.bot.color
        ).add_field(
            name="Websocket",
            value=round(self.bot.latency * 1000, 2)
        ).add_field(
            name="API",
            value=round((datetime.datetime.now() - now).microseconds / 1000, 2)
        )

        voice_client = ctx.guild.voice_client
        if voice_client and isinstance(voice_client, discord.VoiceClient):
            embed.add_field(
                name="Voice",
                value=round(voice_client.latency * 1000, 2)
            )
        
        await m.edit(content="", embed=embed)

    @is_guild_moderator()
    @commands.group(name="spotify")
    async def spotify_group(self, ctx: Context):
        """
        Commands for interacting with spotify
        """

        if ctx.invoked_subcommand is None:
            await ctx.send_help(self.spotify_group)

    @is_guild_moderator()
    @spotify_group.command(name="album")
    async def album_command(self, ctx: Context, album_url: str):
        """
        Returns a JSON file representation of a particular spotify album
        """

        client = spotipy.Spotify(client_credentials_manager=self._get_spotify_credentials_manager())
        await ctx.send(file=discord.File(StringIO(json.dumps(client.album(album_url), indent=4)), filename="album.json"))

    def get_duration_string(self, ms: int):
        return "Not set"

    def get_track_audio_source(self, root: str, name: str) -> discord.FFmpegPCMAudio:
        """Gets a track's audio source given a root directory to search through

        Parameters
        ----------
        root : str
            The path to the root directory to search through
        name : str
            The name of the track to search for

        Raises
        ------
        `ValueError` if the track was not found

        Returns
        -------
        A `discord.FFmpegPCMAudio` object associated with the track
        """
        
        self.logger.debug("Searching in %s for track named %s", root, name)
        for item in os.listdir(root):
            # audio tracks typically have a track number prepended to the file name
            # this is formatted as `xx_...`
            # this also strips out the `.mp3` file extension
            item_name = item[3:][:-4]
            # self.logger.debug(f"Found item named {item_name!r}")

            if item_name == name:
                return discord.FFmpegPCMAudio(f"{root}/{item}", before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5')
        
        self.logger.error(f"No track found in {root} named {name!r}")
        raise ValueError(f"Track {name!r} not found")

    def get_next_track(self, root: str="albums") -> MusicTrackProxy:
        """Randomly retrieves the next track to play.
        
        `root` should be the path to a directory containing only subdirectories which contain tracks to play.

        Parameters
        ----------
        root : str
            The root directory to choose the track from.
            This defaults to `"albums"`
        """

        index: list = json.load(open(f"{root}/index.json", "r"))
        album_index: dict = random.choice(index)
        path: str = album_index["path"]
        island_realm = album_index["name"]
        color = discord.Color.from_rgb(*album_index["color"])

        album: dict = json.load(open(f"{root}/{path}/album.json", "r"))

        track: dict = random.choice(album["tracks"]["items"])
        track_name = track["name"]
        self.logger.debug("Randomly chose track %s from path %s", f"\"{track_name}\"", f"{root}/{path}")

        return MusicTrackProxy(
            title=track["name"],
            artists=[artist["name"] for artist in track["artists"]],
            island_realm=island_realm,
            duration=self.get_duration_string(track["duration_ms"]),
            source=self.get_track_audio_source(f"{root}/{path}", track_name),
            track_url=track["external_urls"]["spotify"],
            album_url=album["external_urls"]["spotify"],
            thumbnail_url=album["images"][0]["url"],
            color=color
        )

    async def modify_radio_message(self, ctx: Context, *, embed: Embed):
        """Modifies the radio message for a guild
        
        If a radio message is not set, the bot attempts to find and set one if a radio text channel is set.
        If there is no radio text channel set, this will do nothing.

        Parameters
        ----------
        ctx : Context
            The invokation context from tehe guild/channel you wish to modify the radio channel of
        embed : Embed
            The embed to put as the modified radio message
        """
        
        message = await self.retrieve_radio_message(ctx.guild)
        if message:
            # easy, just edit the message
            try:
                await message.edit(embed=embed)
            except discord.Forbidden:
                self.logger.error(f"Failed to modify radio message {message.jump_url} - Insufficient permissions")
        else:
            # try to get a radio channel
            channel = await self.retrieve_radio_text_channel(ctx.guild)
            if not channel:
                channel = ctx.channel
            
            # set a new radio message
            try:
                message: discord.Message = await channel.send(embed=embed)

                await self.bot.database.execute(
                    "INSERT INTO config (server_id, radio_message_id) VALUES (?, ?) ON CONFLICT (server_id) DO UPDATE SET radio_message_id = ? WHERE server_id = ?",
                    parameters=(ctx.guild.id, message.id, message.id, ctx.guild.id)
                )

                self.logger.info(f"Set missing radio_message_id to message {message.id} ({message.jump_url})")
            except discord.Forbidden:
                self.logger.error(f"Attempt to set new radio message failed for guild {ctx.guild.id}, channel {ctx.channel.id}")

    def _play_track(self, ctx: Context, voice_client: discord.VoiceClient, track: MusicTrackProxy):
        self.logger.debug(f"Playing track {track.title!r} in guild {ctx.guild.id}, channel {voice_client.channel.id}")

        def after_playing(error: Exception):            
            voice_client = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
            if error:
                self.logger.exception(f"Error while playing track {track.title!r} in guild {ctx.guild.id}: {error.__class__.__name__}. Skipping track.")
                self._play_track(ctx, voice_client, self.get_next_track)
                
            if voice_client:
                self._play_track(ctx, voice_client, self.get_next_track())
            else:
                self.logger.info(f"Missing voice client for guild {ctx.guild.id}")
        
        # edit the embed and start playing
        asyncio.run_coroutine_threadsafe(self.modify_radio_message(ctx, embed=track.embed), loop=self.bot.loop)
        voice_client.play(discord.PCMVolumeTransformer(track.source), after=after_playing)

    @is_guild_moderator()
    @commands.command(name="play", aliases=["p"])
    async def play_command(self, ctx: Context, voice_channel: Optional[discord.VoiceChannel]):
        """
        Starts playing music from the Minecraft Dungeons soundtrack
        """
    
        if not voice_channel:
            voice_channel = ctx.author.voice.channel

        voice_client: Optional[discord.VoiceClient] = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
        if not voice_client:
            voice_client = await voice_channel.connect()

        if voice_client and not voice_client.is_playing():
            self._play_track(ctx, voice_client, self.get_next_track())

    @is_guild_moderator()
    @commands.command(name="stop")
    async def stop_command(self, ctx: Context, *, reason: Optional[str]):
        """
        Stops playing music and leaves the voice channel, with an optional reason

        The bot's radio message will be edited to the default "Nothing Playing" embed
        """

        voice_client: discord.VoiceClient = ctx.guild.voice_client
        if voice_client:
            # set default embed
            embed = self.default_base_embed
            embed.add_field(
                name="**Nothing Currently Playing**",
                value=reason if reason else "Check below to see if there is any news on why the bot is not playing music"
            )

            await voice_client.disconnect()
            await self.modify_radio_message(ctx, embed=embed)
        


def setup(bot: DiscordBot):
    bot.add_cog(Music(bot))

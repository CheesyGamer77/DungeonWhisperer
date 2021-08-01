import discord
import datetime
import json
import logging
import random
from discord import guild
import spotipy
import os
from cheesyutils.discord_bots import DiscordBot, Context, Embed
from cheesyutils.discord_bots.checks import is_guild_moderator
from dataclasses import dataclass, field
from discord.ext import commands
from io import StringIO
from spotipy.oauth2 import SpotifyClientCredentials
from typing import Optional, Union, List


logger = logging.getLogger("music")
logger.setLevel(logging.DEBUG)
handler = logging.FileHandler(filename="DungeonWhisperer.log", encoding="utf-8", mode="a")
handler.setFormatter(logging.Formatter("%(asctime)s: [%(levelname)s]: (%(name)s)): %(message)s"))
logger.addHandler(handler)


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
        return Embed(
            title=self.title,
            color=self.color,
            url=self.track_url,
        ).set_thumbnail(
            url=self.thumbnail_url
        ).set_author(
            name="Now Playing ♪"
        )


class NoVoiceChannel(commands.CommandError):
    """
    Raised when the bot requires a music channel to use a particular command
    """

    pass


class AlreadyInVoiceChannel(commands.CommandError):
    """
    Raised when the bot is already in a voice channel
    """

    pass


class Music(commands.Cog):
    """
    Event listeners and commands for music
    """

    def __init__(self, bot: DiscordBot):
        self.bot = bot

    async def cog_check(self, ctx: Context):
        return super().cog_check(ctx)

    def _get_spotify_credentials_manager(self, fp: str="spotify_credentials.json") -> SpotifyClientCredentials:
        data = json.load(open(fp, "r"))
        return SpotifyClientCredentials(
            client_id=data["client_id"],
            client_secret=data["client_secret"]
        )

    def get_spotify_album(self, album_url: str) -> dict:
        client = spotipy.Spotify(client_credentials_manager=self._get_spotify_credentials_manager())
        return client.album(album_url)

    @property
    def default_embed(self) -> Embed:
        return Embed(
            title="Nothing Currently Playing",
            description="Check below to see if there is any news on why the bot is not playing music",
            color=discord.Color.from_rgb(48, 136, 214)
        )

    
    @is_guild_moderator()
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

        voice_client = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
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
        logger.debug("Searching in %s for track named %s", root, name)
        for item in os.listdir(root):
            # audio tracks typically have a track number prepended to the file name
            # this is formatted as `xx_...`
            # this also strips out the `.mp3` file extension
            item_name = item[3:][:-4]
            logger.debug(f"Found item named {item_name!r}")

            if item_name == name:
                return discord.FFmpegPCMAudio(f"{root}/{item}")
        
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
        logger.debug("Randomly chose track %s from path %s", f"\"{track_name}\"", f"{root}/{path}")

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

    def on_track_end(self, error: Optional[Exception]):
        return 0

    @is_guild_moderator()
    @commands.command(name="play", aliases=["p"])
    async def play_command(self, ctx: Context):
        """
        Starts playing music from the Minecraft Dungeons soundtrack
        """
    
        voice_client: Optional[discord.VoiceClient] = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
        if voice_client and voice_client.is_connected() and not voice_client.is_playing():
            track = self.get_next_track()
            await ctx.send(embed=track.embed)
            await voice_client.play(discord.PCMVolumeTransformer(track.source), after=self.on_track_end)
            await ctx.send("P")

    @is_guild_moderator()
    @commands.command(name="stop")
    async def stop_command(self, ctx: Context):
        """
        Stops playing music and leaves the voice channel
        """

        pass

    @play_command.before_invoke
    async def ensure_voice_state(self, ctx: Context):
        if not ctx.author.voice or not ctx.author.voice.channel:
            raise NoVoiceChannel
        
        if discord.utils.get(self.bot.voice_clients, guild=ctx.guild):
            raise AlreadyInVoiceChannel

def setup(bot: DiscordBot):
    bot.add_cog(Music(bot))

import asyncio
import csv
import discord
import datetime
import json
import logging
import random
import spotipy
import os
from cheesyutils.discord_bots import DiscordBot, Context, Embed, is_guild_moderator, bot_owner_or_guild_moderator
from dataclasses import dataclass
from discord.ext import commands
from io import StringIO
from spotipy.oauth2 import SpotifyClientCredentials
from typing import Dict, List, Optional


@dataclass
class MusicTrackProxy:
    """A dataclass that assists with getting data for music tracks

    Attributes
    ----------
    title : str
        The name of the track
    artists : List[str]
        The list of artist names of the track
    island_realm : str
        The island realm where the track originates from
    duration : str
        The duration string for how long a track is
    source : discord.FFmpegPCMAudio
        The Discord PCM audio source to use to play the track
    track_url : str
        The spotify url for the track
    album_url : str
        The spotify album url for the track
    thumbnail_url : str
        The url of the thumbnail to use for the embed
    color : discord.Color
        The color to use for the track's embed
    """
    
    title: str
    artists: List[str]
    island_realm: str
    duration: str
    source: discord.FFmpegPCMAudio
    track_url: str
    album_url: str
    thumbnail_url: str
    color: discord.Color
    logger: logging.Logger = logging.getLogger("music")

    @property
    def embed(self) -> Embed:
        track_number = "?"

        urls = {
            "Spotify": {
                "emoji": "<:Spotify:869112865615937576>",
                "track_url": self.track_url,
                "album_url": self.album_url
            }
        }

        # find the urls for each track
        with open(f"albums/{self.island_realm}/urls.csv", "r") as csv_file:
            reader = csv.DictReader(csv_file)

            # set track urls
            track_urls = discord.utils.find(lambda d: d["TrackName"] == self.title, reader)

            # this is weird as fuck
            # there is a very rare chance for reader to return empty rows, and as a result causes an index error below
            # to prevent the bot from stopping audio playback, if this error occurs, the error will be ignored,
            # however there will be only spotify track/album urls displayed as well as no track number being displayed
            # TODO: Please fix this absolute dumpsterfire
            try:
                album_urls = list(reader)[0]
            except IndexError:
                album_urls = None
                self.logger.warn(f"IndexError while playing track {self.title!r}. Reader rows: {[str(row) for row in reader]}")

            if track_urls is not None and album_urls is not None:
                track_number = track_urls["Track"]
                urls["Amazon Music"] = {
                    "emoji": "<:Amazon_Music:869112865532030996>",
                    "track_url": track_urls["Amazon Music"],
                    "album_url": album_urls["Amazon Music"]
                }

                urls["Apple Music"] = {
                    "emoji": "<:Apple_Music:869112865599131669>",
                    "track_url": track_urls["Apple Music"],
                    "album_url": album_urls["Apple Music"]
                }

                urls["Deezer"] = {
                    "emoji": "<:Deezer:869112866022780948>",
                    "track_url": track_urls["Deezer"],
                    "album_url": album_urls["Deezer"]
                }

                urls["YouTube Music"] = {
                    "emoji": "<:YouTube_Music:869112866282807356>",
                    "track_url": track_urls["YouTube Music"],
                    "album_url": album_urls["YouTube Music"]
                }

        return Embed(
            color=self.color
        ).set_thumbnail(
            url=self.thumbnail_url
        ).set_author(
            name="♪ Now Playing"
        ).add_field(
            name=f"**{self.title}**",
            value=f"By {', '.join([artist for artist in self.artists])} | Duration: {self.duration}\nTrack {track_number} of the {self.island_realm} album",
            inline=False
        ).add_field(
            name="**Music Links**",
            value="\n".join([f"{item['emoji']} [Track {track_number}]({item['track_url']}) **|** [Album]({item['album_url']})" for key, item in urls.items()])
        )


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

        # TEMPORARY - READ
        # This bot has always had issues with it randomly disconnecting from voice for no apparent
        # reason, and frankly I'm sick of this game of cat and mouse when my error logging should be
        # catching exactly what's going on, but it isn't, and I don't know why.
        # I'm pretty sure it's an FFMPEG issue, but until it's fixed, we have this.
        # This list simply stores a list of guild IDs where a "proper" disconnect via the `stop` command
        # is used, with such entries being near instantaniously removed
        self.proper_disconnects: List[int] = []

    def _get_spotify_credentials_manager(self, fp: str="spotify_credentials.json") -> SpotifyClientCredentials:
        """Gets a `SpotifyClientCredentials` object from a given JSON file containing Spotifty credentials

        The JSON file is expected to contain the following keys:
        - `client_id`
        - `client_secret`

        Parameters
        ----------
        fp : str
            The path-like string designating the JSON file to read and extract Spotify credentials from
        
        Returns
        -------
        A `SpotifyClientCredentials` object with the respective credentials
        """
        
        data = json.load(open(fp, "r"))
        return SpotifyClientCredentials(
            client_id=data["client_id"],
            client_secret=data["client_secret"]
        )

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Ran when a member updates their voice state
        
        This is part of a temporary fix for issues regarding the bot leaving voice unexpectedly due to an error while playing music.
        All this does is check if the bot was *supposed* to leave voice, otherwise cleanup any leftover voice data and reconnect
        """

        if member.id != self.bot.user.id:
            return

        if before.channel and not after.channel:
            guild: discord.Guild = before.channel.guild

            self.logger.info(f"DungeonWhisperer disconnected from voice channel {before.channel.id}, guild {guild.id}")
            

            if guild.id not in self.proper_disconnects and isinstance(before.channel, discord.VoiceChannel):
                # NOTE: THIS WILL BREAK WHEN WE MOVE TO MAKING THIS UTILIZE A STAGE CHANNEL
                self.logger.warn(f"Improper disconnect for guild {guild.id}. Attempting to re-establish voice connection...")

                try:
                    voice = await before.channel.connect()
                except discord.HTTPException as err:
                    # :shrug:
                    self.logger.exception(f"Failed to re-establish voice connection for guild {guild.id}: {err}")
                else:
                    # because of how self._play_track() works, we have to pass an invokation context into
                    # the method, which is normally called from within an actual command
                    # instead, we'll fetch the context based from the guild's radio message, if able
                    # if there's no radio message then I guess sucks to be that guild :shrug:
                    # TODO: Fix this nonsense
                    message: Optional[discord.Message] = await self.retrieve_radio_message(guild)
                    if message:
                        ctx = await self.bot.get_context(message)

                        # THIS DOES NOT CONTAIN ERROR CHECKING!!!
                        self._play_track(ctx, voice, self.get_next_track())
            else:
                self.proper_disconnects.remove(guild.id)

    def get_spotify_album(self, album_url: str) -> dict:
        """Returns a `dict` representing raw album data from Spotify

        NOTE: Spotify paginates album tracks for albums with more than 50 tracks. If an album's tracks are paginated, the `tracks`
        list will contain a `next` key, which contains a url that you can pass into your `Spotify`'s `next` method in order to get
        the rest of the tracks.

        TODO: In the future, for our use case we could just retrieve paginated results automatically

        Parameters
        ----------
        album_url : str
            The url for the album to retrieve the data of
        """
        
        client = spotipy.Spotify(client_credentials_manager=self._get_spotify_credentials_manager())
        return client.album(album_url)

    async def retrieve_radio_text_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        """Retrieves the radio text channel for a particular guild or returns
        `None` if the channel isn't found/set

        Parameters
        ----------
        guild : discord.Guild
            The guild to retrieve the channel for

        Returns
        -------
        A `discord.TextChannel` if the channel was found, else `None`
        """
        
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
    
    async def retrieve_radio_message(self, guild: discord.Guild) -> Optional[discord.Message]:
        """Retrieves the radio message for a particular guild
        or returns `None` if the radio message isn't found

        TODO: Implement caching for the messages

        Parameters
        ----------
        guild : discord.Guild
            The guild to retrieve the radio message for

        Returns
        -------
        A `discord.Message` for the radio message if it exists, else `None`
        """

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
        """Returns the default "Now Playing" embed

        Returns
        -------
        The default `Embed`
        """
        
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

        def __remove_available_market_data(d: dict) -> dict:
            """Removes the "available_markets" lists from album JSON data
            
            These lists are so obnoxiously large and annoying, thats why this exists

            Parameters
            ----------
            d : dict
                The raw JSON data returned from spotify
            
            Returns
            -------
            The modified `dict` object, without the "available_markets" lists
            """

            if d.get("available_markets"):
                del d["available_markets"]

            if d.get("tracks"):
                for i in range(len(d["tracks"]["items"])):
                    del d["tracks"]["items"][i]["available_markets"]
            else:
                for i in range(len(d["items"])):
                    del d["items"][i]["available_markets"]

            return d

        client = spotipy.Spotify(client_credentials_manager=self._get_spotify_credentials_manager())
        
        # Spotify pages album requests after 50 tracks, so make sure to include the second page
        # NOTE: This does *not* account for more than two potential pages, cause lets be honest:
        # if a game DLC soundtrack has more than 100 tracks, chances are half of it is
        # not important

        data: dict = client.album(album_url)
        data = __remove_available_market_data(data)

        next_page = data["tracks"].get("next", False)

        if next_page:
            # get the second page of tracks
            data_2 = __remove_available_market_data(client.next(data["tracks"]))
            data["tracks"]["items"] = data["tracks"]["items"] + data_2["items"]
        
        await ctx.send(file=discord.File(StringIO(json.dumps(data, indent=4)), filename="album.json"))
        
    @staticmethod
    def get_duration_string(ms: int) -> str:
        """Returns a string converting miliseconds to a human readable
        duration string

        Parameters
        ----------
        ms : int
            The ammount of miliseconds to convert
        
        Returns
        -------
        A `str` for the duration string
        """
        
        minutes = int((ms / 1000) / 60)
        seconds = int((ms / 1000) % 60)

        seconds = f"0{seconds}" if seconds < 10 else seconds

        return f"{minutes}:{seconds}"

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
            # audio tracks typically have a zero-padded track number prepended to the file name
            # this is formatted as `xx_...`
            # this strips out the track number and the `.mp3` file extension
            item_name = item[3:][:-4]
            # self.logger.debug(f"Found item named {item_name!r}")

            if item_name == name:
                return discord.FFmpegPCMAudio(f"{root}/{item}")
        
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
        """Main function to play music tracks in a particular guild

        TODO: This method has always been super finicky. Oftentimes the bot disconnects from VC on it's own,
        and the error logging for such issues is.. subpar

        Parameters
        ----------
        ctx : Context
            The original play command invokation context
        voice_client : discord.VoiceClient
            The voice client to use for playing music
        track : MusicTrackProxy
            The dataclass containing the data for the track to play
        """

        self.logger.debug(f"Playing track {track.title!r} in guild {ctx.guild.id}, channel {voice_client.channel.id}")

        def after_playing(error: Optional[Exception]):
            voice_client = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
            
            self.logger.debug(f"Checking error after playing: {error}")
            if error:
                raise error
                
            if voice_client:
                self._play_track(ctx, voice_client, self.get_next_track())
            else:
                self.logger.warning(f"Missing voice client for guild {ctx.guild.id}")
        
        # edit the embed and start playing
        try:
            asyncio.run_coroutine_threadsafe(self.modify_radio_message(ctx, embed=track.embed), loop=self.bot.loop)
            voice_client.play(discord.PCMVolumeTransformer(track.source), after=after_playing)
        except Exception as err:
            self.logger.exception(f"Error occured while playing track {track.title!r} in guild {ctx.guild.id}: {err.__class__.__name__}. Attempting to skip to next track...")
            self._play_track(ctx, voice_client, self.get_next_track())

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
            try:
                self._play_track(ctx, voice_client, self.get_next_track())
            except Exception as err:
                self.logger.exception(f"Error occured in play command in guild {ctx.guild.id}: {err.__class__.__name__}.")

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

            self.proper_disconnects.append(ctx.guild.id)
            await voice_client.disconnect()
            await self.modify_radio_message(ctx, embed=embed)
        

def setup(bot: DiscordBot):
    bot.add_cog(Music(bot))

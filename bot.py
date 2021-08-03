from cheesyutils.discord_bots import DiscordBot
from cheesyutils.discord_bots.help_command import ModOnlyHelpCommand
from cheesyutils.discord_bots.utils import get_discord_color
from dislash import SlashClient

COLOR = get_discord_color("#1fcdff")

bot = DiscordBot(";", members_intent=True, color=COLOR, status="idle", activity="in the Dungeon Depths", database="DungeonWhisperer.db", help_command=ModOnlyHelpCommand(COLOR))
SlashClient(bot)

bot.load_extension("cogs.community_pins")
bot.load_extension("cogs.music")
bot.load_extension("cogs.embeds")
bot.load_extension("cogs.components")

bot.run("token.txt")
from cheesyutils.discord_bots import DiscordBot
from dislash import SlashClient

bot = DiscordBot("$", members_intent=True, color="#1fcdff", status="idle", activity="in the Dungeon Depths", database="DungeonWhisperer.db")
SlashClient(bot)

bot.load_extension("cogs.community_pins")
bot.load_extension("cogs.music")
bot.load_extension("cogs.embeds")
bot.load_extension("cogs.components")

bot.run("token.txt")
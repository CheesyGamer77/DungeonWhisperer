import base64
from dislash import ActionRow, Button, Component, MessageInteraction, SelectMenu, SelectOption
import discord
import io
import json
import logging
from cheesyutils.discord_bots import DiscordBot, Context, Embed, is_guild_moderator, PromptTimedout
from cheesyutils.discord_bots.types import NameConvertibleEnum
from discord.ext import commands
from enum import Enum
from typing import Any, Callable, Dict, Generator, List, Optional, Union
from .actions import ActionEnvironment


class ComponentType(Enum):
    # yes, dislash has a class for this, but the author
    # doesn't seem to know that enums are a thing, and because
    # I hate working with raw integers designating types, I made
    # this as a replacement
    ActionRow = 1
    Button = 2
    SelectMenu = 3


class ButtonType(NameConvertibleEnum):
    primary = 1
    secondary = 2
    success = 3
    danger = 4
    link = 5


class Components(commands.Cog):
    """
    Commands for testing components
    """

    def __init__(self, bot: DiscordBot):
        self.bot = bot

        self.logger = logging.getLogger("components")
        self.logger.setLevel(logging.DEBUG)
        handler = logging.FileHandler(filename="DungeonWhisperer.log", encoding="utf-8", mode="a")
        handler.setFormatter(logging.Formatter("%(asctime)s: [%(levelname)s]: (%(name)s): %(message)s"))
        self.logger.addHandler(handler)

    def walk_components(self, components: List[Component]) -> Generator[Component, None, None]:
        """A generator yielding each component from a list of components.

        Action row components have their inner components extracted from them
        
        Parameters
        ----------
        components : List[Component]
            The list of components to walk through
        
        Yields
        -------
        Each `Component`
        """

        for component in components:
            if isinstance(component, ActionRow):
                yield from self.walk_components(component.components)
            else:
                yield component

    @staticmethod
    async def set_message_components(message: discord.Message, components: List[Component]):
        """Sets a message's list of components
        
        Parameters
        ----------
        message : discord.Message
            The message to edit
        components: List[Component]
            The list of components to set for the message
        """

        await message.edit(
            content=message.content,
            embed=message.embeds[0] if message.embeds else None,
            components=components
        )

    async def fetch_all_components(self, message: Union[discord.Message, discord.PartialMessage]) -> List[Component]:
        """Fetches all components from a message, including Action Rows
        
        The resulting list can be walked through to yield all non Action Row components via `self.walk_components`

        Parameters
        ----------
        message : Union[discord.Message, discord.PartialMessage]
            The message to fetch the components from
        
        Returns
        -------
        A list of `Component` objects from the `message`
        """
        
        # fetch the message data first
        data: dict = await self.bot.http.get_message(message.channel.id, message.id)

        raw_components: List[dict] = data.get("components", [])
        if raw_components:
            components: List[Component] = []

            # extract all components from the message
            for component in raw_components:
                component_type = ComponentType(component["type"])
                if component_type is ComponentType.ActionRow:
                    components.append(ActionRow.from_dict(component))
                elif component_type is ComponentType.Button:
                    components.append(Button.from_dict(component))
                elif component_type is ComponentType.SelectMenu:
                    components.append(SelectMenu.from_dict(component))

            return components

        return raw_components  # empty list

    @staticmethod
    def update_component(components: List[Component], component_id_or_label: str, setter: Callable[[Component], Component]) -> List[Component]:
        """Updates a component contained within `components`, and returns the updated list
        
        Action Rows are walked through and updated automatically

        Parameters
        ----------
        components : List[Component]
            The list of components to update
        component_id_or_label : str
            The ID or label of the component to update.
            For updating menus, you should always supply the menu ID.
            For buttons, you should always supply the button's label.
            This is because link buttons are unique and do not have a custom_id associated with them.
        setter : Callable[Component]->Component
            The setter function to execute on the component to update. This callable
            should take one parameter (the component to update) and return the updated component.
        
        Returns
        -------
        The updated `list` of `Component`s
        """
        
        def _do_update_inner(cmpts: List[Component], outer_index: int, inner_index: int, setter: Callable[[Component], Component]) -> List[Component]:
            """Helper function that updates a component within an action row"""

            cmpts[outer_index].components[inner_index] = setter(cmpts[outer_index].components[inner_index])
            return cmpts

        def _do_update_outer(cmpts: List[Component], index: int, setter: Callable[[Component], Component]) -> List[Component]:
            cmpts[index] = setter(cmpts[index])
            return cmpts

        for i, component in enumerate(components):
            if isinstance(component, ActionRow):
                # walk through the action row's components and update as well
                for j, inner_component in enumerate(component.components):        
                    if isinstance(inner_component, Button) and inner_component.label == component_id_or_label:
                        # link buttons don't have a custom id, so update the button based off
                        # of the button's label instead
                        return _do_update_inner(components, i, j, setter)
                    elif isinstance(inner_component, SelectMenu) and inner_component.custom_id == component_id_or_label:
                        # this is just a select menu, which always has a
                        # custom_id, so proceed with usual updates
                        return _do_update_inner(components, i, j, setter)
            elif isinstance(component, Button) and component.label == component_id_or_label:
                return _do_update_outer(components, i, setter)
            elif isinstance(component, SelectMenu) and component.custom_id == component_id_or_label:
                return _do_update_outer(components, i, setter)
            
        return components
    
    @commands.Cog.listener()
    async def on_button_click(self, interaction: MessageInteraction):
        """Ran whenever a button is clicked

        Parameters
        ----------
        interaction : MessageInteraction
            The interaction containing the button clicked
        """

        custom_id = interaction.component.custom_id
        guild = interaction.guild
        channel = interaction.channel
        message = interaction.message

        row = await self.bot.database.query_first(
            "SELECT * FROM button_actions WHERE server_id = ? AND channel_id = ? AND message_id = ? AND button_id = ?",
            parameters=(guild.id, channel.id, message.id, custom_id)
        )

        self.logger.debug(f"Received button click interaction on button {custom_id} from guild {guild.id}, message {message.jump_url}")

        if row:
            environment = ActionEnvironment(json.loads(row["action"]))
            await environment.execute(interaction)

    @commands.Cog.listener()
    async def on_dropdown(self, interaction: MessageInteraction):
        custom_id = interaction.component.custom_id
        guild = interaction.guild
        channel = interaction.channel
        message = interaction.message

        rows = await self.bot.database.query_all(
            "SELECT * FROM menu_actions WHERE server_id = ? AND channel_id = ? AND message_id = ? AND menu_id = ?",
            parameters=(guild.id, channel.id, message.id, custom_id)
        )

        key: Dict[str, ActionEnvironment] = {}
        for row in rows:
            row = dict(row)
            action_data = json.loads(row["action"])
            self.logger.debug(f"Action data is of type {type(action_data)!r}: {action_data!r}")
            key[row["option_label"]] = ActionEnvironment(action_data)

        self.logger.debug(f"Received menu interaction on menu {custom_id} from guild {guild.id}, channel {channel.id}, message {message.id}")

        for selected_option in interaction.component.selected_options:
            label = selected_option.label
            try:
                environment: ActionEnvironment = key[label]
            except KeyError:
                self.logger.warning(f"Missing action environment key entry with key {label!r}. Ignoring actions for this option.")
            else:
                await environment.execute(interaction, self.bot)

    @commands.guild_only()
    @is_guild_moderator()
    @commands.group(name="selectmenu", aliases=["menu"])
    async def selectmenu_group(self, ctx: Context):
        """
        Commands for creating/modifying select menus
        """

        if ctx.invoked_subcommand is None:
            await ctx.send_help(self.selectmenu_group)

    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_group.command(name="download")
    async def selectmenu_download_command(self, ctx: Context, message: discord.Message, menu_id: str):
        """
        Downloads a select menu in JSON form
        """

        # extract the menu object first
        components = await self.fetch_all_components(message)
        if components:
            menus: List[SelectMenu] = list(filter(lambda c: isinstance(c, SelectMenu), self.walk_components(components)))
            menu: SelectMenu = discord.utils.find(lambda m: m.custom_id == menu_id, menus)

            if menu:
                # convert the menu to JSON
                data = menu.to_dict()
                del data["type"]

                rows = await self.bot.database.query_all(
                    "SELECT * FROM menu_actions WHERE server_id = ? AND channel_id = ? AND message_id = ? AND menu_id = ? ORDER BY priority ASC",
                    parameters=(ctx.guild.id, ctx.channel.id, ctx.message.id, menu.custom_id)
                )
                rows: List[dict] = [dict(row) for row in rows]
                

                # iterate through all the actions, determining what actions, if any, need to be copied
                for i, option in enumerate(menu.options):
                    row = discord.utils.find(lambda r: r["label"] == option.label, rows)
                    data["options"][i]["actions"] = {}
                
                await ctx.send(file=discord.File(io.StringIO(json.dumps(data, indent=4)), "menu_download.json"))

    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_group.group(name="option", aliases=["options"])
    async def selectmenu_option_group(self, ctx: Context):
        """
        Commands for modifying options on a select menu
        """

        if ctx.invoked_subcommand is None:
            await ctx.send_help(self.selectmenu_option_group)
    
    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_option_group.command(name="add")
    async def selectmenu_option_add_command(self, ctx: Context, message: discord.Message, menu_id: str):
        """
        Adds a new option to a select menu. New options are automatically appended to the end of any currently existing options.

        By default, interacting with the resulting option does no action
        """

        components = await self.fetch_all_components(message)

        timeout = 30

        # prompt label
        try:
            label = await ctx.prompt_string(Embed(description="Input the label to use for the option"), timeout=timeout)
        except PromptTimedout as e:
            return await ctx.reply_fail(f"Timed out after {e.timeout} seconds, try again")
        
        # prompt value
        try:
            value = await ctx.prompt_string(Embed(description="Input the value to use for the option"), timeout=timeout)
        except PromptTimedout as e:
            return await ctx.reply_fail(f"Timed out after {e.timeout} seconds, try again")

        # prompt description
        timeout = 45
        try:
            description = await ctx.prompt_string(Embed(description="Input the description to use for the option"), timeout=timeout)
        except PromptTimedout as e:
            return await ctx.reply_fail(f"Timed out after {e.timeout} seconds, try again")

        def setter(menu: SelectMenu) -> SelectMenu:
            menu.options.append(
                SelectOption(
                    label, value, description
                )
            )
            return menu

        await self.set_message_components(message, self.update_component(components, menu_id, setter))       
        await ctx.reply_success("Menu option added")
    
    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_option_group.command(name="remove", aliases=["delete"])
    async def selectmenu_option_remove_command(self, ctx: Context, message: discord.Message, menu_id: str, *, option_label: str):
        """
        Removes a select menu option with a particular label
        """

        components = await self.fetch_all_components(message)

        def setter(menu: SelectMenu) -> SelectMenu:
            for i, option in enumerate(menu.options):
                if option.label == option_label:
                    del menu.options[i]
                    break

            return menu
            
    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_option_group.group(name="actions", aliases=["action", "acts", "act"])
    async def selectmenu_option_actions_group(self, ctx: Context):
        """
        Commands for modifying resulting option actions. Lists valid actions if no subcommand is specified
        """

        if ctx.invoked_subcommand is None:
            await ctx.send_help(self.selectmenu_option_actions_group)
    
    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_option_actions_group.command(name="set")
    async def selectmenu_option_actions_set_command(
        self,
        ctx: Context,
        message: discord.Message,
        menu_id: str,
        *, option_label: str
    ):
        """
        Sets an action to a particular menu option, depending on whether the option was selected or not
        """

        if ctx.message.attachments and ctx.message.attachments[0].filename.endswith(".json"):
            attachment = ctx.message.attachments[0]
            try:
                data: bytes = await attachment.read()
                data: dict = json.loads(data)

                await self.bot.database.execute(
                    "INSERT INTO menu_actions VALUES (?, ?, ?, ?, ?, ?)",
                    parameters=(ctx.guild.id, message.channel.id, message.id, menu_id, option_label, json.dumps(data, indent=0))
                )

                await ctx.reply_success("Action set")
            except discord.HTTPException as e:
                await ctx.reply_fail(f"Couldn't read attachment data: {e.__class__.__name__}")
            except json.JSONDecodeError as e:
                await ctx.reply_fail(f"Failed to decode JSON in attachment at line {e.lineno}")
            else:
                return data

    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_option_group.command(name="value")
    async def selectmenu_option_value_command(self, ctx: Context, message: discord.Message, menu_id: str, *, label: str):
        """
        Changes the value for a particular menu option
        """

        try:
            value = await ctx.prompt_string(Embed(description="Input the value to use for the option"), timeout=45)
        except PromptTimedout as e:
            return await ctx.reply_fail(f"Timed out after {e.timeout} seconds, try again")
    
        # edit menu
        components = await self.fetch_all_components(message)

        def setter(menu: SelectMenu) -> SelectMenu:
            for i, option in enumerate(menu.options):
                if option.label == label:
                    menu.options[i].value = value
                    break

            return menu
        
        await self.set_message_components(message, self.update_component(components, menu_id, setter))
        await ctx.reply_success("Menu option value updated")

    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_option_group.command(name="description", aliases=["desc"])
    async def selectmenu_option_description_command(self, ctx: Context, message: discord.Message, menu_id: str, *, option_label: str):
        """
        Changes the description of a particular menu option
        """

        # prompt new description
        try:
            description = await ctx.prompt_string(Embed(description="Input the description to use for the option"), timeout=45)
        except PromptTimedout as e:
            return await ctx.reply_fail(f"Timed out after {e.timeout} seconds, try again")

        # edit menu
        components = await self.fetch_all_components(message)

        def setter(menu: SelectMenu) -> SelectMenu:
            for i, option in enumerate(menu.options):
                if option.label == option_label:
                    menu.options[i].description = description
                    break

            return menu
        
        await self.set_message_components(message, self.update_component(components, menu_id, setter))
        await ctx.reply_success("Menu option description updated")

    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_option_group.command(name="rename", aliases=["label"])
    async def selectmenu_option_rename_command(self, ctx: Context, message: discord.Message, menu_id: str, *, option_label: str):
        """
        Changes the label of a particular menu option
        """

        try:
            new_label = await ctx.prompt_string(Embed(description="Input the label to use for the option"), timeout=45)
        except PromptTimedout as e:
            return await ctx.reply_fail(f"Timed out after {e.timeout} seconds, try again")

        # edit menu
        components = await self.fetch_all_components(message)

        def setter(menu: SelectMenu) -> SelectMenu:
            for i, option in enumerate(menu.options):
                if option.label == option_label:
                    menu.options[i].label = new_label
                    break

            return menu
        
        await self.set_message_components(message, self.update_component(components, menu_id, setter))
        await ctx.reply_success("Menu option label updated")

    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_option_group.group(name="emoji", aliases=["emojis"])
    async def selectmenu_option_emoji_group(self, ctx: Context):
        """
        Commands for changing the emoji of a particular menu option
        """

        if ctx.invoked_subcommand is None:
            await ctx.send_help(self.selectmenu_option_emoji_group)
    
    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_option_emoji_group.group(name="set", aliases=["add"])
    async def selectmenu_option_emoji_set_command(self, ctx: Context, message: discord.Message, menu_id: str, *, option_label: str):
        """
        Sets the emoji for a particular menu option
        """

        components = await self.fetch_all_components(message)

        try:
            emoji_str = await ctx.prompt_string(Embed(description="Input the emoji to use for the option"), timeout=30)
            emoji = await commands.PartialEmojiConverter().convert(ctx, emoji_str)
        except commands.PartialEmojiConversionFailure:
            emoji = emoji_str
        except PromptTimedout as err:
            return await ctx.reply_fail(f"Prompt timed out after {err.timeout} seconds, try again")

        def setter(menu: SelectMenu) -> SelectMenu:
            for i, option in enumerate(menu.options):
                if option.label == option_label:
                    menu.options[i].emoji = emoji
                    break

            return menu
        
        await self.set_message_components(message, self.update_component(components, menu_id, setter))
        await ctx.reply_success("Option emoji set")
    
    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_option_emoji_group.group(name="clear", aliases=["remove", "delete"])
    async def selectmenu_option_emoji_clear_command(self, ctx: Context, message: discord.Message, menu_id: str, *, option_label: str):
        """
        Removes the emoji for a particular menu option
        """

        components = await self.fetch_all_components(message)

        def setter(menu: SelectMenu) -> SelectMenu:
            for i, option in enumerate(menu.options):
                if option.label == option_label:
                    menu.options[i].emoji = None
                    break

            return menu
        
        await self.set_message_components(message, self.update_component(components, menu_id, setter))
        await ctx.reply_success("Option emoji cleared")

    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_option_group.command(name="reorder", aliases=["order", "setorder"])
    async def selectmenu_option_reorder_command(self, ctx: Context, message: discord.Message, menu_id: str):
        """
        Changes the order of a menu's options by label. The bot will prompt for the label of the option to set, from highest to lowest on the menu
        """
        
        components = await self.fetch_all_components(message)

        # this is a lazy way to get the menu we want
        menu = None
        for component in self.walk_components(components):
            if component.custom_id == menu_id and isinstance(component, SelectMenu):
                menu: SelectMenu = component
                break
        
        if not menu:
            return await ctx.reply_fail(f"No menu with custom ID `{menu_id}` found on that message")
        
        i = 0
        while True:
            try:
                index = int(await ctx.prompt_string(Embed(description=f"Input the label of the option that should be placed at index {i+1}")))
            except ValueError:
                await ctx.send("e")
    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_option_group.command(name="info")
    async def selectmenu_option_info_command(self, ctx: Context, message: discord.Message, menu_id: str, *, option_label: Optional[str]):
        """
        Returns information about a particular select menu option
        """

        components = await self.fetch_all_components(message)

        menus: List[SelectMenu] = list(filter(lambda c: isinstance(c, SelectMenu), self.walk_components(components)))
        if menus:
            menu: SelectMenu = discord.utils.find(lambda m: m.custom_id == menu_id, menus)
            if menu:
                # menu found, display its info
                if option_label:
                    # display info for a particular label
                    option: Optional[SelectOption] = discord.utils.find(lambda o: o.label == option_label, menu.options)
                    if option:
                        # found the option
                        await ctx.send(
                            embed=Embed(
                                title=f"Option Info - Label {option.label}",
                                description=f"Option `{option.label}` contains the following data",
                                color=self.bot.color,
                                url=message.jump_url,
                                author=message.guild
                            ).add_field(
                                name="Description",
                                value=option.description
                            ).add_field(
                                name="Value",
                                value=option.value
                            ).add_field(
                                name="Emoji",
                                value=option.emoji
                            ).add_field(
                                name="Default?",
                                value="Yes" if option.default else "No"
                            )
                        )
                    else:
                        # no option found
                        await ctx.reply_fail(f"No option found with label `{option_label}`")
                else:
                    # list all the labels and other option information
                    await ctx.send(
                        embed=Embed(
                            title=f"Option Info - Menu {menu_id}",
                            description=f"Menu {menu_id} on [this message]({message.jump_url}) contains the following option data",
                            color=self.bot.color,
                            url=message.jump_url,
                            author=message.guild
                        ).add_field(
                            name="Option Metadata",
                            value=f"• __Minimum Required:__ {menu.min_values}\n• __Maximum Required:__ {menu.max_values}\n• __Total Options:__ {len(menu.options)}",
                            inline=False
                        ).add_field(
                            name="Labels/Values",
                            value="\n".join([f"• `{option.label}` - `{option.value}`{' (default)' if option.default else ''}" for option in menu.options]),
                            inline=False
                        )
                    )
            else:
                # no menu with that custom ID found
                await ctx.reply_fail(f"No menu found with custom ID {menu_id!r}")
        else:
            await ctx.reply_fail("No menus found on that message")

    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_group.command(name="renameid", aliases=["setid", "id"])
    async def selectmenu_renameid_command(self, ctx: Context, message: discord.Message, menu_id: str, new_id: str):
        """
        Changes the custom id of a menu
        """

        await self.bot.database.execute(
            "UPDATE menu_actions SET menu_id = ? WHERE menu_id = ?",
            parameters=(new_id, menu_id)
        )

        components = await self.fetch_all_components(message)

        def setter(menu: SelectMenu) -> SelectMenu:
            menu.custom_id = new_id
            return menu
        
        await self.set_message_components(message, self.update_component(components, menu_id, setter))
        await ctx.reply_success("Menu ID renamed")


    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_group.command(name="placeholder")
    async def selectmenu_placeholder_command(self, ctx: Context, message: discord.Message, menu_id: str, *, new_placeholder: str):
        """
        Changes the placeholder text for a menu
        """

        components = await self.fetch_all_components(message)

        def setter(menu: SelectMenu) -> SelectMenu:
            menu.placeholder = new_placeholder
            return menu

        await self.set_message_components(message, self.update_component(components, menu_id, setter))

        await ctx.reply_success("Menu updated")

    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_group.command(name="selectrange", aliases=["numselect", "selectnum", "selectcount", "itemnum"])
    async def selectmenu_selectrange_command(
        self,
        ctx: Context,
        message: discord.Message,
        menu_id: str,
        minimum: int,
        maximum: int
    ):
        """
        Sets the number of selections that can be made on a menu
        """

        components = await self.fetch_all_components(message)

        def setter(menu: SelectMenu) -> SelectMenu:
            menu.min_values = minimum
            menu.max_values = maximum
            return menu

        await self.set_message_components(message, self.update_component(components, menu_id, setter))
        await ctx.reply_success("Menu updated")

    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_group.command(name="disable")
    async def selectmenu_disable_command(self, ctx: Context, message: discord.Message, menu_id: str):
        """
        Disables a menu
        """

        components = await self.fetch_all_components(message)

        def setter(menu: SelectMenu) -> SelectMenu:
            menu.disabled = True
            return menu
        
        await self.set_message_components(message, self.update_component(components, menu_id, setter))
        await ctx.reply_success("Menu updated")

    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_group.command(name="enable")
    async def selectmenu_enable_command(self, ctx: Context, message: discord.Message, menu_id: str):
        """
        Enables a menu
        """

        components = await self.fetch_all_components(message)

        def setter(menu: SelectMenu) -> SelectMenu:
            menu.disabled = False
            return menu
        
        await self.set_message_components(message, self.update_component(components, menu_id, setter))

        await ctx.reply_success("Menu updated")

    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_group.command(name="add")
    async def selectmenu_add_command(self, ctx: Context, message: Optional[discord.Message], custom_id: str):
        """
        Creates a new select menu with default values
        If a `message` is specified, the new menu will be appended onto the `message` on a new action row
        If this isn't specified, then a new menu is created from a blank message in the current channel
        """

        menu = SelectMenu(
            custom_id=custom_id,
            placeholder="Poke me!",
            options=[
                SelectOption("Option 1", "Value 1", description="Description 1")
            ]
        )

        if message:
            action = message.edit
            content = message.content if message.content else "I'm a menu!"
            components = await self.fetch_all_components(message)
            components.append(ActionRow(menu))
        else:
            action = ctx.send
            content = "I'm a menu"
            components = [menu]

        await action(content=content, components=components)
        await ctx.reply_success("Menu added")

    @commands.guild_only()
    @commands.is_owner()
    @selectmenu_group.command(name="remove")
    async def selectmenu_remove_command(self, ctx: Context, message: discord.Message, menu_id: str):
        """
        Removes a particular menu from a message
        """

        components = await self.fetch_all_components(message)
        
        for i, component in enumerate(components):
            if isinstance(component, ActionRow):
                for j, inner_component in enumerate(component.components):
                    if isinstance(inner_component, SelectMenu) and inner_component.custom_id == menu_id:
                        del components[i].components[j]
                        break
            elif isinstance(component, SelectMenu) and component.custom_id == menu_id:
                del components[i]
                break
        
        await message.edit(
            content=message.content,
            embed=message.embeds[0] if message.embeds else None,
            components=components
        )

        await ctx.reply_success("Menu removed")

    @commands.guild_only()
    @commands.is_owner()
    @commands.command("raw")
    async def raw_command(self, ctx: Context, message: discord.PartialMessage):
        data = await self.bot.http.get_message(message.channel.id, message.id)

        import json

        data = json.dumps(data, indent=4)

        text = f"```json\n{data}```"
        if len(text) > 2000:
            file = discord.File(io.StringIO(data), "raw.json")
            return await ctx.reply(file=file)

        return await ctx.reply(text)

    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_group.command(name="info")
    async def selectmenu_info_command(self, ctx: Context, message: discord.Message, menu_id: Optional[str]):
        """
        Returns information about a particular menu
        """

        components = await self.fetch_all_components(message)
        menus: List[SelectMenu] = list(filter(lambda c: isinstance(c, SelectMenu), self.walk_components(components)))

        if menu_id:
            menu: SelectMenu = discord.utils.find(lambda m: m.custom_id == menu_id, menus)
            if menu:
                # menu found, display its info
                await ctx.send(
                    embed=Embed(
                        title="Menu Info",
                        description=f"Menu `{menu_id}` contains the following data",
                        color=self.bot.color,
                        author=message.guild,
                        url=message.jump_url
                    ).add_field(
                        name="**Status**",
                        value="DISABLED" if menu.disabled else "enabled"
                    )
                    .add_field(
                        name="**Placeholder Text**",
                        value=menu.placeholder if menu.placeholder else "*No placeholder text*"
                    ).add_field(
                        name="**Selections**",
                        value=f"• __Min__: {menu.min_values}\n• __Max__: {menu.max_values}\n• __Total Available__: {len(menu.options)}"
                    )
                )
            else:
                # no menu with that custom ID found
                await ctx.reply_fail(f"No menu found with custom ID {menu_id!r}")
        else:
            # I kinda like grammar, so lets use it
            menu_count = len(menus)
            if menu_count == 1:
                pre = "There is 1 menu"
            else:
                pre = f"There are {menu_count} menus"
                
            await ctx.send(embed=Embed(
                title=f"Menu Info [{menu_count}]",
                description=f"{pre} located on [this message]({message.jump_url})",
                color=self.bot.color,
                url=message.jump_url,
                author=message.guild
            ).add_field(
                name="**Menu IDs**",
                value="\n".join([f"• `{menu.custom_id}`" for menu in menus])
            ))
    
    @commands.guild_only()
    @is_guild_moderator()
    @commands.group(name="role")
    async def role_group(self, ctx: Context):
        """
        Commands for user role manipulation
        """

        if ctx.invoked_subcommand is None:
            await ctx.send_help(self.role_group)
    
    @commands.guild_only()
    @is_guild_moderator()
    @role_group.group(name="groups", aliases=["group"])
    async def role_groups_group(self, ctx: Context):
        """
        Commands for modifying role groups
        """

        if ctx.invoked_subcommand is None:
            await ctx.send_help(self.role_groups_group)
    
    @commands.guild_only()
    @is_guild_moderator()
    @role_groups_group.command(name="list")
    async def role_groups_list_command(self, ctx: Context, group_name: Optional[str]):
        """
        Lists the names of all this guild's command groups or, if specified, the roles in a particular group
        """

        if not group_name:
            # get the number of groups
            rows = await self.bot.database.query_all(
                "SELECT DISTINCT name FROM role_groups WHERE server_id = ?",
                parameters=(ctx.guild.id,)
            )

            if rows:
                # there's at least one group

                await ctx.send(
                    embed=Embed(
                        title=f"Role Groups [{len(rows)}]",
                        description="\n".join([f"• `{row['name']}`" for row in rows]),
                        color=self.bot.color
                    )
                )
            else:
                await ctx.reply_fail("No role groups found for this guild")
        else:
            rows = await self.bot.database.query_all(
                "SELECT * FROM role_groups WHERE server_id = ? AND name = ? ORDER BY role_id",
                parameters=(ctx.guild.id, group_name)
            )

            if rows:
                total = len(rows)

                await ctx.send(
                    embed=Embed(
                        title=f"Role Group Info - {total}",
                        description=f"Role group `{group_name}` contains {total} total roles",
                        color=self.bot.color,
                        author=ctx.guild
                    ).add_fields(
                        "Roles", "Roles (cont.)",
                        value="\n".join([f"• <@&{row['role_id']}> - `{row['role_id']}`" for row in rows])
                    )
                )
            else:
                await ctx.reply_fail(f"No group named `{group_name}` found")

    @commands.guild_only()
    @is_guild_moderator()
    @role_groups_group.command(name="add")
    async def role_groups_add_command(self, ctx: Context, group_name: str, roles: commands.Greedy[discord.Role]):
        """
        Adds one or more roles to an existing role group or creates a new group if not found
        """

        for role in roles:
            await self.bot.database.execute(
                "INSERT INTO role_groups VALUES(?, ?, ?)",
                parameters=(ctx.guild.id, group_name, role.id)
            )
        
        await ctx.reply_success(f"Grouped `{len(roles)}` roles into `{group_name}`")

    @commands.guild_only()
    @is_guild_moderator()
    @commands.group(name="button", aliases=["buttons"])
    async def button_group(self, ctx: Context):
        """
        Commands for configuring buttons
        """
        
        if ctx.invoked_subcommand is None:
            await ctx.send_help(self.button_group)

    @commands.guild_only()
    @is_guild_moderator()
    @button_group.command(name="add")
    async def button_add_command(self, ctx: Context, message: Optional[discord.Message], button_type: ButtonType, *, label: str):
        """
        Adds a new button to a message
        """

        # link buttons do not contain a custom id, so only
        # prompt a custom id for non-link buttons
        if button_type is not ButtonType.link:
            url = None

            try:
                custom_id = await ctx.prompt_string(Embed(description="Input the custom ID for the button"), timeout=30)
            except PromptTimedout as e:
                return await ctx.reply_fail(f"Timed out after {e.timeout} seconds, try again")
        else:
            # prompt the url instead
            # TODO: make a URL converter
            custom_id = None

            try:
                url = await ctx.prompt_string(Embed(description="Input the url for the button"), timeout=30)
            except PromptTimedout as e:
                return await ctx.reply_fail(f"Timed out after {e.timeout} seconds, try again")

        if message:
            action = message.edit
            content = message.content if message.content else "I'm a button!"
            components = await self.fetch_all_components(message)

            # figure out where to place the button
            # NOTE: this algorithm places the button in the first
            # suitable spot
            placed = False
            for i, component in enumerate(components):
                if isinstance(component, ActionRow):
                    # check if the action row is holding a button and if it has space
                    if any(isinstance(comp, Button) for comp in component.components) and len(component.components) < 5:
                        component.add_button(
                            style=button_type.value,
                            label=label,
                            custom_id=custom_id,
                            url=url
                        )

                        components[i] = component
                        placed = True
            
            # try to make another action row instead
            if not placed:
                count = 0
                for component in components:
                    if isinstance(component, ActionRow):
                        count += 1
                
                if count < 5:
                    # add another action row
                    components.append(ActionRow(Button(
                        style=button_type.value,
                        label=label,
                        custom_id=custom_id,
                        url=url
                    )))
                else:
                    return await ctx.reply_fail("Could not add another button to that message")
        else:
            action = ctx.send
            content = "I'm a button!"
            components = [Button(
                style=button_type.value,
                label=label,
                custom_id=custom_id,
                url=url
            )]

        await action(content=content, components=components)
        await ctx.reply_success(f"{button_type.name.capitalize()} button {custom_id} added")

    @commands.guild_only()
    @is_guild_moderator()
    @button_group.command(name="reorder")
    async def button_reorder_command(self, ctx: Context):
        """
        Reorders the buttons in a message
        """
        
        pass

    @commands.guild_only()
    @is_guild_moderator()
    @button_group.group(name="emoji", aliases=["emojis"])
    async def button_emoji_group(self, ctx: Context):
        """
        Commands for modifying emojis on buttons
        """

        if ctx.invoked_subcommand is None:
            await ctx.send_help(self.button_emoji_group)

    @commands.guild_only()
    @is_guild_moderator()
    @button_emoji_group.command(name="set", aliases=["add", "edit"])
    async def button_emoji_set_command(self, ctx: Context, message: discord.Message, button_id: str, emoji: Union[discord.PartialEmoji, str]):
        """
        Sets the emoji for a button
        """

        components = await self.fetch_all_components(message)

        def setter(button: Button) -> Button:
            button.emoji = emoji
            return button
        
        await self.set_message_components(message, self.update_component(components, button_id, setter))
        await ctx.reply_success("Button emoji set")
    
    @commands.guild_only()
    @is_guild_moderator()
    @button_emoji_group.command(name="clear", aliases=["remove", "delete"])
    async def button_emoji_clear_command(self, ctx: Context, message: discord.Message, button_id: str):
        """
        Removes the emoji for a button
        """

        components = await self.fetch_all_components(message)

        def setter(button: Button) -> Button:
            button.emoji = None
            return button
        
        await self.set_message_components(message, self.update_component(components, button_id, setter))
        await ctx.reply_success("Button emoji cleared")

    @commands.guild_only()
    @is_guild_moderator()
    @button_group.command(name="info")
    async def button_info_command(self, ctx: Context):
        """
        Returns info about buttons in a message
        """

        pass

    @commands.guild_only()
    @is_guild_moderator()
    @button_group.command(name="remove")
    async def button_remove_command(self, ctx: Context, message: discord.Message, button_id: str):
        """
        Removes a button from a message
        """
    
        components = await self.fetch_all_components(message)
        
        for i, component in enumerate(components):
            if isinstance(component, ActionRow):
                for j, inner_component in enumerate(component.components):
                    if isinstance(inner_component, Button) and inner_component.custom_id == button_id:
                        del components[i].components[j]
                        break
            elif isinstance(component, Button) and component.custom_id == button_id:
                del components[i]
                break
        
        await self.set_message_components(message, components)
        await ctx.reply_success("Button removed")

    @commands.guild_only()
    @is_guild_moderator()
    @button_group.command(name="rename", aliases=["label"])
    async def button_rename_command(self, ctx: Context, message: discord.Message, button_id: str):
        """
        Changes the label of a button
        """

        try:
            new_label = await ctx.prompt_string(Embed(description="Input the label to use for the button"), timeout=45)
        except PromptTimedout as e:
            return await ctx.reply_fail(f"Timed out after {e.timeout} seconds, try again")

        components = await self.fetch_all_components(message)

        def setter(button: Button) -> Button:
            button.label = new_label
            return button
        
        await self.set_message_components(message, self.update_component(components, button_id, setter))
        await ctx.reply_success("Button label updated")

    @commands.guild_only()
    @is_guild_moderator()
    @button_group.command(name="style")
    async def button_style_command(self, ctx: Context):
        """
        Changes the style of a button.

        This can only be done for non-link buttons.
        """

        pass

    @commands.guild_only()
    @is_guild_moderator()
    @button_group.command(name="disable")
    async def button_disable_command(self, ctx: Context, message: discord.Message, button_id: str):
        """
        Disables a button
        """

        components = await self.fetch_all_components(message)

        def setter(button: Button) -> Button:
            button.disabled = True
            return button
        
        await self.set_message_components(message, self.update_component(components, button_id, setter))
        await ctx.reply_success("Button disabled")

    @commands.guild_only()
    @is_guild_moderator()
    @button_group.command(name="enable")
    async def button_enable_command(self, ctx: Context, message: discord.Message, button_id: str):
        """
        Enables a button
        """

        components = await self.fetch_all_components(message)

        def setter(button: Button) -> Button:
            button.disabled = False
            return button
        
        await self.set_message_components(message, self.update_component(components, button_id, setter))
        await ctx.reply_success("Button enabled")

    @commands.guild_only()
    @is_guild_moderator()
    @button_group.group(name="actions")
    async def button_actions_group(self, ctx: Context):
        """
        Commands for configuring button click actions
        """

        if ctx.invoked_subcommand is None:
            await ctx.send_help(self.button_actions_group)

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

    def get_message_json(self, message: discord.Message) -> dict:
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
        
        return data

    @commands.guild_only()
    @is_guild_moderator()
    @button_actions_group.command(name="set")
    async def button_actions_set_command(self, ctx: Context, message: discord.Message, button_id: str):
        """
        Sets the action of a button
        """

        if ctx.message.attachments and ctx.message.attachments[0].filename.endswith(".json"):
            attachment = ctx.message.attachments[0]
            try:
                data: bytes = await attachment.read()
                data: dict = json.loads(data)

                await self.bot.database.execute(
                    "INSERT INTO button_actions VALUES (?, ?, ?, ?, ?, ?)",
                    parameters=(ctx.guild.id, message.channel.id, message.id, button_id, json.dumps(data, indent=0))
                )

                await ctx.reply_success("Action set")
            except discord.HTTPException as e:
                await ctx.reply_fail(f"Couldn't read attachment data: {e.__class__.__name__}")
            except json.JSONDecodeError as e:
                await ctx.reply_fail(f"Failed to decode JSON in attachment at line {e.lineno}")
            else:
                return data

    @commands.guild_only()
    @is_guild_moderator()
    @button_actions_group.command(name="info")
    async def button_actions_info_command(self, ctx: Context):
        """
        Returns information about a button's actions
        """

        pass

    @commands.guild_only()
    @is_guild_moderator()
    @button_actions_group.command(name="remove")
    async def button_actions_remove_command(self, ctx: Context):
        """
        Removes an action from a button
        """

        pass


def setup(bot: DiscordBot):
    bot.add_cog(Components(bot))

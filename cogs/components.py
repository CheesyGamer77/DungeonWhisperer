from dislash import ActionRow, Button, Component, MessageInteraction, SelectMenu, SelectOption
import discord
import io
import json
import logging
from cheesyutils.discord_bots import DiscordBot, Context, Embed, is_guild_moderator, PromptTimedout
from cheesyutils.discord_bots.converters import RangedInteger
from cheesyutils.discord_bots.types import NameConvertibleEnum
from discord.ext import commands
from enum import Enum
from typing import Any, Callable, Generator, List, Optional, Union

class ComponentType(Enum):
    # yes, dislash has a class for this, but the author
    # doesn't seem to know that enums are a thing, and because
    # I hate working with raw integers designating types, I made
    # this as a replacement
    ActionRow = 1
    Button = 2
    SelectMenu = 3


class ConversionFailed(commands.BadArgument):
    def __init__(self, argument: str, message: str=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.argument = argument
        self.message = message


class RoleGroupNotFound(ConversionFailed):
    pass


class RoleGroup:
    def __init__(self, name: str, roles: List[discord.Role]):
        self.name = name
        self.roles = roles

    @classmethod
    async def fetch(cls, bot: DiscordBot, guild: discord.Guild, name: str) -> "RoleGroup":
        """Fetches a role group given a guild and a group name

        This differs from `convert` as it doesn't require a context, but requires a `DiscordBot`

        Parameters
        ----------
        bot : DiscordBot
            The bot to execute the database query on
        guild : discord.Guild
            The guild to fetch the role group for
        name : str
            The group name to fetch

        Raises
        ------
        `RoleGroupNotFound` if the group does not exist

        Returns
        -------
        The respective role group, if able
        """

        rows = await bot.database.query_all(
            "SELECT * FROM role_groups WHERE server_id = ? AND name = ?",
            parameters=(guild.id, name)
        )

        if rows:
            roles: List[discord.Role] = []
            for row in rows:
                try:
                    role: Optional[discord.Role] = guild.get_role(row["role_id"])
                    if role:
                        roles.append(role)
                except KeyError:
                    # logger.error("Could not retrieve role id for role group %s of guild %s. Did the database schema change?", name, guild.id)
                    pass

            return cls(name, roles)
        
        raise RoleGroupNotFound(name)

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> "RoleGroup":
        """Converts the given argument into a valid role group
        
        Parameters
        ----------
        ctx : Context
            The invokation context
        argument : str
            The argument to convert to a role group
        
        Raises
        ------
        `RoleGroupNotFound` if the group does not exist

        Returns
        -------
        The respective role group, if able
        """

        rows = await ctx.bot.database.query_all(
            "SELECT * FROM role_groups WHERE server_id = ? AND name = ?",
            parameters=(ctx.guild.id, argument)
        )

        if rows:
            roles: List[discord.Role] = []
            for row in rows:
                try:
                    role: Optional[discord.Role] = ctx.guild.get_role(row["role_id"])
                    if role:
                        roles.append(role)
                except KeyError:
                    # logger.error("Could not retrieve role id for role group %s of guild %s. Did the database schema change?", argument, ctx.guild.id)
                    pass

            return cls(argument, roles)
        
        raise RoleGroupNotFound(argument)


class MenuEvent(NameConvertibleEnum):
    on_select = 0
    on_unselect = 1


class ButtonType(NameConvertibleEnum):
    primary = 1
    secondary = 2
    success = 3
    danger = 4
    link = 5


class ComponentAction(NameConvertibleEnum):
    add_role = 0  # add a role to the user
    remove_role = 1  # remove a role from the user
    send_message = 2  # send a message
    send_followup = 3  # sends an invisible message
    remove_role_group = 4 # removes all roles part of a particular group


class ContentVisibility(NameConvertibleEnum):
    hidden = 0
    visible = 1


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

    def update_menu(self, components: List[Component], menu_id: str, setter: Callable[[SelectMenu], SelectMenu]) -> List[Component]:
        """Updates a menu contained within a component list, and returns the updated list.

        Action Rows are walked through and updated automatically

        Parameters
        ----------
        components : List[Component]
            The list of components to search through
        menu_id : str
            The custom ID of the menu to update
        setter : Callable[SelectMenu]->SelectMenu
            The setter function to execute on the menu. This callable should take
            one parameter - the menu to update - and return the updated menu
        
        Returns
        -------
        The updated list of components
        """

        for i, component in enumerate(components):
            if isinstance(component, ActionRow):
                # walk through the action row's components and update as well
                for j, inner_component in enumerate(component.components):                    
                    if isinstance(inner_component, SelectMenu) and inner_component.custom_id == menu_id:
                        inner_component = setter(inner_component)
                        component.components[j] = inner_component
                        components[i] = component
                        return components
            elif isinstance(component, SelectMenu) and component.custom_id == menu_id:
                component = setter(component)
                components[i] = component
                return components
            
        return components
    
    @commands.Cog.listener()
    async def on_button_click(self, interaction: MessageInteraction):
        """Ran whenever a button is clicked

        Parameters
        ----------
        interaction : MessageInteraction
            The interaction containing the button clicked
        """

        # fetch all actions
        custom_id = interaction.component.custom_id
        guild = interaction.guild
        channel = interaction.channel
        message = interaction.message

        rows = await self.bot.database.query_all(
            "SELECT * FROM button_actions WHERE server_id = ? AND channel_id = ? AND message_id = ? AND button_id = ? ORDER BY priority ASC",
            parameters=(guild.id, channel.id, message.id, custom_id)
        )

        self.logger.debug(f"Received button click interaction on button {custom_id} from guild {guild.id}, message {message.jump_url}")

        actions: List[dict] = [json.loads(row["action"]) for row in rows]
        self.logger.debug(f"Fetched {len(actions)} actions to execute on click for button {custom_id}")

        for action in actions:
            action_type = ComponentAction(action["type"])

            if action_type is ComponentAction.send_followup:
                self.logger.debug(f"Sending follow up message to {interaction.author.id} in guild {guild.id}, channel {channel.id}")

                # extract message JSON like we do with the embeds, and send it
                # TODO: DUPLICATE CODE!!!!!!!!!!!
                data: dict = json.loads(action["message"])
                
                keys = data.keys()
                if "version" in keys and "backups" in keys and isinstance(data["backups"], list):
                    # this is probably discohook's alternative syntax

                    # NOTE: We only look at the first backup
                    for message in data["backups"][0]["messages"]:
                        message = message["data"]
                        for i, embed_json in enumerate(message["embeds"]):
                            embed_json["type"] = "rich"
                            await interaction.respond(
                                message["content"] if i == 0 else None,
                                embed=Embed.from_dict(embed_json),
                                ephemeral=True
                            )

                elif "content" in keys and "embeds" in keys and isinstance(data["embeds"], list):
                    # this is most likely the standard format discord expects
                    for i, embed_json in enumerate(data["embeds"]):
                        embed_json["type"] = "rich"

                        await interaction.respond(
                            data["content"] if i == 0 else None,
                            embed=Embed.from_dict(embed_json),
                            ephemeral=True
                        )
                else:
                    self.logger.error(f"Undefined message action schema for message {message.jump_url} with root keys {keys}")

    @commands.Cog.listener()
    async def on_dropdown(self, interaction: MessageInteraction):
        # fetch all actions
        rows = await self.bot.database.query_all(
            "SELECT * FROM menu_actions WHERE server_id = ? AND channel_id = ? AND message_id = ? AND menu_id = ? ORDER BY priority ASC",
            parameters=(interaction.guild.id, interaction.channel.id, interaction.message.id, interaction.component.custom_id)
        )

        self.logger.debug(
            "Received menu interaction on menu %s from guild %s, channel %s, message %s",
            interaction.component.custom_id, interaction.guild.id, interaction.channel.id, interaction.message.id
        )

        for selected_option in interaction.component.selected_options:
            # extract any on_select actions for the option
            actions: List[dict] = [json.loads(row["action"]) for row in rows if MenuEvent(row["event"]) is MenuEvent.on_select and row["label"] == selected_option.label]
            self.logger.debug("Found %s actions to execute on select for label %s", len(actions), selected_option.label)

            # iterate through each of the selected options
            # execute any on_event actions, if required
            # NOTE: these iterate in order by priority
            for select_action in actions:
                action_type = ComponentAction(select_action["type"])
                if action_type is ComponentAction.add_role:
                    # check if the member doesn't already has the role
                    role_id = select_action["role_id"]
                    if not (role_id in [role.id for role in interaction.author.roles]):
                        self.logger.debug("Adding role %s to user %s", role_id, interaction.author.id)
                        await interaction.author.add_roles(discord.Object(role_id))
                elif action_type is ComponentAction.remove_role:
                    # check if the member currently has the role
                    role_id = select_action["role_id"]
                    if role_id in [role.id for role in interaction.author.roles]:
                        self.logger.debug("Removing role %s from user %s", role_id, interaction.author.id)
                        await interaction.author.remove_roles(discord.Object(role_id))

                elif action_type is ComponentAction.send_message:
                    # we.. really don't have any checks we can perform with this
                    pass
                elif action_type is ComponentAction.send_followup:
                    # same as above. we really can't check for messages already sent
                    pass
                elif action_type is ComponentAction.remove_role_group:
                    # fetch the role group and check if there's any roles to remove

                    # we first fetch the group
                    try:
                        self.logger.debug("Fetching role group %s from guild %s", select_action["group_name"], interaction.guild.id)
                        role_group = await RoleGroup.fetch(self.bot, interaction.guild, select_action["group_name"])
                    except RoleGroupNotFound:
                        self.logger.error(
                            "Failed to fetch role group %s for guild %s",
                            select_action["group_name"], interaction.guild.id
                        )
                    else:
                        # now that we have the role group, we can
                        # queue for the neccesary roles to be removed
                        grouped_roles = []
                        current_role_ids = [role.id for role in interaction.author.roles]
                        for role in role_group.roles:
                            if role.id in current_role_ids:
                                grouped_roles.append(role)
                        
                        self.logger.debug("Removing %s roles from user %s", len(grouped_roles), interaction.author.id)
                        await interaction.author.remove_roles(*grouped_roles)

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
        
        components = self.update_menu(components, menu_id, setter)

        await message.edit(
            content=message.content,
            embed=message.embeds[0] if message.embeds else None,
            components=components
        )

        await ctx.reply_success("Menu option added")
    
    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_option_group.group(name="actions", aliases=["action", "acts", "act"])
    async def selectmenu_option_actions_group(self, ctx: Context):
        """
        Commands for modifying resulting option actions. Lists valid actions if no subcommand is specified
        """

        if ctx.invoked_subcommand is None:
            if ctx.subcommand_passed is None:
                await ctx.send(
                    embed=Embed(
                        title="Valid Option Actions/Events",
                        description="These are all of the valid actions and events than an option can have",
                        color=self.bot.color, 
                    ).add_fields(
                        "Actions", "Actions (cont.)",
                        value="\n".join([f"• `{action.name}`" for action in ComponentAction])
                    ).add_fields(
                        "Events", "Events (cont.)",
                        value="\n".join([f"• `{event.name}`" for event in MenuEvent])
                    )
                )
            else:
                await ctx.send_help(self.selectmenu_option_actions_group)
    
    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_option_actions_group.command(name="set")
    async def selectmenu_option_actions_set_command(
        self,
        ctx: Context,
        message: discord.Message,
        menu_id: str,
        option_index: int,
        event: MenuEvent,
        action: ComponentAction,
        order: int
    ):
        """
        Sets an action to a particular menu option, depending on whether the option was selected or not
        """

        data = {
            "version": 1,
            "type": action.value
        }

        if action is ComponentAction.add_role or action is ComponentAction.remove_role:
            # prompt for the role
            while True:
                try:
                    arg = await ctx.prompt_string(Embed(description="Input the name, ID, or mention of the role you want to set"), timeout=30)
                    role: discord.Role = await commands.RoleConverter().convert(ctx, arg)
                    data["role_id"] = role.id
                    break
                except PromptTimedout as e:
                    return await ctx.reply_fail(f"Timed out after {e.timeout} seconds, try again")
                except commands.RoleNotFound:
                    await ctx.reply_fail(f"Could not convert `{arg}` into a valid role, try again")
        elif action is ComponentAction.remove_role_group:
            # prompt for role group name
            while True:
                try:
                    arg = await ctx.prompt_string(Embed(description="Input the name of the role group you wish to remove"), timeout=30)
                    group: RoleGroup = await RoleGroup.convert(ctx, arg)
                    data["group_name"] = group.name
                    break
                except PromptTimedout as e:
                    return await ctx.reply_fail(f"Timed out after {e.timeout} seconds, try again")
                except RoleGroupNotFound:
                    await ctx.reply_fail(f"Could not convert {arg} into a valid role group, try again")
        elif action is ComponentAction.send_followup:
            # prompt followup message content
            try:
                data["message"] = await ctx.prompt_string(Embed(description="Input the follow up message to send"), timeout=30)
            except PromptTimedout as e:
                return await ctx.reply_fail(f"Timed out after {e.timeout} seconds, try again")

        # fetch the menu and its respective option
        # this is so jank it's not even funny
        # TODO: please clean this up
        components = await self.fetch_all_components(message)
        if components:
            menus: List[SelectMenu] = list(filter(lambda c: isinstance(c, SelectMenu), self.walk_components(components)))
            menu: SelectMenu = discord.utils.find(lambda m: m.custom_id == menu_id, menus)
            if menu:
                if menu.options:
                    try:
                        option: SelectOption = menu.options[option_index]
                    except IndexError:
                        return await ctx.reply_fail(f"Invalid option index supplied (must be between 0 and {len(menu.options)-1}), try again")
                else:
                    return await ctx.reply_fail("Menu does not contain any options")
            else:
                return await ctx.reply_fail("Message does not contain a menu")
        else:
            return await ctx.reply_fail("Message does not contain any components")

        json_data = json.dumps(data)

        # add the action into the database
        await self.bot.database.execute(
            "INSERT INTO menu_actions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            parameters=(ctx.guild.id, message.channel.id, message.id, menu_id, option.label, json_data, order, event.value)
        )

        await ctx.send(f"Set option action `{action.name}` on event `{event.name}`", file=discord.File(io.StringIO(json_data), "action_data.json"))

    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_option_group.command(name="remove", aliases=["delete"])
    async def selectmenu_option_remove_command(self, ctx: Context, *, label: str):
        """
        Removes a select menu option with a particular label
        """

        pass

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
        
        components = self.update_menu(components, menu_id, setter)

        await message.edit(
            content=message.content,
            embed=message.embeds[0] if message.embeds else None,
            components=components
        )

        await ctx.reply_success("Menu option value updated")

    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_option_group.command(name="description", aliases=["desc"])
    async def selectmenu_option_description_command(self, ctx: Context, message: discord.Message, menu_id: str, *, label: str):
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
                if option.label == label:
                    menu.options[i].description = description
                    break

            return menu
        
        components = self.update_menu(components, menu_id, setter)

        await message.edit(
            content=message.content,
            embed=message.embeds[0] if message.embeds else None,
            components=components
        )

        await ctx.reply_success("Menu option description updated")

    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_option_group.command(name="rename", aliases=["label"])
    async def selectmenu_option_rename_command(self, ctx: Context, message: discord.Message, menu_id: str, *, label: str):
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
                if option.label == label:
                    menu.options[i].label = new_label
                    break

            return menu
        
        components = self.update_menu(components, menu_id, setter)

        await message.edit(
            content=message.content,
            embed=message.embeds[0] if message.embeds else None,
            components=components
        )

        await ctx.reply_success("Menu option label updated")

    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_option_group.command(name="emoji")
    async def selectmenu_option_emoji_command(self, ctx: Context, label: str, new_emoji: discord.PartialEmoji):
        """
        Changes the emoji of a particular menu option
        """

        pass

    @commands.guild_only()
    @is_guild_moderator()
    @selectmenu_option_group.command(name="reorder", aliases=["order", "setorder"])
    async def selectmenu_option_reorder_command(self, ctx: Context, message: discord.Message, menu_id: str, *, labels_separated_by_whitespace: str):
        """
        Changes the order of a menu's options by label. The left-most specified label will be at the top, and the
        right-most label will be at the bottom of the menu
        """
        
        pass

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
        
        components = self.update_menu(components, menu_id, setter)

        await message.edit(
            content=message.content,
            embed=message.embeds[0] if message.embeds else None,
            components=components
        )

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

        components = self.update_menu(components, menu_id, setter)

        await message.edit(
            content=message.content,
            embed=message.embeds[0] if message.embeds else None,
            components=components
        )

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

        components = self.update_menu(components, menu_id, setter)

        await message.edit(
            content=message.content,
            embed=message.embeds[0] if message.embeds else None,
            components=components
        )

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
        
        components = self.update_menu(components, menu_id, setter)

        await message.edit(
            content=message.content,
            embed=message.embeds[0] if message.embeds else None,
            components=components
        )

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
        
        components = self.update_menu(components, menu_id, setter)

        await message.edit(
            content=message.content,
            embed=message.embeds[0] if message.embeds else None,
            components=components
        )

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
        
        await message.edit(
            content=message.content,
            embed=message.embeds[0] if message.embeds else None,
            components=components
        )

        await ctx.reply_success("Button removed")

    @commands.guild_only()
    @is_guild_moderator()
    @button_group.command(name="rename")
    async def button_rename_command(self, ctx: Context):
        """
        Changes the label of a button
        """

        pass

    @commands.guild_only()
    @is_guild_moderator()
    @button_group.command(name="style")
    async def button_style_command(self, ctx: Context):
        """
        Changes the style of a button
        """

        pass

    @commands.guild_only()
    @is_guild_moderator()
    @button_group.command(name="disable")
    async def button_disable_command(self, ctx: Context):
        """
        Disables a button
        """

        pass

    @commands.guild_only()
    @is_guild_moderator()
    @button_group.command(name="enable")
    async def button_enable_command(self, ctx: Context):
        """
        Enables a button
        """

        pass

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
    async def button_actions_set_command(self, ctx: Context, message: discord.Message, button_id: str, action: ComponentAction, order: int):
        """
        Sets the action of a button
        """

        data = {
            "version": 1,
            "type": action.value
        }

        if action is ComponentAction.send_followup or action is ComponentAction.send_message:
            while True:
                try:
                    arg = await ctx.prompt_string(Embed(description="Input the url to the source message to use for the message data"), timeout=30)
                    source_message = await commands.MessageConverter().convert(ctx, arg)
                    data["message"] = json.dumps(self.get_message_json(source_message))
                    break
                except commands.MessageNotFound:
                    await ctx.reply_fail("No message found")
                except PromptTimedout as e:
                    return await ctx.reply_fail(f"Timed out after {e.timeout} seconds, try again")
        
        json_data = json.dumps(data)

        await self.bot.database.execute(
            "INSERT INTO button_actions VALUES (?, ?, ?, ?, ?, ?)",
            parameters=(ctx.guild.id, message.channel.id, message.id, button_id, json_data, order)
        )

        await ctx.send(f"Set button action `{action.name}` on click", file=discord.File(io.StringIO(json_data), filename="action_data.json"))

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

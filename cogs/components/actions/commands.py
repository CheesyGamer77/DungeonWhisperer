import discord
from enum import Enum
from dislash import MessageInteraction
from typing import Any, Dict, List, Optional
from cheesyutils.discord_bots import DiscordBot, Embed
from .callbacks import *
from .conditions import PredicateCondition


class ActionCommand(Enum):
    ACK = "ACK"
    ADD_ROLE = "ADD_ROLE"
    PREDICATE = "PREDICATE"
    REMOVE_ROLE_GROUP = "REMOVE_ROLE_GROUP"
    REMOVE_ROLE = "REMOVE_ROLE"
    SEND_EPHEMERAL_MESSAGE = "SEND_EPHEMERAL_MESSAGE"

    def get_class(self):
        if self is ActionCommand.ACK:
            return Ack
        elif self is ActionCommand.ADD_ROLE:
            return AddRole
        elif self is ActionCommand.PREDICATE:
            return Predicate
        elif self is ActionCommand.REMOVE_ROLE_GROUP:
            return RemoveRoleGroup
        elif self is ActionCommand.REMOVE_ROLE:
            return RemoveRole
        elif self is ActionCommand.SEND_EPHEMERAL_MESSAGE:
            return SendEphemeralMessage


class Action:
    COMMAND: ActionCommand = None

    def __init__(self, _id: str, data: dict, next_action_id: Optional[str]=None):
        self.id = _id
        self._data = data
        self.callbacks: Dict[str, str] = {}
        self._next_action_id: Optional[str] = next_action_id
        
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "command": self.COMMAND.value,
            "data": self._data,
            "next": self._next_action_id
        }

    def _get_next(self):
        """Default implementation of returning the appropriate callback after this action is executed

        By default, this attempts to fetch a "next" action and either calls that action or exits
        
        Raises
        ------
        `CallNext` if there is an additional action to call, `Exit` otherwise
        """

        next_action_id = self._next_action_id

        if next_action_id:
            raise CallNext(next_action_id)
        raise Exit

    async def call(self, interaction: MessageInteraction, bot: DiscordBot):
        """Calls a particular action

        All subclasses are required to implement this

        Parameters
        ----------
        interaction : MessageInteraction
            The incoming message interaction
        bot : DiscordBot
            The parent Discord Bot

        Raises
        ------
        `NotImplementedError` if no functionality is defined
        """

        raise NotImplementedError

    @staticmethod
    def build(data: dict) -> "Action":
        """Builds an action given a particular dict
        
        Parameters
        ----------
        data : dict
            The action JSON

        Returns
        -------
        The built `Action`
        """

        _type = ActionCommand(data["command"]).get_class()
        return _type(data["id"], data["data"], data.get("next"))


class Ack(Action):
    """Represents an "ACK" (Acknowledge) Action Command

    When called, this creates a blank interaction response. This is intended
    to be used as a default if an interaction does not get sent a response already

    This contains no attributes
    """


    COMMAND = ActionCommand.ACK

    def __init__(self, _id: str, data: dict, next_action_id: Optional[str]=None):
        self.id = _id
        if next_action_id:
            raise ValueError("ACK action commands cannot have a 'next' action command")
        
        # ACK commands don't contain any data, therefore there isn't a point in calling the parent class

    async def call(self, interaction: MessageInteraction, bot: DiscordBot):
        await interaction.create_response()
        raise Exit


class AddRole(Action):
    """Represents an "ADD_ROLE" Action Command.

    When called, this adds a specified role to the interaction author.

    This inherits from `Action`

    Attributes
    ----------
    role_id : int
        The Discord ID of the role to add to the user
    """
    
    COMMAND = ActionCommand.ADD_ROLE

    def __init__(self, _id: str, data: dict, next_action_id: str):
        super().__init__(_id, data, next_action_id=next_action_id)

        self.role_id: int = self._data["role_id"]

    async def call(self, interaction: MessageInteraction, bot: DiscordBot):
        try:
            await interaction.author.add_roles(discord.Object(self.role_id))
        except discord.HTTPException:
            # see if there's an on failure
            next_action_id = self.callbacks.pop("on_http_error", self._next_action_id)
            if next_action_id:
                raise CallNext(next_action_id)
        
        self._get_next()


class Predicate(Action):
    COMMAND = ActionCommand.PREDICATE

    def __init__(self, _id: str, data: dict, next_action_id: Optional[str]=None):
        if next_action_id:
            raise ValueError("Predicates cannot have a `next` field")
        super().__init__(_id, data, next_action_id=next_action_id)

        self.on_success_action_id = data.get("on_success")
        self.on_failure_action_id = data.get("on_failure")

        self.conditions = []
        for group in data["conditions"]:
            out = []
            for condition_data in group:
                out.append(PredicateCondition.from_dict(condition_data))
            self.conditions.append(out)

    def __repr__(self) -> str:
        return f"<Predicate (ID: {self.id!r}, {len(self.conditions)} conditions)>"

    def _get_next(self, success: bool):
        """Modified implementation of retrieving the next action to call, as the next action to be called
        depends on the response from the `PREDICATE`s `conditions`.

        Parameters
        ----------
        success : bool
            The result from querying the `PREDICATE`'s `conditions`
        
        Raises
        ------
        `CallNext` depending on the result of the `conditions`, `Exit` if the result has no corresponding action to execute
        """

        if success and self.on_success_action_id:
            raise CallNext(self.on_success_action_id)
        elif not success and self.on_failure_action_id:
            raise CallNext(self.on_failure_action_id)
        else:
            raise Exit

    async def _check_conditions(self, interaction: MessageInteraction, bot: DiscordBot) -> bool:
        async def _check_or_group(conditions: List[PredicateCondition]) -> bool:
            if not conditions:
                return True

            ret = await conditions[0].call(interaction, bot)

            for condition in conditions[1:]:
                ret |= await condition.call(interaction, bot)
            
            return ret
        
        if not (self.conditions and self.conditions[0]):
            return True

        ret = await _check_or_group(self.conditions[0])

        for group in self.conditions[1:]:
            if not ret:
                return False

            ret &= await _check_or_group(group)

        return ret

    async def call(self, interaction: MessageInteraction, bot: DiscordBot):
        result = await self._check_conditions(interaction, bot)

        await self._get_next(result)


class RemoveRoleGroup(Action):
    COMMAND = ActionCommand.REMOVE_ROLE_GROUP

    def __init__(self, _id: str, data: dict, next_action_id: str):
        super().__init__(_id, data, next_action_id)

        self.group_name: str = self._data["group_name"]

    async def call(self, interaction: MessageInteraction, bot: DiscordBot):
        # fetch the role group
        rows = await bot.database.query_all(
            "SELECT * FROM role_groups WHERE server_id = ? AND name = ?",
            parameters=(interaction.guild.id, self.group_name)
        )
        if rows:
            grouped_roles = []
            current_role_ids = [role.id for role in interaction.author.roles]
            for row in rows:
                role_id = row["role_id"]
                if role_id in current_role_ids:
                    grouped_roles.append(discord.Object(role_id))
            
            if grouped_roles:
                await interaction.author.remove_roles(*grouped_roles)
                
        self._get_next()


class RemoveRole(Action):
    COMMAND = ActionCommand.REMOVE_ROLE

    pass


class SendEphemeralMessage(Action):
    COMMAND = ActionCommand.SEND_EPHEMERAL_MESSAGE

    def __init__(self, _id: str, data: dict, next_action_id: Optional[str]=None):
        super().__init__(_id, data, next_action_id)
        self.message_data: Dict[str, Any] = data

    async def call(self, interaction: MessageInteraction, bot: DiscordBot):
        data = self.message_data

        if data.get("embed"):
            data["embed"]["type"] = "rich"

        await interaction.create_response(
            data["content"],
            embed=Embed.from_dict(data["embed"]),
            ephemeral=True
        )


from cheesyutils.discord_bots.bot import DiscordBot
import discord
from dislash import MessageInteraction
from enum import Enum
from typing import Optional


class PredicateConditionType(Enum):
    user_has_role = "USER_HAS_ROLE"
    guild_has_role = "GUILD_HAS_ROLE"
    user_has_channel_permissions = "USER_HAS_CHANNEL_PERMISSIONS"
    user_has_guild_permissions = "USER_HAS_GUILD_PERMISSIONS"


class PredicateCondition:
    """Represents a Condition within a `PREDICATE` command
    
    Attributes
    ----------
    type : PredicateConditionType
        The type of condition this is
    """
    
    def __init__(self, data: dict):
        print(f"{data!r}")
        self.type = PredicateConditionType(data["condition"])
        self.__data = data
        self._condition_data = data["data"]
    
    def __repr__(self) -> str:
        return f"<PredicateCondition {self.type.value!r}>"
    
    def to_dict(self) -> dict:
        return self.__data
    
    async def call(self, interaction: MessageInteraction, bot: DiscordBot) -> bool:
        """Calls this condition given a particular message interaction object.

        All subclasses are required to implement this.

        Parameters
        ----------
        interaction : MessageInteraction
            The incoming interaction to run the condition on
        
        Raises
        ------
        `NotImplementedError` if no behavior has been defined

        Returns
        -------
        `True` if the condition succeeded, `False` otherwise 
        """

        raise NotImplementedError

    @classmethod
    def from_dict(cls, data: dict) -> "PredicateCondition":
        """Builds a PredicateCondition based off of a particular dict

        Usually this dict is given by one of the objects in a `PREDICATE`s `conditions` list

        Parameters
        ----------
        data : dict
            The condition dict to build
        
        Raises
        ------
        `ValueError` if the data does not map to a valid condition

        Returns
        -------
        The corresponding `PredicateCondition` object
        """

        c = cls(data)
        if c.type is PredicateConditionType.user_has_role:
            return UserHasRoleCondition(data)
        elif c.type is PredicateConditionType.guild_has_role:
            return GuildHasRoleCondition(data)

        raise ValueError(f"Received invalid condition type")


class _EntityHasRoleCondition(PredicateCondition):
    def __init__(self, data: dict):
        super().__init__(data)

        self.role_id = int(self._condition_data["role_id"])
    
    async def call(self, interaction: MessageInteraction, bot: DiscordBot) -> bool:
        if self.type is PredicateConditionType.user_has_role:
            seq = interaction.author.roles
        elif self.type is PredicateConditionType.guild_has_role:
            seq = interaction.guild.roles

        return True if discord.utils.find(lambda r: r.id == self.role_id, seq) else False


class _EntityHasPermissionsCondition(PredicateCondition):
    def __init__(self, data: dict):
        super().__init__(data)

        self.permissions = discord.Permissions(int(self._condition_data["permissions_value"]))
    
    async def call(self, interaction: MessageInteraction) -> bool:
        if self.type is PredicateConditionType.user_has_channel_permissions:
            permissions = interaction.channel

class UserHasRoleCondition(_EntityHasRoleCondition):
    """"""
    
    pass


class GuildHasRoleCondition(_EntityHasRoleCondition):
    pass


class UserHasChannelPermissionsCondition(_EntityHasPermissionsCondition):
    pass


class UserHasGuildPermissionsCondition(_EntityHasPermissionsCondition):
    pass


class BotHasChannelPermissionsCondition(_EntityHasPermissionsCondition):
    pass


class BotHasGuildPermissionsCondition(_EntityHasPermissionsCondition):
    pass
    

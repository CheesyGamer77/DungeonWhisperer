from cheesyutils.discord_bots.bot import DiscordBot
from .commands import Action
from .callbacks import *
from typing import List, Dict
from dislash import MessageInteraction, Button, SelectMenu
from enum import Enum


class ActionEnvironmentType(Enum):
    buttons = "buttons"
    menus = "menus"


class ActionEvent:
    def __init__(self, data: dict):
        self.__data = data
        self.id = data["id"]
        self._key: Dict[str, Action] = {}
        for action in data["actions"]:
            action = Action.build(action)
            self._key[action.id] = action

        self.__entrypoint_identifier = self._key[data["entrypoint"]].id

    def to_dict(self) -> dict:
        return self.__data

    async def _recursively_execute_actions(self, action_id: str, interaction: MessageInteraction, bot: DiscordBot):
        try:
            action = self._key.pop(action_id, None)

            if action:
                await action.call(interaction, bot)
        except CallNext as callback:
            print(f"Executing next action of ID {callback.next_action_id!r}")
            await self._recursively_execute_actions(callback.next_action_id, interaction, bot)
        except Exit:
            print("Exiting")
            return            

    async def execute(self, interaction: MessageInteraction, bot: DiscordBot):
        await self._recursively_execute_actions(self.__entrypoint_identifier, interaction, bot)


class ActionEnvironment:
    def __init__(self, data: dict):
        self._type = ActionEnvironmentType(data["type"])
        self.events = [ActionEvent(event) for event in data["events"]]
        self.data = data
    
    def __repr__(self) -> str:
        return f"<ActionEnvironment (type {self._type.value!r}, {len(self.events)} events, )>"
    
    async def execute(self, interaction: MessageInteraction, bot: DiscordBot):
        """Executes all actions within this action environment

        Note
        ----
        In the event that multiple action events are specified (such as for Select Menus), the events will be called
        in the order they were defined in the JSON.

        Prameters
        ---------
        interaction : MessageInteraction
            The incoming message interaction event
        bot : DiscordBot
            The bot used to execute the actions
        """

        # determine which event took place
        if isinstance(interaction.component, Button):
            # get the on_button_click event
            event = list(filter(lambda event: event.id == "on_button_click", self.events))[0]
            await event.execute(interaction, bot)
        elif isinstance(interaction.component, SelectMenu):
            events = list(filter(lambda event: event.id in ["on_menu_select", "on_menu_unselect"], self.events))
            for event in events:
                await event.execute(interaction, bot)
    
    def to_dict(self) -> dict:
        return {
            "type": self._type.value,
            "events": [event.to_dict() for event in self.events]
        }

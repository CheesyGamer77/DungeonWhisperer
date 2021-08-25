class ActionCallback(Exception):
    pass


class CallNext(ActionCallback):
    def __init__(self, next_action_id: str):
        self.next_action_id = next_action_id


class Exit(ActionCallback):
    pass

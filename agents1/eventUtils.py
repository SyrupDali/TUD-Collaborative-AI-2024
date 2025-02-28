import enum
from abc import abstractmethod

# Class to manage which prompt was last encountered by the bot
class PromptSession:
    def __init__(self, bot, ttl=100):
        self.bot = bot
        self.ttl = ttl

    @abstractmethod
    def wait(self): pass
    @abstractmethod
    def on_timeout(self): pass

    def delete_self(self):
        self.bot._current_prompt = None

    @staticmethod
    def increment_values(task, willingness, competence, bot):
        bot._trustBelief(bot._team_members, bot._trustBeliefs, bot._folder, task, "willingness", willingness)
        bot._trustBelief(bot._team_members, bot._trustBeliefs, bot._folder, task, "competence", competence)

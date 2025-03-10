import enum
from agents1.eventUtils import PromptSession, Scenario

class TreeObstacleSession(PromptSession):
    count = 0  # Use to calculate confidence
    def __init__(self, bot, info, ttl=100):
        super().__init__(bot, info, ttl)

    @staticmethod
    def process_trust(bot, info):
        if PromptSession.scenario_used == Scenario.ALWAYS_TRUST:
            return None
        elif PromptSession.scenario_used == Scenario.NEVER_TRUST:
            bot._answered = True
            bot._waiting = False
            bot._send_message('Removing tree blocking ' + str(bot._door['room_name']) + '.',
                              'RescueBot')
            from agents1.OfficialAgent import Phase, RemoveObject
            bot._phase = Phase.ENTER_ROOM
            bot._remove = False

            return RemoveObject.__name__, {'object_id': info['obj_id']}

        VERY_LOW_COMPETENCE_THRESHOLD = -0.6
        VERY_LOW_WILLINGNESS_THRESHOLD = -0.6
        if (bot._trustBeliefs[bot._human_name]['remove_tree']['competence'] < VERY_LOW_COMPETENCE_THRESHOLD or
                bot._trustBeliefs[bot._human_name]['remove_tree']['willingness'] < VERY_LOW_WILLINGNESS_THRESHOLD):
            bot._answered = True
            bot._waiting = False
            bot._send_message('Removing tree blocking ' + str(bot._door['room_name']) + ' due to very low willingness or'
                                                                                        ' competence.',
                              'RescueBot')
            from agents1.OfficialAgent import Phase, RemoveObject
            bot._phase = Phase.ENTER_ROOM
            bot._remove = False

            return RemoveObject.__name__, {'object_id': info['obj_id']}

        LOW_COMPETENCE_THRESHOLD = -0.25
        LOW_WILLINGNESS_THRESHOLD = -0.25

        if bot._trustBeliefs[bot._human_name]['remove_tree']['competence'] > LOW_COMPETENCE_THRESHOLD:
            return None

        if bot._trustBeliefs[bot._human_name]['remove_tree']['willingness'] > LOW_WILLINGNESS_THRESHOLD:
            return None

        # If we have low competence and willingness beliefs for the human, remove the tree immediately
        bot._answered = True
        bot._waiting = False
        bot._send_message('Removing tree blocking ' + str(bot._door['room_name']) + ' due to low willingness and '
                                                                                    'competence.',
                               'RescueBot')
        from agents1.OfficialAgent import Phase, RemoveObject
        bot._phase = Phase.ENTER_ROOM
        bot._remove = False

        return RemoveObject.__name__, {'object_id': info['obj_id']}

    def continue_tree(self):
        print("[Remove Tree] Continuing...")
        if self.scenario_used == Scenario.USE_TRUST_MECHANISM:
            self.increment_values("remove_tree", -0.1, 0, self.bot)
        print("[Remove Tree] Deleting Session")
        self.delete_self()

    def remove_tree(self):
        print("[Remove Tree] Remove Tree heard")
        if self.scenario_used == Scenario.USE_TRUST_MECHANISM:
            self.increment_values("remove_tree", 0.1, 0, self.bot)
        print("[Remove Tree] Deleting Session")
        self.delete_self()

    # Static method for removal when no prompt is generated as the human asked the bot to remove an obstacle
    @staticmethod
    def help_remove_tree(bot):
        print("[Remove Tree] Help Remove Tree heard")
        TreeObstacleSession.increment_values("remove_tree", 0.1, 0, bot)

    def on_timeout(self):
        print("[Remove Tree] Timed out waiting for response!")
        if self.scenario_used == Scenario.USE_TRUST_MECHANISM:
            self.increment_values("remove_tree", -0.15, -0.15, self.bot)

        self.bot._answered = True
        self.bot._waiting = False
        self.bot._send_message('Removing tree blocking ' + str(self.bot._door['room_name']) + '.',
                           'RescueBot')
        from agents1.OfficialAgent import Phase, RemoveObject
        self.bot._phase = Phase.ENTER_ROOM
        self.bot._remove = False

        print("[Remove Tree] Deleting Session")
        self.delete_self()

        return RemoveObject.__name__, {'object_id': self.info['obj_id']}

    @staticmethod
    def increment_values(task, willingness, competence, bot, is_action=True):
        if is_action:
            TreeObstacleSession.count += 1
            bot._trustBelief(bot._team_members, bot._trustBeliefs, bot._folder, task, "count", TreeObstacleSession.count)

        bot._trustBelief(bot._team_members, bot._trustBeliefs, bot._folder, task, "willingness",
                         TreeObstacleSession.get_confidence() * willingness)
        bot._trustBelief(bot._team_members, bot._trustBeliefs, bot._folder, task, "competence",
                         TreeObstacleSession.get_confidence() * competence)

    @staticmethod
    def get_confidence():
        return min(1.0, max(0.0, TreeObstacleSession.count / 35))

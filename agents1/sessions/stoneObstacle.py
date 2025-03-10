import enum
from agents1.eventUtils import PromptSession, Scenario


class StoneObstacleSession(PromptSession):
    count = 0  # Use to calculate confidence
    class StoneObstaclePhase(enum.Enum):
        WAITING_RESPONSE = 0
        WAITING_HUMAN = 1

    def __init__(self, bot, info, ttl=-1):
        super().__init__(bot, info, ttl)
        self.currPhase = self.StoneObstaclePhase.WAITING_RESPONSE
        print("[Remove Stone] Creating Remove Stone Session. Prev prompt:", bot._current_prompt)

    @staticmethod
    def process_trust(bot, info):
        if PromptSession.scenario_used == Scenario.ALWAYS_TRUST:
            return None
        elif PromptSession.scenario_used == Scenario.NEVER_TRUST:
            bot._answered = True
            bot._waiting = False
            bot._send_message('Removing stones blocking ' + str(bot._door['room_name']) + '.',
                              'RescueBot')
            from agents1.OfficialAgent import Phase, RemoveObject
            bot._phase = Phase.ENTER_ROOM
            bot._remove = False

            return RemoveObject.__name__, {'object_id': info['obj_id']}

        VERY_LOW_COMPETENCE_THRESHOLD = -0.6
        VERY_LOW_WILLINGNESS_THRESHOLD = -0.6
        if (bot._trustBeliefs[bot._human_name]['remove_stone']['competence'] < VERY_LOW_COMPETENCE_THRESHOLD or
                bot._trustBeliefs[bot._human_name]['remove_stone']['willingness'] < VERY_LOW_WILLINGNESS_THRESHOLD):
            # If we have low competence and willingness beliefs for the human, remove the stone immediately
            bot._answered = True
            bot._waiting = False
            bot._send_message('Removing stones blocking ' + str(bot._door['room_name']) + ' due to very low competence '
                                                                                          'or willingness.',
                              'RescueBot')
            from agents1.OfficialAgent import Phase, RemoveObject
            bot._phase = Phase.ENTER_ROOM
            bot._remove = False

            return RemoveObject.__name__, {'object_id': info['obj_id']}

        LOW_COMPETENCE_THRESHOLD = -0.25
        LOW_WILLINGNESS_THRESHOLD = -0.25

        if(bot._trustBeliefs[bot._human_name]['remove_stone']['competence'] > LOW_COMPETENCE_THRESHOLD):
            return None

        if (bot._trustBeliefs[bot._human_name]['remove_stone']['willingness'] > LOW_WILLINGNESS_THRESHOLD):
            return None

        # If we have low competence and willingness beliefs for the human, remove the stone immediately
        bot._answered = True
        bot._waiting = False
        bot._send_message('Removing stones blocking ' + str(bot._door['room_name']) + ' due to low competence and '
                                                                                      'willingness.',
                               'RescueBot')
        from agents1.OfficialAgent import Phase, RemoveObject
        bot._phase = Phase.ENTER_ROOM
        bot._remove = False

        return RemoveObject.__name__, {'object_id': info['obj_id']}

    def continue_stone(self):
        print("[Remove Stone] Continuing...")
        if self.scenario_used == Scenario.USE_TRUST_MECHANISM:
            print("[Remove Stone] Updating Beliefs")
            self.increment_values("remove_stone", -0.1, 0, self.bot)
        print("[Remove Stone] Deleting Session")
        self.delete_self()

    def remove_alone(self):
        print("[Remove Stone] Removing Stone Alone")
        if self.scenario_used == Scenario.USE_TRUST_MECHANISM:
            print("[Remove Stone] Updating Beliefs")
            self.increment_values("remove_stone", 0.1, 0, self.bot)
        print("[Remove Stone] Deleting Session")
        self.delete_self()

    def remove_together(self, ttl=400):
        if self.currPhase == self.StoneObstaclePhase.WAITING_HUMAN:
            return self.wait()

        print("[Remove Stone] Remove Together heard")

        if self.scenario_used == Scenario.USE_TRUST_MECHANISM:
            print("[Remove Stone] Updating Beliefs")
            self.increment_values("remove_stone", 0.15, 0, self.bot)
        # Wait for the human
        self.currPhase = self.StoneObstaclePhase.WAITING_HUMAN
        # Reset ttl
        self.ttl = ttl

    # Static method for removal when no prompt is generated as the human asked the bot to remove an obstacle
    @staticmethod
    def help_remove_together(bot, info, ttl=400):
        if not isinstance(bot._current_prompt, StoneObstacleSession):
            print("[Remove Stone] Attaching a new StoneObstacleSession. Prev Prompt:", bot._current_prompt)
            bot._send_message(
                'Please come to ' + str(bot._door['room_name']) + ' to remove stones together.',
                'RescueBot')
            # Attach a new session to the bot
            curr_session = StoneObstacleSession(bot, info, ttl)
            curr_session.currPhase = StoneObstacleSession.StoneObstaclePhase.WAITING_HUMAN
            bot._current_prompt = curr_session
        return bot._current_prompt.wait()

    def complete_remove_together(self):
        print("[Remove Stone] Completed removal!")
        if self.scenario_used == Scenario.USE_TRUST_MECHANISM:
            print("[Remove Stone] Updating Beliefs")
            self.increment_values("remove_stone", 0.1, 0.2, self.bot)
        print("[Remove Stone] Deleting Session")
        self.delete_self()

    def on_timeout(self):
        # Figure out what to do depending on the current phase
        if self.currPhase == self.StoneObstaclePhase.WAITING_RESPONSE:
            print("[Remove Stone] Timed out waiting for response!")
            if self.scenario_used == Scenario.USE_TRUST_MECHANISM:
                print("[Remove Stone] Updating Beliefs")
                self.increment_values("remove_stone", -0.15, -0.15, self.bot)

            self.bot._answered = True
            self.bot._waiting = False
            self.bot._send_message('Removing stones blocking ' + str(self.bot._door['room_name']) + '.',
                               'RescueBot')
            from agents1.OfficialAgent import Phase, RemoveObject
            self.bot._phase = Phase.ENTER_ROOM
            self.bot._remove = False

            print("[Remove Stone] Deleting Session")
            self.delete_self()
            return RemoveObject.__name__, {'object_id': self.info['obj_id']}

        elif self.currPhase == self.StoneObstaclePhase.WAITING_HUMAN:
            print("[Remove Stone] Timed out waiting for human!")
            if self.scenario_used == Scenario.USE_TRUST_MECHANISM:
                print("[Remove Stone] Updating Beliefs")
                self.increment_values("remove_stone", -0.1, 0, self.bot)

            self.bot._answered = True
            self.bot._waiting = False
            self.bot._send_message('Removing stones blocking ' + str(self.bot._door['room_name']) + '.',
                                   'RescueBot')
            from agents1.OfficialAgent import Phase, RemoveObject
            self.bot._phase = Phase.ENTER_ROOM
            self.bot._remove = False

            print("[Remove Stone] Deleting Session")
            self.delete_self()
            return RemoveObject.__name__, {'object_id': self.info['obj_id']}


        else:
            print("How did you even get here?!")
            pass

    
    @staticmethod
    def increment_values(task, willingness, competence, bot, is_action=True):
        if is_action:
            StoneObstacleSession.count += 1
            bot._trustBelief(bot._team_members, bot._trustBeliefs, bot._folder, task, "count", StoneObstacleSession.count)

        bot._trustBelief(bot._team_members, bot._trustBeliefs, bot._folder, task, "willingness",
                         StoneObstacleSession.get_confidence() * willingness)
        bot._trustBelief(bot._team_members, bot._trustBeliefs, bot._folder, task, "competence",
                         StoneObstacleSession.get_confidence() * competence)

    @staticmethod
    def get_confidence():
        return min(1.0, max(0.0, StoneObstacleSession.count / 50))

import enum
from agents1.eventUtils import PromptSession

class HelpRemoveObstacleSession(PromptSession):
    class TrustDecision(enum.Enum):
        LOW_COMPETENCE = 0
        HIGH_COMPETENCE = 1
        
    COMPETENCE_THRESHOLD = -0.3
    
    def __init__(self, bot, info, ttl=100):
        super().__init__(bot, info, ttl)
        self.human_requested_rooms = set()  # Track areas where human asked for help
        self.processed_requests = set()  # Avoid duplicate updates

    def decision_making(self, room_name):
        """
        Decides whether the agent should go help remove an obstacle based on competence.
        - If competence is low, the bot ignores the request.
        - If competence is high, the bot proceeds.
        - If the bot ignored it but later finds an obstacle there, competence increases.
        """
        competence = self.bot._trustBeliefs[self.bot._human_name]['help_remove']['competence']
        self.human_requested_rooms.add(room_name)  # Track skipped locations
        if competence >= self.COMPETENCE_THRESHOLD:
            print(f"[HelpRemove] TRUST: Going to {room_name} (Competence: {competence}, Threshold: {self.COMPETENCE_THRESHOLD})")
            return self.TrustDecision.HIGH_COMPETENCE
        else:
            print(f"[HelpRemove] IGNORE: Skipping {room_name} (Competence: {competence}, Threshold: {self.COMPETENCE_THRESHOLD})")
            return self.TrustDecision.LOW_COMPETENCE


    def decision_to_help(self, state, area):
        if not self.bot._carrying:
            # Identify at which location the human needs help
            self.bot._door = state.get_room_doors(area)[0]
            self.bot._doormat = state.get_room(area)[-1]['doormat']
            if area in self.bot._searched_rooms:
                # indicate that the human lied about searching the area
                # or that the human is not competent enough to find the obstacle?(tricking the bot)
                self.bot._searched_rooms.remove(area)
                #TODO: try to run this code and see if it penalizes the search competence
            # Clear received messages (bug fix)
            self.bot.received_messages = []
            self.bot.received_messages_content = []
            self.bot._moving = True
            self.bot._remove = True
            if self.bot._waiting and self.bot._recent_vic:
                self.bot._todo.append(self.bot._recent_vic)
            self.bot._waiting = False
            # Let the human know that the agent is coming over to help
            self.bot._send_message(
                'Moving to ' + str(self.bot._door['room_name']) + ' to help you remove an obstacle.',
                'RescueBot')
            # Plan the path to the relevant area
            from agents1.OfficialAgent import Phase
            
            self.bot._phase = Phase.PLAN_PATH_TO_ROOM
        # Come over to help after dropping a victim that is currently being carried by the agent
        else:
            area = 'area ' + msg.split()[-1]
            self.bot._send_message('Will come to ' + area + ' after dropping ' + self.bot._goal_vic + '.',
                                'RescueBot')

    
    def penalize_help_remove_willingness_already_searched(self, area, use_confidence=False):
        """
        Penalize the agent's willingness to help remove an obstacle if it sends areas that the human has already
        claimed to have searched.
        """
        increment = self.calculate_increment_with_confidence(self.bot._number_of_actions_help_remove, -0.1) if use_confidence else -0.1
        self.increment_values("help_remove", increment, 0, self.bot)
        print(f"[HelpRemove] Penalizing willingness for sending areas already searched ({area}): {increment:.2f}")
        

    def verify_human_request(self, room_name, obstacle_found, use_confidence=False):
        """
        If the bot previously ignored a help request but later finds an obstacle there,
        it realizes the human was correct and increases competence.
        """
        if room_name in self.human_requested_rooms and room_name not in self.processed_requests:
            self.processed_requests.add(room_name)  # Prevent multiple updates
            reward = self.calculate_increment_with_confidence(self.bot._number_of_actions_help_remove, 0.1) if use_confidence else 0.1
            if obstacle_found:
                print(f"[HelpRemove] Competence INCREASE: Obstacle found in {room_name}, human was truthful.")
                self.increment_values("help_remove", 0, reward, self.bot)  # Increase competence
            else:
                print(f"[HelpRemove] Competence DECREASE: No obstacle in {room_name}, human was incorrect.")
                self.increment_values("help_remove", 0, -reward, self.bot)  # Decrease competence


    def update_help_remove_willingness(self, use_confidence=False):
        """
        Update the willingness to help remove an obstacle after the agent sends a room to the human.
        """
        base_increment = self.compute_help_remove_willingness_update(len(self.bot._help_remove_rooms_current_round), len(self.bot._searched_rooms_by_agent))
        increment = self.calculate_increment_with_confidence(self.bot._number_of_actions_help_remove, base_increment) if use_confidence else base_increment
        new_update = self.bot._help_remove_willingness_start_value + increment
        self.bot._trustBeliefs[self.bot._human_name]['help_remove']['willingness'] = new_update
        print(f"[HelpRemove] Willingness updated to: {new_update:.2f}")

    def compute_help_remove_willingness_update(self, X, Z):
        """
        X = num_help_remove_rooms_current_round
        Z = num_searched_rooms_by_agent
        Compute the willingness update based on the number of areas the human asked for help and the number of areas the agent actually searched.
        """
        if Z == 14:
            return 0
        percentage = X / (14 - Z)
        return (percentage - 0.6) * X / 2
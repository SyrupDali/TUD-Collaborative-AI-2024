import sys, random, enum, ast, time, csv
import numpy as np
from matrx import grid_world
from brains1.ArtificialBrain import ArtificialBrain
from actions1.CustomActions import *
from matrx import utils
from matrx.grid_world import GridWorld
from matrx.agents.agent_utils.state import State
from matrx.agents.agent_utils.navigator import Navigator
from matrx.agents.agent_utils.state_tracker import StateTracker
from matrx.actions.door_actions import OpenDoorAction
from matrx.actions.object_actions import GrabObject, DropObject, RemoveObject
from matrx.actions.move_actions import MoveNorth
from matrx.messages.message import Message
from matrx.messages.message_manager import MessageManager
from actions1.CustomActions import RemoveObjectTogether, CarryObjectTogether, DropObjectTogether, CarryObject, Drop
from agents1.sessions.HelpRemoveObstacle import HelpRemoveObstacleSession
from agents1.sessions.stoneObstacle import StoneObstacleSession
from agents1.sessions.yellowVictim import YellowVictimSession
from agents1.sessions.treeObstacle import TreeObstacleSession
from agents1.sessions.RockObstacle import RockObstacleSession
from agents1.sessions.RedVictim import RedVictimSession
from agents1.eventUtils import PromptSession, Scenario
from agents1.searchTrustLogic import (
    update_search_willingness,
    penalize_search_willingness_for_sending_rooms_already_searched,
    penalize_search_competence_for_claimed_searched_room_with_obstacle,
    penalize_search_competence_for_claimed_searched_room_with_victim,
    reward_search_competence_for_claimed_searched_room,
    add_room_based_on_trust
)

class Phase(enum.Enum):
    INTRO = 1,
    FIND_NEXT_GOAL = 2,
    PICK_UNSEARCHED_ROOM = 3,
    PLAN_PATH_TO_ROOM = 4,
    FOLLOW_PATH_TO_ROOM = 5,
    PLAN_ROOM_SEARCH_PATH = 6,
    FOLLOW_ROOM_SEARCH_PATH = 7,
    PLAN_PATH_TO_VICTIM = 8,
    FOLLOW_PATH_TO_VICTIM = 9,
    TAKE_VICTIM = 10,
    PLAN_PATH_TO_DROPPOINT = 11,
    FOLLOW_PATH_TO_DROPPOINT = 12,
    DROP_VICTIM = 13,
    WAIT_FOR_HUMAN = 14,
    WAIT_AT_ZONE = 15,
    FIX_ORDER_GRAB = 16,
    FIX_ORDER_DROP = 17,
    REMOVE_OBSTACLE_IF_NEEDED = 18,
    ENTER_ROOM = 19


class BaselineAgent(ArtificialBrain):
    def __init__(self, slowdown, condition, name, folder):
        super().__init__(slowdown, condition, name, folder)
        # Initialization of some relevant variables
        self._tick = None
        self._slowdown = slowdown
        self._condition = condition
        self._human_name = name
        self._folder = folder
        self._phase = Phase.INTRO
        self._room_vics = []
        self._searched_rooms = [] # Contains the searched rooms by the agent and human(based on the beliefs lvl)
        self._searched_rooms_by_agent = [] # Contains the searched rooms by the agent, so 100% sure
        self._searched_rooms_claimed_by_human = [] # record the rooms claimed searched by the human
        self._help_remove_rooms_current_round = [] # Contains the rooms where the agent found obstacles
        self._found_victims = []
        self._collected_victims = []
        self._found_victim_logs = {}
        self._send_messages = []
        self._current_door = None
        self._team_members = []
        self._carrying_together = False
        self._remove = False
        self._goal_vic = None
        self._goal_loc = None
        self._human_loc = None
        self._distance_human = None
        self._distance_drop = None
        self._agent_loc = None
        self._todo = []
        self._answered = False
        self._to_search = []
        self._carrying = False
        self._waiting = False
        self._rescue = None
        self._recent_vic = None
        self._received_messages = []
        self._consumed_messages = set() # we only want to consume each message once (to add searched rooms with probability)
        self._moving = False
        self._remainingZones = []
        self._trustBeliefs = None # only load the trust beliefs once
        self._re_searching = False # once it becomes true, competence penalty is applied when the agent found obstacles or victims
        self._not_penalizable = [] # contains the areas searched by agent and where the agent found obstacles or victims
        self._door = {'room_name': None, 'location': None}
        self._all_room_tiles = None
        self._search_willingness_start_value = None
        self._search_competence_start_value = None
        self._help_remove_willingness_start_value = None
        
        # Used for managing prompts
        self._current_prompt = None
        self._stop_finding_next_goal = False

        # Used when Searching for Victims
        self._number_of_actions_search = 0

        # Used when Rescuing Yellow Victims
        self._yellow_victim_session = None
        self._claimed_collected_victims = []
        self._yellow_victim_processed_messages = set()

        # Used when Rescuing Yellow Victims
        self._red_victim_session = None
        self._number_of_red_victims_saved = 0

        # Used when Removing Obstacles
        self._number_of_actions_help_remove = 0
        self._help_remove_obstacle_session = HelpRemoveObstacleSession(self, None, 100)

        # Keep track of obstacles we've decided to skip
        self._skipped_obstacles = []

    def initialize(self):
        # Initialization of the state tracker and navigation algorithm
        self._state_tracker = StateTracker(agent_id=self.agent_id)
        self._navigator = Navigator(agent_id=self.agent_id, action_set=self.action_set,
                                    algorithm=Navigator.A_STAR_ALGORITHM)
        # Initialization of the tasks the agent can perform
        self._tasks = ['search', 'rescue_yellow', 'rescue_red', 
                       'remove_rock', 'remove_stone', 'remove_tree', 'help_remove']
        
    def filter_observations(self, state):
        # Filtering of the world state before deciding on an action 
        return state

    def decide_on_actions(self, state):
        # Store the location of tiles in all the rooms
        if self._all_room_tiles == None:
            self._all_room_tiles = [info['location'] for info in state.values()
                            if 'class_inheritance' in info
                            and 'AreaTile' in info['class_inheritance']
                            and 'room_name' in info]
        # Identify team members
        agent_name = state[self.agent_id]['obj_id']
        for member in state['World']['team_members']:
            if member != agent_name and member not in self._team_members:
                self._team_members.append(member)
        # Create a list of received messages from the human team member
        for mssg in self.received_messages:
            for member in self._team_members:
                if mssg.from_id == member and mssg.content not in self._received_messages:
                    
                    self._received_messages.append(mssg.content)

        # Process messages from team members
        self._process_messages(state, self._team_members, self._condition)

        # Initialize and update trust beliefs for team members
        if self._trustBeliefs == None:
            self._trustBeliefs = self._loadBelief(self._team_members, self._folder)
            self._search_willingness_start_value = self._trustBeliefs[self._human_name]['search']['willingness']
            self._search_competence_start_value = self._trustBeliefs[self._human_name]['search']['competence'] # We will use this value to compute the probability of adding searched rooms
            self._help_remove_willingness_start_value = self._trustBeliefs[self._human_name]['help_remove']['willingness']
            
        # Initialize random values for each task if the random baseline is used
        if PromptSession.scenario_used == Scenario.RANDOM_TRUST:
            for task in self._tasks:
                self._trustBeliefs[self._human_name][task]['competence'] = np.random.uniform(-1, 1)
                self._trustBeliefs[self._human_name][task]['willingness'] = np.random.uniform(-1, 1)
                self._search_willingness_start_value = np.random.uniform(-1, 1)
                self._search_competence_start_value = np.random.uniform(-1, 1)
                self._help_remove_willingness_start_value = np.random.uniform(-1, 1)
        elif PromptSession.scenario_used == Scenario.NEVER_TRUST:
            for task in self._tasks:
                self._trustBeliefs[self._human_name][task]['competence'] = -1
                self._trustBeliefs[self._human_name][task]['willingness'] = -1
                self._search_willingness_start_value = -1
                self._search_competence_start_value = -1
                self._help_remove_willingness_start_value = -1
        elif PromptSession.scenario_used == Scenario.ALWAYS_TRUST:
            for task in self._tasks:
                self._trustBeliefs[self._human_name][task]['competence'] = 1
                self._trustBeliefs[self._human_name][task]['willingness'] = 1
                self._search_willingness_start_value = 1
                self._search_competence_start_value = 1
                self._help_remove_willingness_start_value = 1

        # Check whether human is close in distance
        if state[{'is_human_agent': True}]:
            self._distance_human = 'close'
        if not state[{'is_human_agent': True}]:
            # Define distance between human and agent based on last known area locations
            if self._agent_loc in [1, 2, 3, 4, 5, 6, 7] and self._human_loc in [8, 9, 10, 11, 12, 13, 14]:
                self._distance_human = 'far'
            if self._agent_loc in [1, 2, 3, 4, 5, 6, 7] and self._human_loc in [1, 2, 3, 4, 5, 6, 7]:
                self._distance_human = 'close'
            if self._agent_loc in [8, 9, 10, 11, 12, 13, 14] and self._human_loc in [1, 2, 3, 4, 5, 6, 7]:
                self._distance_human = 'far'
            if self._agent_loc in [8, 9, 10, 11, 12, 13, 14] and self._human_loc in [8, 9, 10, 11, 12, 13, 14]:
                self._distance_human = 'close'

        # Define distance to drop zone based on last known area location
        if self._agent_loc in [1, 2, 5, 6, 8, 9, 11, 12]:
            self._distance_drop = 'far'
        if self._agent_loc in [3, 4, 7, 10, 13, 14]:
            self._distance_drop = 'close'

        # Check whether victims are currently being carried together by human and agent
        for info in state.values():
            if 'is_human_agent' in info and self._human_name in info['name'] and len(
                    info['is_carrying']) > 0 and 'critical' in info['is_carrying'][0]['obj_id'] or \
                    'is_human_agent' in info and self._human_name in info['name'] and len(
                info['is_carrying']) > 0 and 'mild' in info['is_carrying'][0][
                'obj_id'] and self._rescue == 'together' and not self._moving:

                # Human Showed Up
                if 'mild' in info['is_carrying'][0]['obj_id']:
                    if isinstance(self._yellow_victim_session, PromptSession):
                        self._yellow_victim_session.delete_yellow_victim_session()

                if 'critical' in info['is_carrying'][0]['obj_id']:
                    if isinstance(self._red_victim_session, PromptSession):
                        self._number_of_red_victims_saved += 1
                        self._red_victim_session.delete_red_victim_session()

                # If victim is being carried, add to collected victims memory
                if info['is_carrying'][0]['img_name'][8:-4] not in self._collected_victims:
                    self._collected_victims.append(info['is_carrying'][0]['img_name'][8:-4])

                self._carrying_together = True


            if 'is_human_agent' in info and self._human_name in info['name'] and len(info['is_carrying']) == 0:
                self._carrying_together = False

        # If carrying a victim together, let agent be idle (because joint actions are essentially carried out by the human)
        if self._carrying_together == True:
            return None, {}

        # Send the hidden score message for displaying and logging the score during the task, DO NOT REMOVE THIS
        self._send_message('Our score is ' + str(state['rescuebot']['score']) + '.', 'RescueBot')

        # Ongoing loop until the task is terminated, using different phases for defining the agent's behavior
        while True:
            if Phase.INTRO == self._phase:
                # Send introduction message
                self._send_message('Hello! My name is RescueBot. Together we will collaborate and try to search and rescue the 8 victims on our right as quickly as possible. \
                Each critical victim (critically injured girl/critically injured elderly woman/critically injured man/critically injured dog) adds 6 points to our score, \
                each mild victim (mildly injured boy/mildly injured elderly man/mildly injured woman/mildly injured cat) 3 points. \
                If you are ready to begin our mission, you can simply start moving.', 'RescueBot')
                # Wait untill the human starts moving before going to the next phase, otherwise remain idle
                if not state[{'is_human_agent': True}]:
                    self._phase = Phase.FIND_NEXT_GOAL
                else:
                    return None, {}



            if Phase.FIND_NEXT_GOAL == self._phase:
                # Definition of some relevant variables
                self._answered = False
                self._goal_vic = None
                self._goal_loc = None
                self._rescue = None
                self._moving = True
                remaining_zones = []
                remaining_vics = []
                remaining = {}
                # Identification of the location of the drop zones
                zones = self._get_drop_zones(state)
                # Identification of which victims still(!) need to be rescued and on which location they should be dropped
                for info in zones:
                    if str(info['img_name'])[8:-4] not in self._collected_victims:
                        remaining_zones.append(info)
                        remaining_vics.append(str(info['img_name'])[8:-4])
                        remaining[str(info['img_name'])[8:-4]] = info['location']
                if remaining_zones:
                    self._remainingZones = remaining_zones
                    self._remaining = remaining
                # Remain idle if there are no victims left to rescue
                if not remaining_zones:
                    return None, {}

                # Check which victims can be rescued next because human or agent already found them
                for vic in remaining_vics:
                    # Define a previously found victim as target victim because all areas have been searched
                    if vic in self._found_victims and vic in self._todo and len(self._searched_rooms) == 0:
                        self._goal_vic = vic
                        self._goal_loc = remaining[vic]
                        # Move to target victim
                        self._rescue = 'together'
                        self._send_message('Moving to ' + self._found_victim_logs[vic][
                            'room'] + ' to pick up ' + self._goal_vic + '. Please come there as well to help me carry ' + self._goal_vic + ' to the drop zone.',
                                          'RescueBot')
                        
                        # Verified: this is reached when ALL the areas have been searched, but NOT all the victims
                        
                        # Plan path to victim because the exact location is known (i.e., the agent found this victim)
                        if 'location' in self._found_victim_logs[vic].keys():
                            
                            # This code is reached when the human says "Continue" when the robot finds a victim
                            print("Code Location 0")
                                
                            self._phase = Phase.PLAN_PATH_TO_VICTIM
                            return Idle.__name__, {'duration_in_ticks': 25}
                        
                        
                        # Plan path to area because the exact victim location is not known, only the area (i.e., human found this victim)
                        if 'location' not in self._found_victim_logs[vic].keys():
                            
                            print("Code Location 1")
                            # This is reached when human does "Found victim X in area Y" but does NOT pick up
                            # if 'mild' in vic:
                            
                            self._phase = Phase.PLAN_PATH_TO_ROOM
                            return Idle.__name__, {'duration_in_ticks': 25}
                    
                        
                    # Define a previously found victim as target victim
                    if vic in self._found_victims and vic not in self._todo:
                        self._goal_vic = vic
                        self._goal_loc = remaining[vic]
                        
                        # Rescue together when victim is critical or when the human is weak and the victim is mildly injured
                        if 'critical' in vic or 'mild' in vic and self._condition == 'weak':
                            self._rescue = 'together'
                            print("Code Location 2")
                        
                        # Rescue alone if the victim is mildly injured and the human not weak
                        if 'mild' in vic and self._condition != 'weak':
                            self._rescue = 'alone'
                            print("Code Location 3")
                        
                        # Plan path to victim because the exact location is known (i.e., the agent found this victim)
                        if 'location' in self._found_victim_logs[vic].keys():
                            self._phase = Phase.PLAN_PATH_TO_VICTIM
                            return Idle.__name__, {'duration_in_ticks': 25}
                        
                        # Plan path to area because the exact victim location is not known, only the area (i.e., human found this  victim)
                        if 'location' not in self._found_victim_logs[vic].keys():
                            print("Code Location 4")
                            self._phase = Phase.PLAN_PATH_TO_ROOM
                            return Idle.__name__, {'duration_in_ticks': 25}
                        
                    # If there are no target victims found, visit an unsearched area to search for victims
                    if vic not in self._found_victims or vic in self._found_victims and vic in self._todo and len(
                            self._searched_rooms) > 0:
                        self._phase = Phase.PICK_UNSEARCHED_ROOM




            if Phase.PICK_UNSEARCHED_ROOM == self._phase:
                agent_location = state[self.agent_id]['location']
                # Identify which areas are not explored yet
                unsearched_rooms = [room['room_name'] for room in state.values()
                                   if 'class_inheritance' in room
                                   and 'Door' in room['class_inheritance']
                                   and room['room_name'] not in self._searched_rooms
                                   and room['room_name'] not in self._to_search]
                # If all areas have been searched but the task is not finished, start searching areas again
                if self._remainingZones and len(unsearched_rooms) == 0:
                    print("All areas have been searched, starting re-searching.")

                    self._to_search = []
                    self._searched_rooms = list(self._searched_rooms_by_agent) # Reset the searched rooms(only includes the ones searched by the agent)
                    self._searched_rooms_claimed_by_human = [] # Reset the searched rooms claimed by the human
                    self._searched_rooms_by_agent = [] # Reset the searched rooms by the agent, only store the new ones in the next searching round
                    self._help_remove_rooms_current_round = [] # Reset the rooms where the agent found obstacles
                    self._send_messages = []
                    self.received_messages = []
                    self.received_messages_content = []
                    self._consumed_messages = set()
                    self._re_searching = True
                    self._not_penalizable = list(self._searched_rooms) # Reset to agent searched rooms
                    self._search_willingness_start_value = self._trustBeliefs[self._human_name]['search']['willingness'] # Reset the start value for next search round
                    self._search_competence_start_value = self._trustBeliefs[self._human_name]['search']['competence']
                    self._help_remove_willingness_start_value = self._trustBeliefs[self._human_name]['help_remove']['willingness']
                    self._send_message('Going to re-search all areas.', 'RescueBot')
                    self._phase = Phase.FIND_NEXT_GOAL
                # If there are still areas to search, define which one to search next
                else:
                    # Identify the closest door when the agent did not search any areas yet
                    if self._current_door == None:
                        # Find all area entrance locations
                        self._door = state.get_room_doors(self._getClosestRoom(state, unsearched_rooms, agent_location))[
                            0]
                        self._doormat = \
                            state.get_room(self._getClosestRoom(state, unsearched_rooms, agent_location))[-1]['doormat']
                        # Workaround for one area because of some bug
                        if self._door['room_name'] == 'area 1':
                            self._doormat = (3, 5)
                        # Plan path to area
                        self._phase = Phase.PLAN_PATH_TO_ROOM
                    # Identify the closest door when the agent just searched another area
                    if self._current_door != None:
                        self._door = \
                            state.get_room_doors(self._getClosestRoom(state, unsearched_rooms, self._current_door))[0]
                        self._doormat = \
                            state.get_room(self._getClosestRoom(state, unsearched_rooms, self._current_door))[-1][
                                'doormat']
                        if self._door['room_name'] == 'area 1':
                            self._doormat = (3, 5)
                        self._phase = Phase.PLAN_PATH_TO_ROOM




            if Phase.PLAN_PATH_TO_ROOM == self._phase:
                # Reset the navigator for a new path planning
                self._navigator.reset_full()

                # Check if there is a goal victim, and it has been found, but its location is not known
                if self._goal_vic \
                        and self._goal_vic in self._found_victims \
                        and 'location' not in self._found_victim_logs[self._goal_vic].keys():
                    # Retrieve the victim's room location and related information
                    victim_location = self._found_victim_logs[self._goal_vic]['room']
                    self._door = state.get_room_doors(victim_location)[0]
                    self._doormat = state.get_room(victim_location)[-1]['doormat']

                    # Handle special case for 'area 1'
                    if self._door['room_name'] == 'area 1':
                        self._doormat = (3, 5)

                    # Set the door location based on the doormat
                    doorLoc = self._doormat

                # If the goal victim's location is known, plan the route to the identified area
                else:
                    if self._door['room_name'] == 'area 1':
                        self._doormat = (3, 5)
                    doorLoc = self._doormat

                # Add the door location as a waypoint for navigation
                self._navigator.add_waypoints([doorLoc])
                # Follow the route to the next area to search
                self._phase = Phase.FOLLOW_PATH_TO_ROOM



            if Phase.FOLLOW_PATH_TO_ROOM == self._phase:
                # Check if the previously identified target victim was rescued by the human
                if self._goal_vic and self._goal_vic in self._collected_victims:
                    # Reset current door and switch to finding the next goal
                    self._current_door = None
                    self._phase = Phase.FIND_NEXT_GOAL

                # Check if the human found the previously identified target victim in a different room
                if self._goal_vic \
                        and self._goal_vic in self._found_victims \
                        and self._door['room_name'] != self._found_victim_logs[self._goal_vic]['room']:
                    self._current_door = None
                    self._phase = Phase.FIND_NEXT_GOAL

                # Check if the human already searched the previously identified area without finding the target victim
                if self._door['room_name'] in self._searched_rooms and self._goal_vic not in self._found_victims:
                    self._current_door = None
                    self._phase = Phase.FIND_NEXT_GOAL

                # Move to the next area to search
                else:
                    # Update the state tracker with the current state
                    self._state_tracker.update(state)

                    # Explain why the agent is moving to the specific area, either:
                    # [-] it contains the current target victim
                    # [-] it is the closest un-searched area
                    if self._goal_vic in self._found_victims \
                            and str(self._door['room_name']) == self._found_victim_logs[self._goal_vic]['room'] \
                            and not self._remove:
                        if self._condition == 'weak':
                            self._send_message('Moving to ' + str(
                                self._door['room_name']) + ' to pick up ' + self._goal_vic + ' together with you.',
                                              'RescueBot')
                        else:
                            self._send_message(
                                'Moving to ' + str(self._door['room_name']) + ' to pick up ' + self._goal_vic + '.',
                                'RescueBot')

                    if self._goal_vic not in self._found_victims and not self._remove or not self._goal_vic and not self._remove:
                        self._send_message(
                            'Moving to ' + str(self._door['room_name']) + ' because it is the closest unsearched area.',
                            'RescueBot')

                    # Set the current door based on the current location
                    self._current_door = self._door['location']

                    # Retrieve move actions to execute
                    action = self._navigator.get_move_action(self._state_tracker)
                    # Check for obstacles blocking the path to the area and handle them if needed
                    if action is not None:
                        # Remove obstacles blocking the path to the area 
                        for info in state.values():
                            if 'class_inheritance' in info and 'ObstacleObject' in info[
                                'class_inheritance'] and 'stone' in info['obj_id'] and info['location'] not in [(9, 4),
                                                                                                                (9, 7),
                                                                                                                (9, 19),
                                                                                                                (21,
                                                                                                                 19)]:
                                self._send_message('Reaching ' + str(self._door['room_name'])
                                                   + ' will take a bit longer because I found stones blocking my path.',
                                                   'RescueBot')
                                return RemoveObject.__name__, {'object_id': info['obj_id']}
                        return action, {}
                    # Identify and remove obstacles if they are blocking the entrance of the area
                    self._phase = Phase.REMOVE_OBSTACLE_IF_NEEDED




            if Phase.REMOVE_OBSTACLE_IF_NEEDED == self._phase:
                objects = []
                agent_location = state[self.agent_id]['location']
                # Identify which obstacle is blocking the entrance
                for info in state.values():
                    if 'class_inheritance' in info and 'ObstacleObject' in info['class_inheritance'] and 'rock' in info['obj_id']:

                        objects.append(info)
                        # Competence Update: Decrease trust in human if bot found obstacles at the entrance of the claimed searched area
                        if (self._re_searching or self._door['room_name'] in self._searched_rooms_claimed_by_human) and self._door['room_name'] not in self._not_penalizable:
                            penalize_search_competence_for_claimed_searched_room_with_obstacle(self, 'rock', use_confidence=True)
                        # verify if the room is blocked by an obstacle
                        if self._remove and not self._waiting:
                            self._help_remove_obstacle_session.verify_human_request(self._door['room_name'], True, use_confidence=True)
                        # Communicate which obstacle is blocking the entrance
                        if self._answered == False and not self._remove and not self._waiting:
                            self._send_message('Found rock blocking ' + str(self._door['room_name']) + '. Please decide whether to "Remove" or "Continue" searching. \n \n \
                                Important features to consider are: \n safe - victims (claimed to be) rescued: ' + str(
                                set(self._collected_victims) | set(self._claimed_collected_victims)) + ' \n explore - areas searched: area ' + str(
                                self._searched_rooms).replace('area ', '') + ' \
                                \n clock - removal time: 5 seconds \n afstand - distance between us: ' + self._distance_human,
                                              'RescueBot')
                            self._waiting = True
                            # Initialize the rock obstacle session with a 200 tick timeout (roughly 20 seconds)
                            self._rock_obstacle_session = RockObstacleSession(self, info, 200)
                            self._current_prompt = self._rock_obstacle_session
                        
                        # If the human says "Continue" and we're not in forced removal mode, skip this obstacle
                        if self.received_messages_content \
                        and self.received_messages_content[-1] == 'Continue' \
                        and not self._remove:
                            if isinstance(self._current_prompt, RockObstacleSession):
                                self._current_prompt.continue_rock()
                            self._answered = True
                            self._waiting = False
                            self._skipped_obstacles.append(info['obj_id'])
                            self._to_search.append(self._door['room_name'])
                            self._phase = Phase.FIND_NEXT_GOAL

                        # Wait for the human to help removing the obstacle and remove the obstacle together
                        if self.received_messages_content and self.received_messages_content[
                            -1] == 'Remove' or self._remove:
                            self._help_remove_obstacle_session.verify_human_request(self._door['room_name'], True, use_confidence=True)
                            if not self._remove:
                                self._answered = True
                                self._remove = True
                                if isinstance(self._current_prompt, RockObstacleSession):
                                    self._current_prompt.remove_rock()
                                else:
                                    # If for some reason the session is missing, create it
                                    self._rock_obstacle_session = RockObstacleSession(self, info, 200)
                                    self._current_prompt = self._rock_obstacle_session
                                    self._current_prompt.remove_rock()
                        
                        # Handle the removal process
                        if self._remove:
                            # Check if the human has arrived
                            if state[{'is_human_agent': True}]:
                                # Human is here, tell them to press D to remove the rock
                                self._send_message(
                                    'Thank you for coming to help! Press D to remove the big rock blocking ' + str(self._door['room_name']) + '.',
                                    'RescueBot'
                                )
                                # Let the game handle the actual removal when player presses D
                                return None, {}
                            else:
                                # Still waiting for human to arrive
                                # The session's wait() method will handle timeout if the human doesn't arrive
                                if isinstance(self._current_prompt, PromptSession):
                                    result = self._current_prompt.wait()
                                    if result:
                                        return result
                                return None, {}
                        
                        # If none of the above triggered, we are likely waiting for a response
                        else:
                            # Let the prompt session handle waiting, timeouts, etc.
                            if isinstance(self._current_prompt, PromptSession):
                                result = self._current_prompt.wait()
                                if result:
                                    return result
                            return None, {}

                    if 'class_inheritance' in info and 'ObstacleObject' in info['class_inheritance'] and 'tree' in info[
                        'obj_id']:
                        objects.append(info)

                        # Competence Update: Decrease trust in human if bot found obstacles at the entrance of the claimed searched area
                        if (self._re_searching or self._door['room_name'] in self._searched_rooms_claimed_by_human) and self._door['room_name'] not in self._not_penalizable:
                            penalize_search_competence_for_claimed_searched_room_with_obstacle(self, 'tree', use_confidence=True)

                        # verify if the room is blocked by an obstacle
                        if self._remove and not self._waiting:
                            self._help_remove_obstacle_session.verify_human_request(self._door['room_name'], True, use_confidence=True)
                        # Communicate which obstacle is blocking the entrance
                        if self._answered == False and not self._remove and not self._waiting:

                            # Trust Check
                            decision = TreeObstacleSession.process_trust(self, info)
                            # If decision is None, we trust the human and generate the prompt
                            if decision is not None:
                                return decision
                            self._send_message('Found tree blocking  ' + str(self._door['room_name']) + '. Please decide whether to "Remove" or "Continue" searching. \n \n \
                                Important features to consider are: \n safe - victims (claimed to be) rescued: ' + str(
                                set(self._collected_victims) | set(self._claimed_collected_victims)) + '\n explore - areas searched: area ' + str(
                                self._searched_rooms).replace('area ', '') + ' \
                                \n clock - removal time: 10 seconds', 'RescueBot')
                            self._waiting = True

                            self._current_prompt = TreeObstacleSession(self, info, 20)

                        # Determine the next area to explore if the human tells the agent not to remove the obstacle
                        if self.received_messages_content and self.received_messages_content[
                            -1] == 'Continue' and not self._remove:
                            self._current_prompt.continue_tree()

                            self._answered = True
                            self._waiting = False
                            # Add area to the to do list
                            self._to_search.append(self._door['room_name'])
                            self._phase = Phase.FIND_NEXT_GOAL
                        # Remove the obstacle if the human tells the agent to do so
                        if self.received_messages_content and self.received_messages_content[
                            -1] == 'Remove' or self._remove:

                            if not self._remove:
                                self._current_prompt.remove_tree()
                                self._answered = True
                                self._waiting = False
                                self._send_message('Removing tree blocking ' + str(self._door['room_name']) + '.',
                                                  'RescueBot')
                            if self._remove:
                                TreeObstacleSession.help_remove_tree(self)
                                self._send_message('Removing tree blocking ' + str(
                                    self._door['room_name']) + ' because you asked me to.', 'RescueBot')
                            self._phase = Phase.ENTER_ROOM
                            self._remove = False
                            return RemoveObject.__name__, {'object_id': info['obj_id']}
                        # Remain idle untill the human communicates what to do with the identified obstacle
                        else:
                            if isinstance(self._current_prompt, PromptSession):
                                return self._current_prompt.wait()
                            return None, {}

                    if 'class_inheritance' in info and 'ObstacleObject' in info['class_inheritance'] and 'stone' in \
                            info['obj_id']:
                        objects.append(info)
                        # Competence Update: Decrease trust in human if bot found obstacles at the entrance of the claimed searched area
                        if (self._re_searching or self._door['room_name'] in self._searched_rooms_claimed_by_human) and self._door['room_name'] not in self._not_penalizable:
                            penalize_search_competence_for_claimed_searched_room_with_obstacle(self, 'stone', use_confidence=True)
                        # verify if the room is blocked by an obstacle
                        if self._remove and not self._waiting:
                            self._help_remove_obstacle_session.verify_human_request(self._door['room_name'], True, use_confidence=True)
                        # Communicate which obstacle is blocking the entrance
                        if self._answered == False and not self._remove and not self._waiting:

                            # Trust Check
                            decision = StoneObstacleSession.process_trust(self, info)
                            # If decision is None, we trust the human and generate the prompt
                            if decision is not None:
                                return decision
                            self._send_message('Found stones blocking  ' + str(self._door['room_name']) + '. Please decide whether to "Remove together", "Remove alone", or "Continue" searching. \n \n \
                                Important features to consider are: \n safe - victims (claimed to be) rescued: ' + str(
                                set(self._collected_victims) | set(self._claimed_collected_victims)) + ' \n explore - areas searched: area ' + str(
                                self._searched_rooms).replace('area', '') + ' \
                                \n clock - removal time together: 3 seconds \n afstand - distance between us: ' + self._distance_human + '\n clock - removal time alone: 20 seconds',
                                              'RescueBot')
                            self._waiting = True

                            self._current_prompt = StoneObstacleSession(self, info, 100)

                        # Determine the next area to explore if the human tells the agent not to remove the obstacle          
                        if self.received_messages_content and self.received_messages_content[
                            -1] == 'Continue' and not self._remove:
                            self._current_prompt.continue_stone()

                            self._answered = True
                            self._waiting = False
                            # Add area to the to do list
                            self._to_search.append(self._door['room_name'])
                            self._phase = Phase.FIND_NEXT_GOAL


                        # Remove the obstacle alone if the human decides so
                        if self.received_messages_content and self.received_messages_content[
                            -1] == 'Remove alone' and not self._remove:
                            self._answered = True
                            self._waiting = False
                            self._send_message('Removing stones blocking ' + str(self._door['room_name']) + '.',
                                              'RescueBot')
                            self._phase = Phase.ENTER_ROOM
                            self._remove = False

                            self._current_prompt.remove_alone()

                            return RemoveObject.__name__, {'object_id': info['obj_id']}

                        # Remove the obstacle together if the human decides so
                        if self.received_messages_content and self.received_messages_content[
                            -1] == 'Remove together' or self._remove:
                            self._help_remove_obstacle_session.verify_human_request(self._door['room_name'], True, use_confidence=True)
                            if not self._remove:
                                self._answered = True
                            # Tell the human to come over and be idle until human arrives
                            if not state[{'is_human_agent': True}]:
                                tmp = StoneObstacleSession.help_remove_together(self, info)
                                if tmp is not None:
                                    return tmp

                                self._send_message(
                                    'Please come to ' + str(self._door['room_name']) + ' to remove stones together.',
                                    'RescueBot')
                                return None, {}
                            # Tell the human to remove the obstacle when he/she arrives
                            if state[{'is_human_agent': True}]:
                                if not isinstance(self._current_prompt, StoneObstacleSession):
                                    self._send_message(
                                        'Lets remove stones blocking ' + str(self._door['room_name']) + '!',
                                        'RescueBot')
                                    tmp = StoneObstacleSession.help_remove_together(self, info)
                                    if tmp is not None:
                                        return tmp

                                # If a help remove was called earlier and the bot is now stuck here
                                tmp = self._current_prompt.remove_together()
                                if tmp is not None:
                                    return tmp

                                return None, {}
                        # Remain idle until the human communicates what to do with the identified obstacle
                        else:
                            if isinstance(self._current_prompt, PromptSession):
                                return self._current_prompt.wait()
                            return None, {}
                        
                    if 'class_inheritance' in info and 'ObstacleObject' in info['class_inheritance'] and 'rock' in info['obj_id']:
                        objects.append(info)
                        # verify if the room is blocked by an obstacle
                        if self._remove and not self._waiting:
                            self._help_remove_obstacle_session.verify_human_request(self._door['room_name'], True, use_confidence=True)
                        # If we haven't asked about it yet (and not currently removing or waiting), prompt the human
                        if self._answered is False and not self._remove and not self._waiting:
                            self._send_message(
                                'Found big rock blocking ' + str(self._door['room_name'])
                                + '. Please decide whether to "Remove together" or "Continue" searching. \n \n '
                                + 'Important features to consider are: \n safe - victims rescued: ' + str(self._collected_victims)
                                + ' \n explore - areas searched: area ' + str(self._searched_rooms).replace('area ', '')
                                + ' \n clock - removal time: 5 seconds \n afstand - distance between us: '
                                + self._distance_human,
                                'RescueBot'
                            )
                            self._waiting = True
                            self._current_prompt = RockObstacleSession(self, info, 100)

                        # If the human says "Continue" and we're not in forced removal mode, skip this obstacle
                        if self.received_messages_content \
                        and self.received_messages_content[-1] == 'Continue' \
                        and not self._remove:
                            self._current_prompt.continue_rock()
                            self._answered = True
                            self._waiting = False
                            self._skipped_obstacles.append(info['obj_id'])
                            self._to_search.append(self._door['room_name'])
                            self._phase = Phase.FIND_NEXT_GOAL

                        # If the user says "Remove together", or we forcibly remove
                        if (self.received_messages_content
                            and self.received_messages_content[-1] == 'Remove together') or self._remove:
                            self._help_remove_obstacle_session.verify_human_request(self._door['room_name'], True, use_confidence=True)
                            if not self._remove:
                                self._answered = True

                            # If the session is already in the WAITING_HUMAN phase, keep waiting for the human
                            if isinstance(self._current_prompt, RockObstacleSession) \
                            and self._current_prompt.currPhase == RockObstacleSession.RockObstaclePhase.WAITING_HUMAN:
                                if state[{'is_human_agent': True}]:
                                    # Human has arrived; finalize removal in the prompt
                                    self._current_prompt.complete_remove_rock()
                                    self._send_message(
                                        'Removing the big rock blocking ' + str(self._door['room_name']) + ' together!',
                                        'RescueBot'
                                    )
                                    # IMPORTANT: remove it from the environment
                                    self._phase = Phase.ENTER_ROOM
                                    return RemoveObject.__name__, {'object_id': info['obj_id']}
                                else:
                                    # Still waiting for the human to show up
                                    return None, {}

                            # If the user says "Remove together" but the human has not actually arrived
                            if not state[{'is_human_agent': True}]:
                                # Ask them to come
                                self._current_prompt.remove_rock()
                                self._send_message(
                                    'Please come to ' + str(self._door['room_name']) + ' to remove big rock.',
                                    'RescueBot'
                                )
                                return None, {}

                            else:
                                # Human is already here – remove the obstacle right now
                                self._current_prompt.complete_remove_rock()
                                self._send_message(
                                    'Removing the big rock blocking ' + str(self._door['room_name']) + ' together!',
                                    'RescueBot'
                                )
                                # Actually perform the MATRX remove action
                                self._phase = Phase.ENTER_ROOM
                                return RemoveObject.__name__, {'object_id': info['obj_id']}

                        # If none of the above triggered, we are likely waiting or idle
                        else:
                            # Let the prompt session handle waiting, timeouts, etc.
                            if isinstance(self._current_prompt, PromptSession):
                                result = self._current_prompt.wait()
                                if result:
                                    return result
                            return None, {}
                
                
                # If no obstacles are blocking the entrance, enter the area
                if len(objects) == 0:
                    if isinstance(self._current_prompt, StoneObstacleSession):
                        # If the current prompt is a stone obstacle session, then a stone obstacle must've been removed
                        self._current_prompt.complete_remove_together()
                    elif isinstance(self._current_prompt, RockObstacleSession) and self._current_prompt.currPhase == RockObstacleSession.RockObstaclePhase.WAITING_HUMAN:
                        # If the current prompt is a rock obstacle session in WAITING_HUMAN phase, complete the removal
                        self._current_prompt.complete_remove_rock()
                    elif not isinstance(self._current_prompt, TreeObstacleSession):
                        if self._remove and not self._waiting:
                            self._help_remove_obstacle_session.verify_human_request(self._door['room_name'], False, use_confidence=True) # No obstacle found
                    self._answered = False
                    self._remove = False
                    self._waiting = False
                    self._phase = Phase.ENTER_ROOM
                    



            if Phase.ENTER_ROOM == self._phase:
                self._answered = False

                # Check if the target victim has been rescued by the human, and switch to finding the next goal
                if self._goal_vic in self._collected_victims:
                    self._current_door = None
                    self._phase = Phase.FIND_NEXT_GOAL

                # Check if the target victim is found in a different area, and start moving there
                if self._goal_vic in self._found_victims \
                        and self._door['room_name'] != self._found_victim_logs[self._goal_vic]['room']:
                    self._current_door = None
                    self._phase = Phase.FIND_NEXT_GOAL

                # Check if area already searched without finding the target victim, and plan to search another area
                if self._door['room_name'] in self._searched_rooms and self._goal_vic not in self._found_victims:
                    self._current_door = None
                    self._phase = Phase.FIND_NEXT_GOAL

                # Enter the area and plan to search it
                else:
                    self._state_tracker.update(state)

                    action = self._navigator.get_move_action(self._state_tracker)
                    # If there is a valid action, return it; otherwise, plan to search the room
                    if action is not None:
                        return action, {}
                    self._phase = Phase.PLAN_ROOM_SEARCH_PATH



            if Phase.PLAN_ROOM_SEARCH_PATH == self._phase:
                # Extract the numeric location from the room name and set it as the agent's location
                self._agent_loc = int(self._door['room_name'].split()[-1])

                # Store the locations of all area tiles in the current room
                room_tiles = [info['location'] for info in state.values()
                             if 'class_inheritance' in info
                             and 'AreaTile' in info['class_inheritance']
                             and 'room_name' in info
                             and info['room_name'] == self._door['room_name']]
                self._roomtiles = room_tiles

                # Make the plan for searching the area
                self._navigator.reset_full()
                self._navigator.add_waypoints(self._efficientSearch(room_tiles))

                # Initialize variables for storing room victims and switch to following the room search path
                self._room_vics = []
                self._phase = Phase.FOLLOW_ROOM_SEARCH_PATH




            if Phase.FOLLOW_ROOM_SEARCH_PATH == self._phase:
                # Search the area
                self._state_tracker.update(state)
                action = self._navigator.get_move_action(self._state_tracker)
                if action != None:
                    # Identify victims present in the area
                    for info in state.values():
                        if 'class_inheritance' in info and 'CollectableBlock' in info['class_inheritance']:
                            vic = str(info['img_name'][8:-4])
                            # Remember which victim the agent found in this area
                            if vic not in self._room_vics:
                                self._room_vics.append(vic)

                            # Identify the exact location of the victim that was found by the human earlier
                            if vic in self._found_victims and 'location' not in self._found_victim_logs[vic].keys():
                                self._recent_vic = vic
                                # Add the exact victim location to the corresponding dictionary
                                self._found_victim_logs[vic] = {'location': info['location'],
                                                                'room': self._door['room_name'],
                                                                'obj_id': info['obj_id']}
                                if vic == self._goal_vic:
                                    # Communicate which victim was found
                                    self._send_message('Found ' + vic + ' in ' + self._door[
                                        'room_name'] + ' because you told me ' + vic + ' was located here.',
                                                      'RescueBot')
                                    # Add the area to the list with searched areas
                                    if self._door['room_name'] not in self._searched_rooms:
                                        self._searched_rooms.append(self._door['room_name'])
                                        # mark the area as searched by the agent
                                        self._searched_rooms_by_agent.append(self._door['room_name'])
                                        update_search_willingness(self, use_confidence=True) # number of searched rooms by agent has changed, update willingness
                                    # Do not continue searching the rest of the area but start planning to rescue the victim
                                    self._phase = Phase.FIND_NEXT_GOAL

                            
                            # Identify injured victim in the area
                            if 'healthy' not in vic and vic not in self._found_victims:
                                # Competence Update: Penalize human if bot finds victim during re-searching**
                                if (self._re_searching or self._door['room_name'] in self._searched_rooms_claimed_by_human)and self._door['room_name'] not in self._not_penalizable:
                                    penalize_search_competence_for_claimed_searched_room_with_victim(self, vic, use_confidence=True)
                                self._recent_vic = vic
                                # Add the victim and the location to the corresponding dictionary
                                self._found_victims.append(vic)
                                self._found_victim_logs[vic] = {'location': info['location'],
                                                                'room': self._door['room_name'],
                                                                'obj_id': info['obj_id']}
                                
                                
                                # Communicate which victim the agent found and ask the human whether to rescue the victim now or at a later stage
                                if 'mild' in vic and self._answered == False and not self._waiting:
                                    self._yellow_victim_session = YellowVictimSession(self, info, 100)
                                    print("Yellow Victim Session Created")

                                    trust_value = self._yellow_victim_session.decision_making()
                                    print(trust_value)

                                    if trust_value == YellowVictimSession.TrustDecision.LOW_COMPETENCE_AND_LOW_WILLINGNESS:
                                        print("Low competence and low willingness detected.")
                                        #add the Return back

                                        self._send_message('Found ' + vic + ' in ' + self._door['room_name'] + '. I do not trust you to pick them up.', 'RescueBot')
                                        return self._yellow_victim_session.decision_to_rescue()

                                    if trust_value == YellowVictimSession.TrustDecision.HIGH_COMPETENCE_AND_HIGH_WILLINGNESS:
                                        print("High competence and high willingness detected.")

                                        self._send_message('Found ' + vic + ' in ' + self._door['room_name'] + '. I trust you to pick them up.', 'RescueBot')
                                        return self._yellow_victim_session.decision_to_continue()

                                    # If either Competence or Willingness is low, continue as normal and request
                                    self._send_message('Found ' + vic + ' in ' + self._door['room_name'] + '. Please decide whether to "Rescue together", "Rescue alone", or "Continue" searching. \n \n \
                                        Important features to consider are: \n safe - victims (claimed to be) rescued: ' + str(
                                        set(self._collected_victims) | set(self._claimed_collected_victims)) + '\n explore - areas searched: area ' + str(
                                        self._searched_rooms).replace('area ', '') + '\n \
                                        clock - extra time when rescuing alone: 15 seconds \n afstand - distance between us: ' + self._distance_human,
                                                      'RescueBot')

                                    self._waiting = True


                                if 'critical' in vic and not self._answered and not self._waiting:
                                    self._red_victim_session = RedVictimSession(self, info, 200)
                                    print("Red Victim Session Created")

                                    self._red_victim_session.room_name = self._door['room_name']

                                    self._send_message('Found ' + vic + ' in ' + self._door['room_name'] + '. Please decide whether to "Rescue" or "Continue" searching. \n\n \
                                        Important features to consider are: \n explore - areas searched: area ' + str(
                                        self._searched_rooms).replace('area',
                                                                      '') + ' \n safe - victims (claimed to be) rescued: ' + str(
                                        set(self._collected_victims) | set(self._claimed_collected_victims)) + '\n \
                                        afstand - distance between us: ' + self._distance_human, 'RescueBot')
                                    self._waiting = True
                    return action, {}


                # Communicate that the agent did not find the target victim in the area while the human previously communicated the victim was located here
                if self._goal_vic in self._found_victims and self._goal_vic not in self._room_vics and \
                        self._found_victim_logs[self._goal_vic]['room'] == self._door['room_name']:
                    self._send_message(self._goal_vic + ' not present in ' + str(self._door[
                                                                                    'room_name']) + ' because I searched the whole area without finding ' + self._goal_vic + '.',
                                      'RescueBot')
                    # Remove the victim location from memory
                    self._found_victim_logs.pop(self._goal_vic, None)
                    self._found_victims.remove(self._goal_vic)
                    self._room_vics = []
                    # Reset received messages (bug fix)
                    self.received_messages = []
                    self.received_messages_content = []


                # Add the area to the list of searched areas
                if self._door['room_name'] not in self._searched_rooms:
                    self._searched_rooms.append(self._door['room_name'])
                    # mark the area as searched by the agent
                    self._searched_rooms_by_agent.append(self._door['room_name'])
                    update_search_willingness(self, use_confidence=True) # number of searched rooms by agent has changed, update willingness

                    
                    
                    
                # Make a plan to rescue a found critically injured victim if the human decides so
                if self.received_messages_content and self.received_messages_content[-1] == 'Rescue' \
                    and 'critical' in self._recent_vic:
                    self._rescue = 'together'
                    self._answered = True
                    self._waiting = False

                    # If the human isn't currently visible, remind them to come closer
                    if not state[{'is_human_agent': True}]:
                        self._red_victim_session.robot_rescue_together(use_confidence=True)

                        self._send_message(
                            f"Please come to {self._door['room_name']} to carry {self._recent_vic} together.",
                            "RescueBot"
                        )
                    if state[{'is_human_agent': True}]:
                        self._red_victim_session.robot_rescue_together(use_confidence=True)
                        self._send_message(
                            f"Lets carry {self._recent_vic} together! Please wait until I'm on top of {self._recent_vic}.",
                            "RescueBot"
                        )
                    
                    self._goal_vic = self._recent_vic
                    self._recent_vic = None
                    self._phase = Phase.PLAN_PATH_TO_VICTIM
                    
                    
                # Make a plan to rescue a found mildly injured victim together if the human decides so
                if self.received_messages_content and self._recent_vic \
                    and self.received_messages_content[-1] == 'Rescue together' \
                    and 'mild' in self._recent_vic:
                    self._rescue = 'together'
                    self._answered = True
                    self._waiting = False

                    # Tell the human to come over and help carry the mildly injured victim
                    if not state[{'is_human_agent': True}]:
                        self._yellow_victim_session.robot_rescue_together(use_confidence=True)

                        self._send_message('Please come to ' + str(self._door['room_name']) + ' to carry ' + str(
                            self._recent_vic) + ' together.', 'RescueBot')

                    # Tell the human to carry the mildly injured victim together (this code gets reached when the human is visible to the robot)
                    if state[{'is_human_agent': True}]:
                        self._yellow_victim_session.robot_rescue_together(use_confidence=True)

                        self._send_message('Lets carry ' + str(
                            self._recent_vic) + ' together! Please wait until I moved on top of ' + str(
                            self._recent_vic) + '.', 'RescueBot')
                    self._goal_vic = self._recent_vic
                    self._recent_vic = None
                    self._phase = Phase.PLAN_PATH_TO_VICTIM
                    
                    
                # Make a plan to rescue the mildly injured victim alone if the human decides so, and communicate this to the human
                if self.received_messages_content and self.received_messages_content[
                    -1] == 'Rescue alone' and 'mild' in self._recent_vic:

                    self._yellow_victim_session.robot_rescue_alone(True)

                    self._send_message('Picking up ' + self._recent_vic + ' in ' + self._door['room_name'] + '.',
                                      'RescueBot')
                    self._rescue = 'alone'
                    self._answered = True
                    self._waiting = False

                    self._goal_vic = self._recent_vic
                    self._goal_loc = self._remaining[self._goal_vic]
                    self._recent_vic = None

                    self._phase = Phase.PLAN_PATH_TO_VICTIM
                    
                    
                # Continue searching other areas if the human decides so
                if self.received_messages_content and self.received_messages_content[-1] == 'Continue':

                    # Check if the recent victim is a yellow or red victim
                    if isinstance(self._yellow_victim_session, YellowVictimSession):
                        self._yellow_victim_session.robot_continue_rescue(use_confidence=True)

                    elif isinstance(self._red_victim_session, RedVictimSession):
                        self._red_victim_session.robot_continue_rescue(use_confidence=True)
                    
                    self._answered = True
                    self._waiting = False
                    self._todo.append(self._recent_vic)
                    self._recent_vic = None
                    self._phase = Phase.FIND_NEXT_GOAL
                    
                    
                # Remain idle untill the human communicates to the agent what to do with the found victim
                if self.received_messages_content and self._waiting and self.received_messages_content[
                    -1] != 'Rescue' and self.received_messages_content[-1] != 'Continue':
                    if isinstance(self._yellow_victim_session, PromptSession):
                        self._yellow_victim_session.wait(use_confidence=True)
                    if isinstance(self._red_victim_session, PromptSession):
                        self._red_victim_session.wait(use_confidence=True)
                    return None, {}


                # Find the next area to search when the agent is not waiting for an answer from the human or occupied with rescuing a victim
                if not self._waiting and not self._rescue:
                    self._recent_vic = None
                    self._phase = Phase.FIND_NEXT_GOAL
                return Idle.__name__, {'duration_in_ticks': 25}


            if Phase.PLAN_PATH_TO_VICTIM == self._phase:
                # Plan the path to a found victim using its location
                self._navigator.reset_full()
                self._navigator.add_waypoints([self._found_victim_logs[self._goal_vic]['location']])
                # Follow the path to the found victim
                self._phase = Phase.FOLLOW_PATH_TO_VICTIM



            if Phase.FOLLOW_PATH_TO_VICTIM == self._phase:
                # Start searching for other victims if the human already rescued the target victim
                if self._goal_vic and self._goal_vic in self._collected_victims:
                    self._phase = Phase.FIND_NEXT_GOAL

                # Move towards the location of the found victim
                else:
                    self._state_tracker.update(state)

                    action = self._navigator.get_move_action(self._state_tracker)
                    # If there is a valid action, return it; otherwise, switch to taking the victim
                    if action is not None:
                        return action, {}
                    self._phase = Phase.TAKE_VICTIM


# --------------------
            if Phase.TAKE_VICTIM == self._phase:
                # Store all area tiles in a list
                room_tiles = [info['location'] for info in state.values()
                             if 'class_inheritance' in info
                             and 'AreaTile' in info['class_inheritance']
                             and 'room_name' in info
                             and info['room_name'] == self._found_victim_logs[self._goal_vic]['room']]
                self._roomtiles = room_tiles
                objects = []

                # When the victim has to be carried by human and agent together, check whether human has arrived at the victim's location
                for info in state.values():
                    # When the victim has to be carried by human and agent together, check whether human has arrived at the victim's location
                    if 'class_inheritance' in info and 'CollectableBlock' in info['class_inheritance'] and 'critical' in \
                            info['obj_id'] and info['location'] in self._roomtiles or \
                            'class_inheritance' in info and 'CollectableBlock' in info[
                        'class_inheritance'] and 'mild' in info['obj_id'] and info[
                        'location'] in self._roomtiles and self._rescue == 'together' or \
                            self._goal_vic in self._found_victims and self._goal_vic in self._todo and len(
                        self._searched_rooms) == 0 and 'class_inheritance' in info and 'CollectableBlock' in info[
                        'class_inheritance'] and 'critical' in info['obj_id'] and info['location'] in self._roomtiles or \
                            self._goal_vic in self._found_victims and self._goal_vic in self._todo and len(
                        self._searched_rooms) == 0 and 'class_inheritance' in info and 'CollectableBlock' in info[
                        'class_inheritance'] and 'mild' in info['obj_id'] and info['location'] in self._roomtiles:
                        
                        objects.append(info)

                        if 'mild' in info['obj_id']:
                            if isinstance(self._yellow_victim_session, PromptSession):
                                timeout_encountered = self._yellow_victim_session.wait(use_confidence=True)
                                if timeout_encountered == 1:
                                    return None, {}


                        if 'critical' in info['obj_id']:
                            if isinstance(self._red_victim_session, PromptSession):
                                timeout_encountered = self._red_victim_session.wait(use_confidence=True)
                                if timeout_encountered == 1:
                                    return None, {}
                
                        # Remain idle when the human has not arrived at the location
                        if not info.get('is_human_agent', False):
                            # That means this object is not the human
                            self._waiting = True
                            self._moving = False
                            return None, {}

                
                # Add the victim to the list of rescued victims when it has been picked up
                if len(objects) == 0 and 'critical' in self._goal_vic or len(
                        objects) == 0 and 'mild' in self._goal_vic and self._rescue == 'together':

                    if 'mild' in self._goal_vic:
                        print("Dropped Yellow Victim Together")

                    if 'critical' in self._goal_vic:
                        print("Dropped Red Victim Together")
                    
                    self._waiting = False
                    if self._goal_vic not in self._collected_victims:
                        self._collected_victims.append(self._goal_vic)
                    self._carrying_together = True
                    # Determine the next victim to rescue or search
                    self._phase = Phase.FIND_NEXT_GOAL

                # When rescuing mildly injured victims alone, pick the victim up and plan the path to the drop zone
                if 'mild' in self._goal_vic and self._rescue == 'alone':
                    self._phase = Phase.PLAN_PATH_TO_DROPPOINT
                    if self._goal_vic not in self._collected_victims:
                        self._collected_victims.append(self._goal_vic)
                    self._carrying = True
                    return CarryObject.__name__, {'object_id': self._found_victim_logs[self._goal_vic]['obj_id'],
                                                  'human_name': self._human_name}



            if Phase.PLAN_PATH_TO_DROPPOINT == self._phase:
                self._navigator.reset_full()
                # Plan the path to the drop zone
                self._navigator.add_waypoints([self._goal_loc])
                # Follow the path to the drop zone
                self._phase = Phase.FOLLOW_PATH_TO_DROPPOINT



            if Phase.FOLLOW_PATH_TO_DROPPOINT == self._phase:
                # Communicate that the agent is transporting a mildly injured victim alone to the drop zone
                if 'mild' in self._goal_vic and self._rescue == 'alone':
                    self._send_message('Transporting ' + self._goal_vic + ' to the drop zone.', 'RescueBot')
                self._state_tracker.update(state)
                # Follow the path to the drop zone
                action = self._navigator.get_move_action(self._state_tracker)
                if action is not None:
                    return action, {}
                # Drop the victim at the drop zone
                self._phase = Phase.DROP_VICTIM



            if Phase.DROP_VICTIM == self._phase:
                # Communicate that the agent delivered a mildly injured victim alone to the drop zone
                if 'mild' in self._goal_vic and self._rescue == 'alone':
                    self._send_message('Delivered ' + self._goal_vic + ' at the drop zone.', 'RescueBot')
                
                if 'critical' in self._goal_vic:
                    if isinstance(self._red_victim_session, RedVictimSession):
                        # This finalizes the rescue, includes time-based trust update
                        self._red_victim_session.complete_rescue_together(use_confidence=True)
                        self._red_victim_session.delete_red_victim_session()
                    self._send_message(f"Delivered {self._goal_vic} (critical) at the drop zone.", "RescueBot")
                
                # Identify the next target victim to rescue
                self._phase = Phase.FIND_NEXT_GOAL
                self._rescue = None
                self._current_door = None
                self._tick = state['World']['nr_ticks']
                self._carrying = False
                # Drop the victim on the correct location on the drop zone
                return Drop.__name__, {'human_name': self._human_name}


    def _get_drop_zones(self, state):
        '''
        @return list of drop zones (their full dict), in order (the first one is the
        place that requires the first drop)
        '''
        places = state[{'is_goal_block': True}]
        places.sort(key=lambda info: info['location'][1])
        zones = []
        for place in places:
            if place['drop_zone_nr'] == 0:
                zones.append(place)
        return zones

    def _process_messages(self, state, teamMembers, condition):
        '''
        process incoming messages received from the team members
        '''

        receivedMessages = {}
        # Create a dictionary with a list of received messages from each team member
        for member in teamMembers:
            receivedMessages[member] = []
        for mssg in self.received_messages:
            for member in teamMembers:
                if mssg.from_id == member:
                    receivedMessages[member].append(mssg.content)

        # Check the content of the received messages
        for mssgs in receivedMessages.values():
            for msg in mssgs:
                # If a received message involves team members searching areas, add these areas to the memory of areas that have been explored
                if msg.startswith("Search:") and msg not in self._consumed_messages:
                    area = 'area ' + msg.split()[-1]
                    if area not in self._searched_rooms:
                        add_room_based_on_trust(self, self._search_competence_start_value, area)
                    # always add the area to the memory of searched areas by human for competence evaluation later
                    if area in self._searched_rooms_claimed_by_human or area in self._searched_rooms_by_agent:
                        penalize_search_willingness_for_sending_rooms_already_searched(self, area, use_confidence=True)
                    else:
                        reward_search_competence_for_claimed_searched_room(self, area, use_confidence=True)
                        self._number_of_actions_search += 1
                        # Update 'count' in csv
                        self._trustBelief(self._team_members, self._trustBeliefs, self._folder, 'search', "count",
                                         self._number_of_actions_search)
                        self._searched_rooms_claimed_by_human.append(area)
                        update_search_willingness(self, use_confidence=True)
                    # avoid processing the same message multiple times
                    self._consumed_messages.add(msg)


                # If a received message involves team members finding victims, add these victims and their locations to memory
                if msg.startswith("Found:"):
                    # Identify which victim and area it concerns
                    if len(msg.split()) == 6:
                        foundVic = ' '.join(msg.split()[1:4])
                    else:
                        foundVic = ' '.join(msg.split()[1:5])
                    loc = 'area ' + msg.split()[-1]


                    # Add the area to the memory of searched areas
                    if loc not in self._searched_rooms:
                        if 'mild' in foundVic:
                            rescue_yellow_competence = self._trustBeliefs[self._human_name]['rescue_yellow']['competence']
                            add_room_based_on_trust(self, rescue_yellow_competence, loc)
                        else:
                            rescue_red_competence = self._trustBeliefs[self._human_name]['rescue_red']['competence']
                            add_room_based_on_trust(self, rescue_red_competence, loc)


                    if msg not in self._yellow_victim_processed_messages and 'mild' in foundVic:
                        self._yellow_victim_session = YellowVictimSession(self, None, 100)

                        # Human claimed to have found a new yellow victim
                        if foundVic not in self._found_victims:
                            self._yellow_victim_session.human_found_alone_truth(True)

                        # Human claimed to have found a new yellow victim that was already found
                        if foundVic in self._found_victims:
                            self._yellow_victim_session.human_found_alone_lie(True)

                        self._yellow_victim_session.delete_yellow_victim_session(False)

                        self._yellow_victim_processed_messages.add(msg)


                    # Add the victim and its location to memory
                    if foundVic not in self._found_victims:
                        self._found_victims.append(foundVic)
                        self._found_victim_logs[foundVic] = {'room': loc}
                    if foundVic in self._found_victims and self._found_victim_logs[foundVic]['room'] != loc:
                        self._found_victim_logs[foundVic] = {'room': loc}


                    # Decide to help the human carry a found victim when the human's condition is 'weak'
                    if condition == 'weak':
                        self._rescue = 'together'
                    # Add the found victim to the to do list when the human's condition is not 'weak'
                    if 'mild' in foundVic and condition != 'weak':
                        self._todo.append(foundVic)



                # If a received message involves team members rescuing victims, add these victims and their locations to memory
                if msg.startswith('Collect:'):
                    # Identify which victim and area it concerns
                    if len(msg.split()) == 6:
                        collectVic = ' '.join(msg.split()[1:4])
                    else:
                        collectVic = ' '.join(msg.split()[1:5])
                    loc = 'area ' + msg.split()[-1]

                    # Add the area to the memory of searched areas
                    if loc not in self._searched_rooms:
                        rescue_yellow_competence = self._trustBeliefs[self._human_name]['rescue_yellow']['competence']
                        add_room_based_on_trust(self, rescue_yellow_competence, loc)

                    if msg not in self._yellow_victim_processed_messages and 'mild' in collectVic:
                        self._yellow_victim_session = YellowVictimSession(self, None, 100)

                        # Human claimed to have collect a new yellow victim
                        if collectVic not in self._found_victims and collectVic not in self._collected_victims and collectVic not in self._claimed_collected_victims:
                            self._yellow_victim_session.human_collect_alone_truth(True)

                        # Human claimed to have collect a new yellow victim that was already collected
                        if collectVic in self._collected_victims or collectVic in self._claimed_collected_victims:
                            self._yellow_victim_session.human_collect_alone_lie(True)

                        self._yellow_victim_session.delete_yellow_victim_session(False)

                        self._yellow_victim_processed_messages.add(msg)



                    # Add the victim and location to the memory of found victims
                    if collectVic not in self._found_victims:
                        self._found_victims.append(collectVic)
                        self._found_victim_logs[collectVic] = {'room': loc}
                    if collectVic in self._found_victims and self._found_victim_logs[collectVic]['room'] != loc:
                        self._found_victim_logs[collectVic] = {'room': loc}

                        # A lie about the victim location has occured
                        self._yellow_victim_session = YellowVictimSession(self, None, 100)
                        self._yellow_victim_session.human_collect_alone_lie_location(True)
                        self._yellow_victim_session.delete_yellow_victim_session(False)


                    # Add the victim to the memory of rescued victims when the human's condition is not weak
                    if condition != 'weak' and collectVic not in self._claimed_collected_victims:
                        # self._collected_victims.append(collectVic)
                        self._claimed_collected_victims.append(collectVic)

                    # Decide to help the human carry the victim together when the human's condition is weak
                    if condition == 'weak':
                        self._rescue = 'together'


                # If a received message involves team members asking for help with removing obstacles, add their location to memory and come over
                if msg.startswith('Remove:') and msg not in self._consumed_messages:
                    area = 'area ' + msg.split()[-1]
                    if area in self._searched_rooms_by_agent:
                        self._help_remove_obstacle_session.penalize_help_remove_willingness_already_searched(area, use_confidence=True)
                        
                    else:
                        if area not in self._help_remove_rooms_current_round:
                            self._help_remove_rooms_current_round.append(area)
                            self._help_remove_obstacle_session.update_help_remove_willingness(use_confidence=True)
                        self._number_of_actions_help_remove += 1
                        trust_value = self._help_remove_obstacle_session.decision_making(area)
                        # Come over immediately when the agent is not carrying a victim
                        if trust_value == HelpRemoveObstacleSession.TrustDecision.HIGH_COMPETENCE:
                            self._send_message('I am coming over to help remove the obstacle.', 'RescueBot')
                            self._help_remove_obstacle_session.decision_to_help(state, area)
                        else:
                            self._send_message('I am not coming over to help remove the obstacle.', 'RescueBot')
                    self._consumed_messages.add(msg)
            # Store the current location of the human in memory
            if mssgs and mssgs[-1].split()[-1] in ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12', '13',
                                                   '14']:
                self._human_loc = int(mssgs[-1].split()[-1])



    def _loadBelief(self, members, folder):
        '''
        Loads trust belief values if agent already collaborated with human before, otherwise trust belief values are initialized using default values.
        '''
        # Create a dictionary with trust values for all team members
        trustBeliefs = {}
        
        # Set a default starting trust value
        search_default = 0.0
        rescue_yellow_default = 0.25
        rescue_red_default = 0.25
        remove_stone_default = 0.25
        remove_rock_default = 0.25
        remove_tree_default = 0.25
        help_remove_default = 0.25
        
        default = 0.5
        
        trustfile_header = []
        trustfile_contents = []
        
        # Check if agent already collaborated with this human before, if yes: load the corresponding trust values, if no: initialize using default trust values
        with open(folder + '/beliefs/allTrustBeliefs.csv') as csvfile:
            reader = csv.reader(csvfile, delimiter=';', quotechar="'")
            for row in reader:
                if trustfile_header == []:
                    trustfile_header = row
                    continue
                # Retrieve trust values 
                if row and row[0] == self._human_name:
                    name, task, competence, willingness, count = row[0], row[1], float(row[2]), float(row[3]), int(row[4])
                    
                    # Ensure dictionary structure exists
                    if name not in trustBeliefs:
                        trustBeliefs[name] = {}

                    # Store retrieved trust values for performed tasks
                    trustBeliefs[name][task] = {'competence': competence, 'willingness': willingness, 'count':count}

        # Check for missing tasks and initialize defaults only for them**
        if self._human_name not in trustBeliefs:
            trustBeliefs[self._human_name] = {}

        for task in self._tasks:
            if task not in trustBeliefs[self._human_name]:  # Only initialize if missing
                if task == 'search':
                    trustBeliefs[self._human_name][task] = {'competence': search_default, 'willingness': search_default, 'count': 0}
                if task == 'rescue_yellow':
                    trustBeliefs[self._human_name][task] = {'competence': rescue_yellow_default, 'willingness': rescue_yellow_default, 'count': 0}
                if task == 'rescue_red':
                    trustBeliefs[self._human_name][task] = {'competence': rescue_red_default, 'willingness': rescue_red_default, 'count': 0}
                if task == 'remove_rock':
                    trustBeliefs[self._human_name][task] = {'competence': remove_rock_default, 'willingness': remove_rock_default, 'count': 0}
                if task == 'remove_stone':
                    trustBeliefs[self._human_name][task] = {'competence': remove_stone_default, 'willingness': remove_stone_default, 'count': 0}
                if task == 'remove_tree':
                    trustBeliefs[self._human_name][task] = {'competence': remove_tree_default, 'willingness': remove_tree_default, 'count': 0}
                if task == 'help_remove':
                    trustBeliefs[self._human_name][task] = {'competence': help_remove_default, 'willingness': help_remove_default, 'count': 0}
                else:
                    trustBeliefs[self._human_name][task] = {'competence': default, 'willingness': default, 'count': 0}

        self._number_of_actions_search = trustBeliefs[self._human_name]['search']['count']
        YellowVictimSession.number_of_actions = trustBeliefs[self._human_name]['rescue_yellow']['count']
        RedVictimSession.number_of_actions = trustBeliefs[self._human_name]['rescue_red']['count']
        RockObstacleSession.count_actions = trustBeliefs[self._human_name]['remove_rock']['count']
        StoneObstacleSession.count = trustBeliefs[self._human_name]['remove_stone']['count']
        TreeObstacleSession.count = trustBeliefs[self._human_name]['remove_tree']['count']
        self._number_of_actions_help_remove = trustBeliefs[self._human_name]['help_remove']['count']

        return trustBeliefs

    def _trustBelief(self, members, trustBeliefs, folder, task, belief, increment):
        '''
        Baseline implementation of a trust belief. Creates a dictionary with trust belief scores for each team member. 
        '''

        if PromptSession.scenario_used != Scenario.USE_TRUST_MECHANISM:
            # Perform no change if the scenario is the same
            return trustBeliefs

        # Save current trust belief values so we can later use and retrieve them to add to a csv file with all the logged trust belief values
        # Update the trust value
        trustBeliefs[self._human_name][task][belief] += increment

        if belief != 'count':
            # Restrict the belief value to a range of -1 to 1
            trustBeliefs[self._human_name][task][belief] = np.clip(trustBeliefs[self._human_name][task][belief], -1, 1)

        # Save current trust belief values to a CSV file for logging
        with open(folder + '/beliefs/currentTrustBelief.csv', mode='w') as csv_file:
            csv_writer = csv.writer(csv_file, delimiter=';', quotechar='"', quoting=csv.QUOTE_MINIMAL)
            csv_writer.writerow(['name', 'task', 'competence', 'willingness', 'count'])
            for name, tasks in trustBeliefs.items():
                for task, values in tasks.items():
                    csv_writer.writerow([name, task, values['competence'], values['willingness'], values['count']])


        return trustBeliefs
   
    def _send_message(self, mssg, sender):
        '''
        send messages from agent to other team members
        '''
        msg = Message(content=mssg, from_id=sender)
        if msg.content not in self.received_messages_content and 'Our score is' not in msg.content:
            self.send_message(msg)
            self._send_messages.append(msg.content)
        # Sending the hidden score message (DO NOT REMOVE)
        if 'Our score is' in msg.content:
            self.send_message(msg)

    def _getClosestRoom(self, state, objs, currentDoor):
        '''
        calculate which area is closest to the agent's location
        '''
        agent_location = state[self.agent_id]['location']
        locs = {}
        for obj in objs:
            locs[obj] = state.get_room_doors(obj)[0]['location']
        dists = {}
        for room, loc in locs.items():
            if currentDoor != None:
                dists[room] = utils.get_distance(currentDoor, loc)
            if currentDoor == None:
                dists[room] = utils.get_distance(agent_location, loc)

        return min(dists, key=dists.get)

    def _efficientSearch(self, tiles):
        '''
        efficiently transverse areas instead of moving over every single area tile
        '''
        x = []
        y = []
        for i in tiles:
            if i[0] not in x:
                x.append(i[0])
            if i[1] not in y:
                y.append(i[1])
        locs = []
        for i in range(len(x)):
            if i % 2 == 0:
                locs.append((x[i], min(y)))
            else:
                locs.append((x[i], max(y)))
        return locs

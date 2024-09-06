from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
from rlbot.flat import (
    BoostPad,
    FieldInfo,
    GameStateType,
    GameTickPacket,
    GravityOption,
    MatchSettings,
)

from .car import Car
from .common_values import BOOST_LOCATIONS
from .game_config import GameConfig
from .physics_object import PhysicsObject
from .utils import create_default_init


@dataclass(init=False)
class GameState:
    tick_count: int
    goal_scored: bool
    config: GameConfig
    cars: Dict[int, Car]
    ball: PhysicsObject
    _inverted_ball: PhysicsObject
    boost_pad_timers: np.ndarray
    _inverted_boost_pad_timers: np.ndarray

    _first_update_call: bool
    _tick_skip: int
    # Unless something changes, this mapping will be [14,10,7,12,8,11,29,4,3,15,18,30,1,2,5,6,9,20,19,22,21,23,25,32,31,26,27,24,28,33,17,13,16,0] for the standard map.
    _boost_pad_order_mapping: np.ndarray
    _boost_pads: List[BoostPad]

    __slots__ = tuple(__annotations__)

    exec(create_default_init(__slots__))

    @property
    def scoring_team(self) -> Optional[int]:
        if self.goal_scored:
            return 0 if self.ball.position[1] > 0 else 1
        return None

    @property
    def inverted_ball(self) -> PhysicsObject:
        self._inverted_ball = self.ball.inverted()
        return self._inverted_ball

    @property
    def inverted_boost_pad_timers(self) -> np.ndarray:
        self._inverted_boost_pad_timers = np.ascontiguousarray(
            self.boost_pad_timers[::-1]
        )
        return self._inverted_boost_pad_timers

    @staticmethod
    def create_compat_game_state(
        field_info: FieldInfo,
        match_settings=MatchSettings(),
        tick_skip=8,
        standard_map=True,
    ):
        state = GameState()
        state._tick_skip = tick_skip
        state.tick_count = 0
        state.goal_scored = False
        state.config = GameConfig()
        state.config.boost_consumption = 1  # Not modifiable
        state.config.dodge_deadzone = 0.5  # Not modifiable
        if match_settings.mutator_settings is not None:
            match match_settings.mutator_settings.gravity_option:
                case GravityOption.Low:
                    gravity = -325
                case GravityOption.Default:
                    gravity = -650
                case GravityOption.High:
                    gravity = -1137.5
                case GravityOption.Super_High:
                    gravity = -3250
                case GravityOption.Reverse:
                    gravity = 650
            state.config.gravity = gravity / -650.0
        else:
            state.config.gravity = 1
        state.cars = {}
        state.ball = PhysicsObject.create_compat_physics_object()
        state.boost_pad_timers = np.zeros(len(field_info.boost_pads), dtype=np.float32)
        if standard_map:
            boost_locations = np.array(BOOST_LOCATIONS)
            state._boost_pad_order_mapping = np.zeros(
                len(field_info.boost_pads), dtype=np.int32
            )
            for rlbot_boost_pad_idx, boost_pad in enumerate(field_info.boost_pads):
                loc = np.array(
                    [boost_pad.location.x, boost_pad.location.y, boost_pad.location.z]
                )
                similar_vals = np.isclose(boost_locations[:, :2], loc[:2], atol=2).all(
                    axis=1
                )
                candidate_idx = np.argmax(similar_vals)
                assert similar_vals[
                    candidate_idx
                ], f"Boost pad at location {loc} doesn't match any in the standard map (see BOOST_LOCATIONS in common_values.py)"
                state._boost_pad_order_mapping[rlbot_boost_pad_idx] = candidate_idx
        else:
            state._boost_pad_order_mapping = [
                idx for idx in range(len(field_info.boost_pads))
            ]
        state._boost_pads = list(field_info.boost_pads)
        state._first_update_call = True
        return state

    def update(self, packet: GameTickPacket):
        doing_first_call = False
        if self._first_update_call:
            self.tick_count = packet.game_info.frame_num
            self._first_update_call = False
            doing_first_call = True

        # Initialize new players
        for player_index, player_info in enumerate(packet.players):
            if player_info.spawn_id not in self.cars:
                self.cars[player_info.spawn_id] = Car.create_compat_car(
                    packet, player_index, self._tick_skip
                )

        ticks_elapsed = packet.game_info.frame_num - self.tick_count
        # Nothing to do
        if ticks_elapsed == 0 and not doing_first_call:
            return

        self.goal_scored = packet.game_info.game_state_type == GameStateType.GoalScored

        if len(packet.balls) > 0:
            ball = packet.balls[0]
            self.ball.update(ball.physics)
        else:
            ball = None

        for player_index, player_info in enumerate(packet.players):
            self.cars[player_info.spawn_id].update(
                player_info,
                (
                    None
                    if ball is None or ball.latest_touch.player_index != player_index
                    else ball.latest_touch
                ),
                ticks_elapsed,
            )

        for boost_pad_index, boost_pad_state in enumerate(packet.boost_pad_states):
            self.boost_pad_timers[self._boost_pad_order_mapping[boost_pad_index]] = (
                boost_pad_state.timer
            )

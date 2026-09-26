"""Allowlisted action registry. Only ids on the policy allowlist can be registered,
and there is deliberately no action that writes to a machine controller."""
from __future__ import annotations

from typing import Callable

from .safety_guard import Policy
from .schemas import ProposedStep


class ActionNotAllowed(PermissionError):
    pass


class ActionRegistry:
    def __init__(self, policy: Policy):
        self.policy = policy
        self._fns: dict[str, Callable[[ProposedStep], None]] = {}

    def register(self, action_id: str, fn: Callable[[ProposedStep], None]) -> None:
        if action_id not in self.policy.allowlisted_actions:
            raise ActionNotAllowed(f"{action_id} is not on the {self.policy.label} allowlist")
        self._fns[action_id] = fn

    def run(self, step: ProposedStep) -> bool:
        if step.action_id not in self.policy.allowlisted_actions:
            raise ActionNotAllowed(step.action_id)
        fn = self._fns.get(step.action_id)
        if fn is None:
            return False
        fn(step)
        return True

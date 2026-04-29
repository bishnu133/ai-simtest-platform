"""Membership lookup and creation — Turn 2 Step 2 (Category B).

Consumers import the Protocol and the record type from this module:

    from src.memberships import MembershipRepository, MembershipRecord
"""
from __future__ import annotations

from src.db.domain import MembershipRecord
from src.memberships.repository import (
    InMemoryMembershipRepository,
    MembershipRepository,
    PostgresMembershipRepository,
)

__all__ = [
    "MembershipRecord",
    "MembershipRepository",
    "InMemoryMembershipRepository",
    "PostgresMembershipRepository",
]

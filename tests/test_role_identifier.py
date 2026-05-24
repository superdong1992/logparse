"""Tests for RoleIdentifier."""
from __future__ import annotations

from datetime import datetime

import pytest

from backend.models import (
    ActivePeriod, BoardRole, LogEntry, MechResult, ParseResult, SlotInfo,
)
from backend.parsing.role_identifier import RoleIdentifier


@pytest.fixture
def identifier():
    return RoleIdentifier()


class TestRoleIdentifier:
    def test_mech_active(self, identifier):
        result = ParseResult()
        slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        result.diagnostic_slots.append(slot)
        mech = MechResult(module_name="MOD", active_master_slots=["1"])
        identifier.apply_mech_roles(mech, result)
        assert slot.role == BoardRole.ACTIVE

    def test_fallback_active_single_candidate(self, identifier):
        """唯一候选 → ACTIVE。"""
        result = ParseResult()
        slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        slot.add_active_period(ActivePeriod(
            start=datetime(2026, 1, 3, 0, 0), end=datetime(2026, 1, 3, 1, 0),
        ))
        result.diagnostic_slots.append(slot)
        identifier.fallback_roles(result)
        assert slot.role == BoardRole.ACTIVE

    def test_fallback_multiple_candidates_stay_unknown(self, identifier):
        """多个 slot 都有 ActivePeriod → 不武断判 ACTIVE，保持 UNKNOWN。"""
        result = ParseResult()
        slot1 = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        slot1.add_active_period(ActivePeriod(
            start=datetime(2026, 1, 3, 0, 0), end=datetime(2026, 1, 3, 1, 0),
        ))
        slot2 = SlotInfo(slot_id="2", name="slot_2", path="/tmp")
        slot2.add_active_period(ActivePeriod(
            start=datetime(2026, 1, 3, 0, 0), end=datetime(2026, 1, 3, 1, 0),
        ))
        result.diagnostic_slots.extend([slot1, slot2])
        identifier.fallback_roles(result)
        # 多候选时保持 UNKNOWN（不武断判 ACTIVE）
        assert slot1.role == BoardRole.UNKNOWN
        assert slot2.role == BoardRole.UNKNOWN

    def test_fallback_standby(self, identifier):
        result = ParseResult()
        slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        slot.add_diagnostic_log(LogEntry(path="/tmp/f", name="f.log", size_bytes=100))
        result.diagnostic_slots.append(slot)
        identifier.fallback_roles(result)
        assert slot.role == BoardRole.STANDBY

    def test_fallback_unknown(self, identifier):
        result = ParseResult()
        slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        result.diagnostic_slots.append(slot)
        identifier.fallback_roles(result)
        assert slot.role == BoardRole.UNKNOWN

    def test_no_override_existing(self, identifier):
        result = ParseResult()
        slot = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        slot.role = BoardRole.ACTIVE
        result.diagnostic_slots.append(slot)
        identifier.fallback_roles(result)
        assert slot.role == BoardRole.ACTIVE

    def test_mech_active_blocks_fallback(self, identifier):
        """机制模块已判 ACTIVE → fallback 不覆盖，其他 slot 判 STANDBY。"""
        result = ParseResult()
        slot1 = SlotInfo(slot_id="1", name="slot_1", path="/tmp")
        slot1.role = BoardRole.ACTIVE
        slot2 = SlotInfo(slot_id="2", name="slot_2", path="/tmp")
        slot2.add_diagnostic_log(LogEntry(path="/tmp/f", name="f.log", size_bytes=100))
        result.diagnostic_slots.extend([slot1, slot2])
        identifier.fallback_roles(result)
        assert slot1.role == BoardRole.ACTIVE
        assert slot2.role == BoardRole.STANDBY

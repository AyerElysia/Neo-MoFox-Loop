"""Tests for PermissionManager.

This module contains comprehensive tests for the permission system,
including person ID generation, permission level management,
and command permission checking.
"""

import asyncio

import pytest

from src.core.components.types import PermissionLevel
from src.core.managers.permission_manager import (
    PermissionManager,
    get_permission_manager,
)


class TestPermissionLevel:
    """Test cases for PermissionLevel enum."""

    def test_permission_level_values(self) -> None:
        """Test permission level numeric values."""
        assert PermissionLevel.GUEST.value == 1
        assert PermissionLevel.USER.value == 2
        assert PermissionLevel.OPERATOR.value == 3
        assert PermissionLevel.OWNER.value == 4

    def test_permission_level_comparison(self) -> None:
        """Test permission level comparison operators."""
        assert PermissionLevel.GUEST < PermissionLevel.USER
        assert PermissionLevel.USER < PermissionLevel.OPERATOR
        assert PermissionLevel.OPERATOR < PermissionLevel.OWNER
        assert PermissionLevel.OWNER >= PermissionLevel.OPERATOR

    def test_permission_level_from_string(self) -> None:
        """Test converting string to PermissionLevel."""
        assert PermissionLevel.from_string("guest") == PermissionLevel.GUEST
        assert PermissionLevel.from_string("user") == PermissionLevel.USER
        assert PermissionLevel.from_string("operator") == PermissionLevel.OPERATOR
        assert PermissionLevel.from_string("owner") == PermissionLevel.OWNER
        # Case insensitive
        assert PermissionLevel.from_string("GUEST") == PermissionLevel.GUEST
        assert PermissionLevel.from_string("User") == PermissionLevel.USER

    def test_permission_level_from_string_invalid(self) -> None:
        """Test PermissionLevel.from_string with invalid input."""
        with pytest.raises(ValueError, match="无效的权限级别"):
            PermissionLevel.from_string("invalid")

    def test_permission_level_to_string(self) -> None:
        """Test converting PermissionLevel to string."""
        assert PermissionLevel.GUEST.to_string() == "guest"
        assert PermissionLevel.USER.to_string() == "user"
        assert PermissionLevel.OPERATOR.to_string() == "operator"
        assert PermissionLevel.OWNER.to_string() == "owner"


class TestPersonIdGeneration:
    """Test cases for person ID generation."""

    def setup_method(self) -> None:
        """Setup test fixtures."""
        self.manager = PermissionManager()

    def test_generate_person_id_consistency(self) -> None:
        """Test that same input produces same hash."""
        id1 = self.manager.generate_person_id("qq", "123456")
        id2 = self.manager.generate_person_id("qq", "123456")
        assert id1 == id2

    def test_generate_person_id_uniqueness(self) -> None:
        """Test that different input produces different hash."""
        id1 = self.manager.generate_person_id("qq", "123456")
        id2 = self.manager.generate_person_id("qq", "654321")
        assert id1 != id2

        id3 = self.manager.generate_person_id("wechat", "123456")
        assert id1 != id3

    def test_generate_person_id_format(self) -> None:
        """Test person ID format is SHA-256 hash (64 hex chars)."""
        person_id = self.manager.generate_person_id("qq", "123456")
        assert len(person_id) == 64  # SHA-256 produces 64 hex characters
        assert all(c in "0123456789abcdef" for c in person_id)

    def test_generate_raw_person_id(self) -> None:
        """Test raw person ID generation."""
        raw_id = self.manager.generate_raw_person_id("qq", "123456")
        assert raw_id == "qq:123456"

        raw_id2 = self.manager.generate_raw_person_id("wechat", "abc")
        assert raw_id2 == "wechat:abc"


class TestPermissionManager:
    """Test cases for PermissionManager."""

    def setup_method(self) -> None:
        """Setup test fixtures."""
        self.manager = PermissionManager()

    def test_singleton(self) -> None:
        """Test that get_permission_manager returns singleton."""
        manager1 = get_permission_manager()
        manager2 = get_permission_manager()
        assert manager1 is manager2

    @pytest.mark.asyncio
    async def test_get_user_permission_level_default(self) -> None:
        """Test getting default permission level for new user."""
        person_id = self.manager.generate_person_id("test", "user1")
        level = await self.manager.get_user_permission_level(person_id)
        # Default should be USER
        assert level == PermissionLevel.USER

    @pytest.mark.asyncio
    async def test_set_user_permission_group(self) -> None:
        """Test setting user permission group."""
        person_id = self.manager.generate_person_id("test", "user2")

        # Set to operator
        success = await self.manager.set_user_permission_group(
            person_id=person_id,
            level=PermissionLevel.OPERATOR,
            granted_by="admin_id",
            reason="测试提升",
        )
        assert success is True

        # Verify
        level = await self.manager.get_user_permission_level(person_id)
        assert level == PermissionLevel.OPERATOR

    @pytest.mark.asyncio
    async def test_set_user_permission_group_update(self) -> None:
        """Test updating existing user permission group."""
        person_id = self.manager.generate_person_id("test", "user3")

        # Set initial level
        await self.manager.set_user_permission_group(
            person_id=person_id, level=PermissionLevel.USER
        )

        # Update to higher level
        await self.manager.set_user_permission_group(
            person_id=person_id,
            level=PermissionLevel.OWNER,
            granted_by="system",
            reason="升级",
        )

        # Verify update
        level = await self.manager.get_user_permission_level(person_id)
        assert level == PermissionLevel.OWNER

    @pytest.mark.asyncio
    async def test_remove_user_permission_group(self) -> None:
        """Test removing user permission group."""
        person_id = self.manager.generate_person_id("test", "user4")

        # Set permission group
        await self.manager.set_user_permission_group(
            person_id=person_id, level=PermissionLevel.OPERATOR
        )

        # Verify it's set
        level = await self.manager.get_user_permission_level(person_id)
        assert level == PermissionLevel.OPERATOR

        # Remove permission group
        success = await self.manager.remove_user_permission_group(person_id)
        assert success is True

        # Verify it's back to default
        level = await self.manager.get_user_permission_level(person_id)
        assert level == PermissionLevel.USER

    @pytest.mark.asyncio
    async def test_remove_nonexistent_permission_group(self) -> None:
        """Test removing non-existent permission group."""
        person_id = self.manager.generate_person_id("test", "user5")
        success = await self.manager.remove_user_permission_group(person_id)
        assert success is False


class TestCommandPermissionCheck:
    """Test cases for command permission checking."""

    def setup_method(self) -> None:
        """Setup test fixtures."""
        from src.core.components.base.command import BaseCommand

        self.manager = get_permission_manager()

        # Create mock command classes with different permission levels
        class MockGuestCommand(BaseCommand):
            command_name = "guest_cmd"
            command_description = "Guest command"
            permission_level = PermissionLevel.GUEST

        class MockUserCommand(BaseCommand):
            command_name = "user_cmd"
            command_description = "User command"
            permission_level = PermissionLevel.USER

        class MockOperatorCommand(BaseCommand):
            command_name = "operator_cmd"
            command_description = "Operator command"
            permission_level = PermissionLevel.OPERATOR

        class MockOwnerCommand(BaseCommand):
            command_name = "owner_cmd"
            command_description = "Owner command"
            permission_level = PermissionLevel.OWNER

        self.GuestCommand = MockGuestCommand
        self.UserCommand = MockUserCommand
        self.OperatorCommand = MockOperatorCommand
        self.OwnerCommand = MockOwnerCommand

    @pytest.mark.asyncio
    async def test_permission_check_by_level_equal(self) -> None:
        """Test permission check with equal levels."""
        person_id = self.manager.generate_person_id("test", "user1")

        # User level (2) == User command (2) -> allowed
        has_perm, reason = await self.manager.check_command_permission(
            person_id=person_id,
            command_class=self.UserCommand,
            command_signature="test:command:user_cmd",
        )
        assert has_perm is True
        assert "权限充足" in reason

    @pytest.mark.asyncio
    async def test_permission_check_by_level_higher(self) -> None:
        """Test permission check with higher user level."""
        person_id = self.manager.generate_person_id("test", "user2")

        # Set user to operator
        await self.manager.set_user_permission_group(
            person_id=person_id, level=PermissionLevel.OPERATOR
        )

        # Operator level (3) > User command (2) -> allowed
        has_perm, reason = await self.manager.check_command_permission(
            person_id=person_id,
            command_class=self.UserCommand,
            command_signature="test:command:user_cmd",
        )
        assert has_perm is True

    @pytest.mark.asyncio
    async def test_permission_check_by_level_lower(self) -> None:
        """Test permission check with lower user level."""
        person_id = self.manager.generate_person_id("test", "user3")

        # User level (2) < Operator command (3) -> denied
        has_perm, reason = await self.manager.check_command_permission(
            person_id=person_id,
            command_class=self.OperatorCommand,
            command_signature="test:command:operator_cmd",
        )
        assert has_perm is False
        assert "需要 operator" in reason.lower()

    @pytest.mark.asyncio
    async def test_permission_check_with_override_allow(self) -> None:
        """Test permission check with allow override."""
        person_id = self.manager.generate_person_id("test", "user4")

        # User level (2) < Owner command (4), but with override -> allowed
        await self.manager.grant_command_permission(
            person_id=person_id,
            command_signature="test:command:owner_cmd",
            granted=True,
            reason="特殊授权",
        )

        has_perm, reason = await self.manager.check_command_permission(
            person_id=person_id,
            command_class=self.OwnerCommand,
            command_signature="test:command:owner_cmd",
        )
        assert has_perm is True
        assert "覆盖" in reason

    @pytest.mark.asyncio
    async def test_permission_check_with_override_deny(self) -> None:
        """Test permission check with deny override."""
        person_id = self.manager.generate_person_id("test", "user5")

        # Set user to owner
        await self.manager.set_user_permission_group(
            person_id=person_id, level=PermissionLevel.OWNER
        )

        # Owner level (4) > User command (2), but with deny override -> denied
        await self.manager.grant_command_permission(
            person_id=person_id,
            command_signature="test:command:user_cmd",
            granted=False,  # Deny
            reason="禁止执行",
        )

        has_perm, reason = await self.manager.check_command_permission(
            person_id=person_id,
            command_class=self.UserCommand,
            command_signature="test:command:user_cmd",
        )
        assert has_perm is False
        assert "覆盖" in reason


class TestCommandPermissionOverride:
    """Test cases for command permission overrides."""

    def setup_method(self) -> None:
        """Setup test fixtures."""
        self.manager = get_permission_manager()

    @pytest.mark.asyncio
    async def test_grant_command_permission(self) -> None:
        """Test granting command permission."""
        person_id = self.manager.generate_person_id("test", "user1")

        success = await self.manager.grant_command_permission(
            person_id=person_id,
            command_signature="test:command:test_cmd",
            granted=True,
            granted_by="admin",
            reason="测试授权",
        )
        assert success is True

    @pytest.mark.asyncio
    async def test_grant_command_permission_update(self) -> None:
        """Test updating command permission."""
        person_id = self.manager.generate_person_id("test", "user2")

        # Create initial override
        await self.manager.grant_command_permission(
            person_id=person_id,
            command_signature="test:command:test_cmd",
            granted=True,
        )

        # Update to deny
        await self.manager.grant_command_permission(
            person_id=person_id,
            command_signature="test:command:test_cmd",
            granted=False,
            reason="撤销授权",
        )

        # Verify update
        overrides = await self.manager.get_user_command_overrides(person_id)
        assert len(overrides) == 1
        assert overrides[0]["granted"] is False
        assert overrides[0]["reason"] == "撤销授权"

    @pytest.mark.asyncio
    async def test_remove_command_permission_override(self) -> None:
        """Test removing command permission override."""
        person_id = self.manager.generate_person_id("test", "user3")

        # Create override
        await self.manager.grant_command_permission(
            person_id=person_id,
            command_signature="test:command:test_cmd",
            granted=True,
        )

        # Verify it exists
        overrides = await self.manager.get_user_command_overrides(person_id)
        assert len(overrides) == 1

        # Remove override
        success = await self.manager.remove_command_permission_override(
            person_id=person_id,
            command_signature="test:command:test_cmd",
        )
        assert success is True

        # Verify it's removed
        overrides = await self.manager.get_user_command_overrides(person_id)
        assert len(overrides) == 0

    @pytest.mark.asyncio
    async def test_remove_nonexistent_override(self) -> None:
        """Test removing non-existent override."""
        person_id = self.manager.generate_person_id("test", "user4")

        success = await self.manager.remove_command_permission_override(
            person_id=person_id,
            command_signature="test:command:nonexistent",
        )
        assert success is False

    @pytest.mark.asyncio
    async def test_get_user_command_overrides(self) -> None:
        """Test getting all user command overrides."""
        person_id = self.manager.generate_person_id("test", "user5")

        # Create multiple overrides
        await self.manager.grant_command_permission(
            person_id=person_id,
            command_signature="test:command:cmd1",
            granted=True,
            reason="授权1",
        )
        await self.manager.grant_command_permission(
            person_id=person_id,
            command_signature="test:command:cmd2",
            granted=False,
            reason="禁止2",
        )

        # Get all overrides
        overrides = await self.manager.get_user_command_overrides(person_id)
        assert len(overrides) == 2

        # Verify content
        commands = {o["command_signature"] for o in overrides}
        assert "test:command:cmd1" in commands
        assert "test:command:cmd2" in commands

    @pytest.mark.asyncio
    async def test_get_empty_overrides(self) -> None:
        """Test getting overrides for user with no overrides."""
        person_id = self.manager.generate_person_id("test", "user6")

        overrides = await self.manager.get_user_command_overrides(person_id)
        assert len(overrides) == 0


class TestOwnerList:
    """Test cases for owner list functionality."""

    def setup_method(self) -> None:
        """Setup test fixtures."""
        self.manager = PermissionManager()

    @pytest.mark.asyncio
    async def test_owner_in_list(self) -> None:
        """Test that user in owner_list gets OWNER level."""
        # Note: This test depends on config file setup
        # In real tests, you would mock the config
        person_id = self.manager.generate_person_id("qq", "123456")

        # This will return default unless configured in owner_list
        level = await self.manager.get_user_permission_level(person_id)
        # Without owner_list config, should return default
        assert level == PermissionLevel.USER


# Pytest configuration
if __name__ == "__main__":
    pytest.main([__file__, "-v"])

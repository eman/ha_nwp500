"""What external control leaves behind when it never ran (#189).

Kept apart from test_init.py, whose autouse fixture stubs the entity
registry.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.nwp500.const import DOMAIN


class TestControlLeftovers:
    """What external control leaves behind when it never ran (#189)."""

    MAC = "AA:BB:CC:DD:EE:FF"

    async def _entry_with_leftovers(self, hass, options):
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers.storage import Store
        from pytest_homeassistant_custom_component.common import (
            MockConfigEntry,
        )

        from custom_components.nwp500.const import control_storage_key

        entry = MockConfigEntry(domain=DOMAIN, options=options)
        entry.add_to_hass(hass)
        registry = er.async_get(hass)
        control = registry.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{self.MAC}_control_ack",
            config_entry=entry,
        )
        own = registry.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{self.MAC}_tank_upper_temperature",
            config_entry=entry,
        )
        store = Store(hass, 1, control_storage_key(entry.entry_id))
        await store.async_save({"devices": {}})
        return entry, registry, control, own, store

    @pytest.mark.asyncio
    async def test_switching_off_after_a_failed_start_removes_them(
        self, hass, hass_storage
    ):
        from custom_components.nwp500 import async_reload_entry
        from custom_components.nwp500.const import control_storage_key

        entry, registry, control, own, _ = await self._entry_with_leftovers(
            hass, {"control_enabled": False}
        )
        hass.config_entries.async_reload = AsyncMock()

        await async_reload_entry(hass, entry)
        await hass.async_block_till_done()

        assert registry.async_get(control.entity_id) is None
        assert registry.async_get(own.entity_id) is not None
        assert control_storage_key(entry.entry_id) not in hass_storage
        hass.config_entries.async_reload.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_an_entry_that_never_had_it_touches_nothing(
        self, hass, hass_storage
    ):
        """The release before the feature did nothing here either."""
        from custom_components.nwp500 import (
            async_reload_entry,
            async_remove_entry,
        )
        from custom_components.nwp500.const import control_storage_key

        entry, registry, control, _, _ = await self._entry_with_leftovers(
            hass, {}
        )
        hass.config_entries.async_reload = AsyncMock()

        await async_reload_entry(hass, entry)
        await async_remove_entry(hass, entry)
        await hass.async_block_till_done()

        assert registry.async_get(control.entity_id) is not None
        assert control_storage_key(entry.entry_id) in hass_storage

    @pytest.mark.asyncio
    async def test_deleting_the_integration_removes_its_stored_state(
        self, hass, hass_storage
    ):
        from homeassistant.helpers import issue_registry as ir

        from custom_components.nwp500 import async_remove_entry
        from custom_components.nwp500.const import control_storage_key

        entry, *_ = await self._entry_with_leftovers(
            hass, {"control_enabled": True}
        )
        issue = f"control_start_failed_{entry.entry_id}"
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="control_start_failed",
        )

        await async_remove_entry(hass, entry)
        await hass.async_block_till_done()

        assert control_storage_key(entry.entry_id) not in hass_storage
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is None

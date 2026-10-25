"""Keeps the plan entity option on the scheduler's plan entity.

Renamed in Home Assistant, the option follows it to its new id; missing
from Home Assistant once it has started, a Repairs issue says so until it
is back. What the feature does with the plan in force while the entity is
missing is unchanged (spec section 6.1): it stays in force.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import (
    async_track_entity_registry_updated_event,
    async_track_state_change_event,
)
from homeassistant.helpers.start import async_at_started

from ..const import (
    CONF_CONTROL_INTENT_ENTITY,
    DOMAIN,
    ISSUE_CONTROL_PLAN_ENTITY_MISSING,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry


def plan_entity_issue_id(entry_id: str) -> str:
    """The Repairs issue for an entry's missing plan entity."""
    return f"{ISSUE_CONTROL_PLAN_ENTITY_MISSING}_{entry_id}"


class PlanEntityWatch:
    """Follows the plan entity's renames, and says when it is missing."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Nothing watched until `async_start`."""
        self.hass = hass
        self.entry = entry
        self.entity_id: str | None = entry.options.get(
            CONF_CONTROL_INTENT_ENTITY
        )
        self._unsubs: list[CALLBACK_TYPE] = []

    @callback
    def async_start(self) -> None:
        """Watch the entity; check for it once Home Assistant runs."""
        if not self.entity_id:
            return
        self._unsubs = [
            async_track_entity_registry_updated_event(
                self.hass, self.entity_id, self._registry_updated
            ),
            async_track_state_change_event(
                self.hass, [self.entity_id], self._state_changed
            ),
            # At start-up an entity may not have its state yet: judged
            # once everything has loaded.
            async_at_started(self.hass, self._started),
        ]

    @callback
    def async_stop(self) -> None:
        """Stop watching, and withdraw the issue.

        A start judges the entity again at once, so an issue left by an
        entry that stays unloaded (disabled, or failing to set up) would
        only go stale.
        """
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []
        ir.async_delete_issue(
            self.hass, DOMAIN, plan_entity_issue_id(self.entry.entry_id)
        )

    @callback
    def _started(self, _hass: HomeAssistant) -> None:
        entity_id = self.entity_id or ""
        known = (
            er.async_get(self.hass).async_get(entity_id) is not None
            or self.hass.states.get(entity_id) is not None
        )
        self._report(missing=not known)

    @callback
    def _state_changed(self, event: Event[Any]) -> None:
        if event.data.get("new_state") is not None:
            self._report(missing=False)
        elif er.async_get(self.hass).async_get(self.entity_id or "") is None:
            # Gone, with no registry entry to say so (a YAML entity with
            # no unique id).
            self._report(missing=True)

    @callback
    def _registry_updated(self, event: Event[Any]) -> None:
        data = event.data
        if data["action"] == "remove":
            self._report(missing=True)
        elif data["action"] == "update" and "old_entity_id" in data:
            # Renamed: the option follows, and the entry reloads with it.
            self.hass.config_entries.async_update_entry(
                self.entry,
                options={
                    **self.entry.options,
                    CONF_CONTROL_INTENT_ENTITY: data["entity_id"],
                },
            )

    @callback
    def _report(self, *, missing: bool) -> None:
        issue_id = plan_entity_issue_id(self.entry.entry_id)
        if not missing:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_CONTROL_PLAN_ENTITY_MISSING,
            translation_placeholders={"entity_id": self.entity_id or ""},
        )

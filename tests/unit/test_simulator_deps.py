"""The simulator's packages: checked, and installed only on request."""

from __future__ import annotations

import importlib.metadata
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.requirements import RequirementsNotFound
from packaging.requirements import Requirement

from custom_components.nwp500 import simulator_deps
from custom_components.nwp500.simulator_deps import (
    REASON_INSTALL_FAILED,
    REASON_MISSING,
    SIMULATOR_REQUIREMENTS,
    async_ensure,
)

ALL = tuple(SIMULATOR_REQUIREMENTS)


def _in_sim_extra(requirement: Requirement) -> bool:
    marker = requirement.marker
    return (
        marker is not None
        and marker.evaluate({"extra": "sim"})
        and not marker.evaluate({"extra": ""})
    )


def test_the_requirements_are_the_librarys_sim_extra():
    """Held to the extra the pinned nwp500-python declares."""
    declared = [
        Requirement(text)
        for text in importlib.metadata.requires("nwp500-python") or []
    ]
    extra = {
        requirement.name: str(requirement.specifier)
        for requirement in declared
        if _in_sim_extra(requirement)
    }
    ours = {
        Requirement(text).name: str(Requirement(text).specifier)
        for text in SIMULATOR_REQUIREMENTS
    }
    assert ours == extra


@pytest.mark.asyncio
async def test_present_packages_install_nothing(hass: HomeAssistant):
    with (
        patch.object(simulator_deps, "_missing", return_value=()),
        patch.object(
            simulator_deps, "async_process_requirements", AsyncMock()
        ) as install,
    ):
        result = await async_ensure(hass)

    assert result.ok
    install.assert_not_awaited()


@pytest.mark.asyncio
async def test_skip_pip_installs_nothing(hass: HomeAssistant):
    hass.config.skip_pip = True
    with (
        patch.object(simulator_deps, "_missing", return_value=ALL),
        patch.object(
            simulator_deps, "async_process_requirements", AsyncMock()
        ) as install,
    ):
        result = await async_ensure(hass)

    assert not result.ok
    assert result.reason == REASON_MISSING
    assert result.missing == ALL
    install.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_packages_are_installed(hass: HomeAssistant):
    hass.config.skip_pip = False
    with (
        patch.object(simulator_deps, "_missing", side_effect=[ALL[1:], ()]),
        patch.object(
            simulator_deps, "async_process_requirements", AsyncMock()
        ) as install,
    ):
        result = await async_ensure(hass)

    assert result.ok
    install.assert_awaited_once_with(
        hass, "nwp500", list(ALL[1:]), is_built_in=False
    )


@pytest.mark.asyncio
async def test_a_failed_install_says_so(hass: HomeAssistant):
    hass.config.skip_pip = False
    with (
        patch.object(simulator_deps, "_missing", return_value=ALL),
        patch.object(
            simulator_deps,
            "async_process_requirements",
            AsyncMock(side_effect=RequirementsNotFound("nwp500", list(ALL))),
        ),
    ):
        result = await async_ensure(hass)

    assert not result.ok
    assert result.reason == REASON_INSTALL_FAILED


@pytest.mark.asyncio
async def test_an_install_that_leaves_them_missing_failed(hass: HomeAssistant):
    hass.config.skip_pip = False
    with (
        patch.object(simulator_deps, "_missing", return_value=ALL),
        patch.object(simulator_deps, "async_process_requirements", AsyncMock()),
    ):
        result = await async_ensure(hass)

    assert not result.ok
    assert result.reason == REASON_INSTALL_FAILED
    assert result.missing == ALL

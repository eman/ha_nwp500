"""The hot water simulator's packages.

Whether they are there, and getting them when they are not.

The simulator needs nwp500-python's `sim` extra (numpy, pandas, scipy).
They are not in the manifest, so an install without the simulator never
gets them. They are installed here only when the simulator is switched on.

The packages are listed by name rather than as `nwp500-python[sim]`:
Home Assistant checks a requirement by its distribution's version only, so
an extra on an installed nwp500-python would be taken as satisfied and
install nothing.

This module imports nothing heavy, so the setup flow and the options flow
can use it with the simulator off.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
from dataclasses import dataclass

from homeassistant.core import HomeAssistant
from homeassistant.requirements import (
    RequirementsNotFound,
    async_process_requirements,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# nwp500-python's `sim` extra, as ranges: Home Assistant's own constraints
# (package_constraints.txt) pin numpy and pandas, and an exact pin here
# would conflict with them on the next Home Assistant release. A test holds
# this to the extra the pinned nwp500-python declares.
SIMULATOR_REQUIREMENTS: tuple[str, ...] = (
    "numpy>=1.24.0",
    "pandas>=2.0.0",
    "scipy>=1.11.0",
)

# The module each requirement provides.
_MODULES: dict[str, str] = {
    "numpy>=1.24.0": "numpy",
    "pandas>=2.0.0": "pandas",
    "scipy>=1.11.0": "scipy",
}

# The install in progress, shared: a flow closed during it, or set-up
# cancelled, must not cancel pip, or the next attempt would run a second pip
# into the same packages while the first is still writing them.
_DATA_INSTALL = f"{DOMAIN}_simulator_install"

# Why the packages are not there.
REASON_MISSING = "missing"
REASON_INSTALL_FAILED = "install_failed"


def package_names(requirements: tuple[str, ...]) -> str:
    """The packages of `requirements`, for people: "pandas, scipy"."""
    return ", ".join(_MODULES[r] for r in requirements) or "-"


@dataclass(frozen=True)
class DepsResult:
    """Whether the simulator's packages are available, and if not, why."""

    ok: bool
    #: REASON_MISSING (not installed, and Home Assistant installs nothing:
    #: skip_pip) or REASON_INSTALL_FAILED.
    reason: str | None = None
    #: The packages that are not there.
    missing: tuple[str, ...] = ()


def _missing() -> tuple[str, ...]:
    """The requirements whose module cannot be found.

    Runs in an executor: finding a module reads the file system.
    """
    return tuple(
        requirement
        for requirement in SIMULATOR_REQUIREMENTS
        if importlib.util.find_spec(_MODULES[requirement]) is None
    )


async def async_missing(hass: HomeAssistant) -> tuple[str, ...]:
    """The simulator's packages that are not installed."""
    return await hass.async_add_import_executor_job(_missing)


async def async_ensure(hass: HomeAssistant) -> DepsResult:
    """Make the simulator's packages available, installing them if needed.

    Every caller shares one install, and cancelling a caller leaves it
    running. Home Assistant remembers a failed install until it restarts,
    so a second try in the same run fails at once.
    """
    task: asyncio.Task[DepsResult] | None = hass.data.get(_DATA_INSTALL)
    if task is None or task.done():
        task = hass.async_create_background_task(
            _async_ensure(hass), "nwp500 simulator packages"
        )
        hass.data[_DATA_INSTALL] = task
    return await asyncio.shield(task)


async def _async_ensure(hass: HomeAssistant) -> DepsResult:
    missing = await async_missing(hass)
    if not missing:
        return DepsResult(ok=True)
    if hass.config.skip_pip:
        _LOGGER.warning(
            "The hot water simulator needs %s, and Home Assistant is set "
            "not to install packages (skip_pip)",
            ", ".join(missing),
        )
        return DepsResult(ok=False, reason=REASON_MISSING, missing=missing)
    try:
        await async_process_requirements(
            hass, DOMAIN, list(missing), is_built_in=False
        )
    except RequirementsNotFound:
        _LOGGER.warning(
            "Could not install %s for the hot water simulator",
            ", ".join(missing),
        )
        return DepsResult(
            ok=False, reason=REASON_INSTALL_FAILED, missing=missing
        )
    still = await async_missing(hass)
    if still:
        return DepsResult(ok=False, reason=REASON_INSTALL_FAILED, missing=still)
    return DepsResult(ok=True)

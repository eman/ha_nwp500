"""Tests for NWP500DataUpdateCoordinator."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.nwp500.coordinator import NWP500DataUpdateCoordinator


@pytest.fixture
def mock_entry():
    """Mock ConfigEntry."""
    entry = MagicMock()
    entry.options = {}
    entry.data = {"email": "test@example.com", "password": "password"}
    return entry


@pytest.fixture
def coordinator(mock_hass, mock_entry):
    """Create NWP500DataUpdateCoordinator instance."""
    with patch(
        "custom_components.nwp500.coordinator.DataUpdateCoordinator.__init__"
    ):
        coordinator = NWP500DataUpdateCoordinator(mock_hass, mock_entry)
        coordinator.hass = mock_hass
        coordinator.data = {}
        return coordinator


def _make_disconnected_mqtt_manager(
    last_reconnect_offset: float = -9999.0,
) -> MagicMock:
    """Return a mock mqtt_manager that reports as disconnected."""
    mgr = MagicMock()
    mgr.is_connected = False
    mgr.last_reconnect_time = time.time() + last_reconnect_offset
    mgr.force_reconnect = AsyncMock(return_value=True)
    return mgr


def test_on_device_status_update_schedules_loop_task(coordinator, mock_hass):
    """Test that _on_device_status_update schedules a task in the event loop."""
    mac = "AA:BB:CC:DD:EE:FF"
    status = MagicMock()

    coordinator._on_device_status_update(mac, status)

    # Verify call_soon_threadsafe was called with the handler
    mock_hass.loop.call_soon_threadsafe.assert_called_once()
    args = mock_hass.loop.call_soon_threadsafe.call_args[0]
    assert args[0] == coordinator._handle_status_update_in_loop
    assert args[1] == mac
    assert args[2] == status


def test_handle_status_update_in_loop(coordinator):
    """Test that _handle_status_update_in_loop updates data and notifies listeners."""
    mac = "AA:BB:CC:DD:EE:FF"
    status = MagicMock()
    coordinator.data = {
        mac: {"device": MagicMock(), "status": None, "last_update": None}
    }
    coordinator.async_update_listeners = MagicMock()

    coordinator._handle_status_update_in_loop(mac, status)

    assert coordinator.data[mac]["status"] == status
    assert coordinator.data[mac]["last_update"] is not None
    coordinator.async_update_listeners.assert_called_once()


def test_on_device_feature_update_schedules_loop_task(coordinator, mock_hass):
    """Test that _on_device_feature_update schedules a task in the event loop."""
    mac = "AA:BB:CC:DD:EE:FF"
    feature = MagicMock()

    coordinator._on_device_feature_update(mac, feature)

    # Verify call_soon_threadsafe was called with the handler
    mock_hass.loop.call_soon_threadsafe.assert_called_once()
    args = mock_hass.loop.call_soon_threadsafe.call_args[0]
    assert args[0] == coordinator._handle_feature_update_in_loop
    assert args[1] == mac
    assert args[2] == feature


def test_handle_feature_update_in_loop(coordinator):
    """Test that _handle_feature_update_in_loop updates device_features."""
    mac = "AA:BB:CC:DD:EE:FF"
    feature = MagicMock()
    # Mock model_dump to avoid issues if it's called
    feature.model_dump = MagicMock(return_value={})

    coordinator._handle_feature_update_in_loop(mac, feature)

    assert coordinator.device_features[mac] == feature


@pytest.mark.asyncio
async def test_async_update_data_syncs_unit_system(coordinator, mock_hass):
    """Test that _async_update_data synchronizes the unit system."""
    from homeassistant.const import UnitOfTemperature

    mock_hass.config.units.temperature_unit = UnitOfTemperature.CELSIUS
    coordinator.unit_system = "metric"
    # Set auth_client to mock to avoid _setup_clients() call which triggers network
    coordinator.auth_client = AsyncMock()

    # We need to mock the module that is imported inside the function
    with (
        patch("nwp500.unit_system.set_unit_system") as mock_set_unit_system,
        patch(
            "custom_components.nwp500.coordinator.DataUpdateCoordinator._async_update_data",
            side_effect=lambda: None,
        ),
    ):
        # We can't easily mock the super()._async_update_data() call inside the method if we don't mock the method itself
        # But we want to test the method logic.
        # Since DataUpdateCoordinator._async_update_data is not called in the implementation of NWP500DataUpdateCoordinator._async_update_data
        # (it overrides it completely without calling super), we don't need to patch it.
        pass

    with patch("nwp500.unit_system.set_unit_system") as mock_set_unit_system:
        await coordinator._async_update_data()

        mock_set_unit_system.assert_called_once_with("metric")


# ---------------------------------------------------------------------------
# MQTT disconnection handling tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_update_returns_cached_data_when_mqtt_disconnected(
    coordinator, mock_hass
):
    """When MQTT is not connected, cached data is returned without sending requests."""
    cached = {
        "mac1": {
            "device": MagicMock(),
            "status": MagicMock(),
            "last_update": 1.0,
        }
    }
    coordinator.data = cached
    coordinator.auth_client = AsyncMock()
    coordinator.mqtt_manager = _make_disconnected_mqtt_manager()

    mock_hass.config.units.temperature_unit = "°F"
    coordinator.unit_system = "us_customary"

    with patch("nwp500.unit_system.set_unit_system"):
        result = await coordinator._async_update_data()

    assert result == cached
    coordinator.mqtt_manager.force_reconnect.assert_not_called()


@pytest.mark.asyncio
async def test_async_update_increments_consecutive_timeouts_when_disconnected(
    coordinator, mock_hass
):
    """Each update cycle while disconnected increments _consecutive_timeouts."""
    coordinator.data = {}
    coordinator.auth_client = AsyncMock()
    coordinator.mqtt_manager = _make_disconnected_mqtt_manager()
    coordinator._consecutive_timeouts = 0

    mock_hass.config.units.temperature_unit = "°F"
    coordinator.unit_system = "us_customary"

    with patch("nwp500.unit_system.set_unit_system"):
        await coordinator._async_update_data()
        assert coordinator._consecutive_timeouts == 1

        await coordinator._async_update_data()
        assert coordinator._consecutive_timeouts == 2


@pytest.mark.asyncio
async def test_async_update_does_not_force_reconnect_while_library_disconnected(
    coordinator, mock_hass
):
    """Disconnected cycles rely on the library's internal reconnect loop."""
    coordinator.data = {}
    coordinator.auth_client = AsyncMock()
    coordinator.mqtt_manager = _make_disconnected_mqtt_manager(
        last_reconnect_offset=-9999.0
    )
    coordinator._consecutive_timeouts = 2

    mock_hass.config.units.temperature_unit = "°F"
    coordinator.unit_system = "us_customary"

    with patch("nwp500.unit_system.set_unit_system"):
        await coordinator._async_update_data()

    coordinator.mqtt_manager.force_reconnect.assert_not_called()
    assert coordinator._consecutive_timeouts == 3


@pytest.mark.asyncio
async def test_async_update_triggers_force_reconnect_after_request_timeouts(
    coordinator, mock_hass
):
    """Repeated request timeouts still trigger a forced reconnect."""
    device = MagicMock()
    device.device_info.mac_address = "aabbcc001122"
    coordinator.devices = [device]
    coordinator.data = {}
    coordinator.auth_client = AsyncMock()
    coordinator.mqtt_manager = MagicMock()
    coordinator.mqtt_manager.is_connected = True
    coordinator.mqtt_manager.connected_since = 1000.0
    coordinator._mqtt_connected_since = 1000.0
    coordinator.mqtt_manager.last_reconnect_time = time.time() - 9999.0
    coordinator.mqtt_manager.request_status = AsyncMock(
        side_effect=TimeoutError
    )
    coordinator.mqtt_manager.request_device_info = AsyncMock()
    coordinator.mqtt_manager.force_reconnect = AsyncMock(return_value=True)
    coordinator._consecutive_timeouts = 2
    coordinator._reconnect_task = None

    mock_hass.config.units.temperature_unit = "°F"
    coordinator.unit_system = "us_customary"

    with patch("nwp500.unit_system.set_unit_system"):
        await coordinator._async_update_data()

    coordinator.mqtt_manager.force_reconnect.assert_called_once()
    assert coordinator._consecutive_timeouts == 0


@pytest.mark.asyncio
async def test_async_update_skips_force_reconnect_within_min_interval(
    coordinator, mock_hass
):
    """Rate-limited reconnection attempts skip but don't reset timeout counter."""
    device = MagicMock()
    device.device_info.mac_address = "aabbcc001122"
    coordinator.devices = [device]
    coordinator.data = {}
    coordinator.auth_client = AsyncMock()
    coordinator.mqtt_manager = MagicMock()
    coordinator.mqtt_manager.is_connected = True
    coordinator.mqtt_manager.connected_since = 1000.0
    coordinator._mqtt_connected_since = 1000.0
    coordinator.mqtt_manager.last_reconnect_time = time.time() - 5.0
    coordinator.mqtt_manager.request_status = AsyncMock(
        side_effect=TimeoutError
    )
    coordinator.mqtt_manager.request_device_info = AsyncMock()
    coordinator.mqtt_manager.force_reconnect = AsyncMock(return_value=True)
    coordinator._consecutive_timeouts = 2
    coordinator._reconnect_task = None

    mock_hass.config.units.temperature_unit = "°F"
    coordinator.unit_system = "us_customary"

    with patch("nwp500.unit_system.set_unit_system"):
        await coordinator._async_update_data()

    # Reconnection should not be attempted due to rate limiting (5s < 30s)
    coordinator.mqtt_manager.force_reconnect.assert_not_called()
    # Counter should accumulate to 3 (not reset during rate limit)
    assert coordinator._consecutive_timeouts == 3


@pytest.mark.asyncio
async def test_async_update_resets_timeouts_on_new_mqtt_connection(
    coordinator, mock_hass
):
    """A single timeout right after reconnecting should not force-reconnect.

    If the coordinator accumulated a stale consecutive-timeout count while
    disconnected, a new MQTT session (detected via a changed
    ``connected_since`` value) must reset that counter before any new
    request timeouts count toward the forced-reconnect threshold.
    """
    device = MagicMock()
    device.device_info.mac_address = "aabbcc001122"
    coordinator.devices = [device]
    coordinator.data = {}
    coordinator.auth_client = AsyncMock()

    # Simulate a prior prolonged disconnect that pushed the counter to the
    # forced-reconnect threshold, and no connection observed yet.
    coordinator._consecutive_timeouts = 3
    coordinator._mqtt_connected_since = None

    coordinator.mqtt_manager = MagicMock()
    coordinator.mqtt_manager.is_connected = True
    # New connection session established (different from what we last saw).
    coordinator.mqtt_manager.connected_since = 2000.0
    coordinator.mqtt_manager.last_reconnect_time = time.time() - 9999.0
    coordinator.mqtt_manager.request_status = AsyncMock(
        side_effect=TimeoutError
    )
    coordinator.mqtt_manager.request_device_info = AsyncMock()
    coordinator.mqtt_manager.force_reconnect = AsyncMock(return_value=True)
    coordinator._reconnect_task = None

    mock_hass.config.units.temperature_unit = "°F"
    coordinator.unit_system = "us_customary"

    with patch("nwp500.unit_system.set_unit_system"):
        await coordinator._async_update_data()

    # The stale counter should have been reset on the new connection, so a
    # single subsequent timeout leaves the count at 1, not 4, and does not
    # trigger a forced reconnect.
    coordinator.mqtt_manager.force_reconnect.assert_not_called()
    assert coordinator._consecutive_timeouts == 1
    assert coordinator._mqtt_connected_since == 2000.0


@pytest.mark.asyncio
async def test_async_update_resets_consecutive_timeouts_on_successful_request(
    coordinator, mock_hass
):
    """A successful MQTT request resets _consecutive_timeouts to 0."""
    device = MagicMock()
    device.device_info.mac_address = "aabbcc001122"
    coordinator.devices = [device]
    coordinator.data = {}
    coordinator.auth_client = AsyncMock()
    coordinator._consecutive_timeouts = 5

    mock_mqtt = MagicMock()
    mock_mqtt.is_connected = True
    mock_mqtt.request_status = AsyncMock(return_value=True)
    mock_mqtt.request_device_info = AsyncMock()
    mock_mqtt.connected_since = 1000.0
    coordinator.mqtt_manager = mock_mqtt
    # Pin the session marker so the "MQTT reconnected" reset earlier in the
    # method does not fire. Without this the counter is already zero before
    # the request loop runs, and this test passes for the wrong reason.
    coordinator._mqtt_connected_since = 1000.0

    mock_hass.config.units.temperature_unit = "°F"
    coordinator.unit_system = "us_customary"

    with patch("nwp500.unit_system.set_unit_system"):
        await coordinator._async_update_data()

    assert coordinator._consecutive_timeouts == 0


@pytest.mark.asyncio
async def test_async_update_starts_reauth_after_library_reconnect_failure(
    coordinator, mock_hass
):
    """Library reconnection_failed is surfaced through UpdateFailed + reauth."""
    coordinator.auth_client = AsyncMock()
    coordinator.mqtt_manager = _make_disconnected_mqtt_manager()
    coordinator.entry.async_start_reauth = MagicMock()
    coordinator._mqtt_reconnection_failed_attempts = 4

    mock_hass.config.units.temperature_unit = "°F"
    coordinator.unit_system = "us_customary"

    with (
        patch("nwp500.unit_system.set_unit_system"),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._async_update_data()

    coordinator.entry.async_start_reauth.assert_called_once_with(mock_hass)
    assert coordinator._mqtt_reconnection_failed_attempts is None


# ---------------------------------------------------------------------------
# Device-routed commands
#
# async_control_device, async_send_command, async_update_reservations and
# async_request_reservations all share the same guard shape: no MQTT manager
# or an unknown MAC must fail closed rather than raise.
# ---------------------------------------------------------------------------


def _connected_mqtt_manager() -> MagicMock:
    mgr = MagicMock()
    mgr.is_connected = True
    mgr.send_command = AsyncMock(return_value=True)
    mgr.request_device_info = AsyncMock()
    return mgr


def _device(mac: str) -> MagicMock:
    device = MagicMock()
    device.device_info.mac_address = mac
    return device


MAC = "AA:BB:CC:DD:EE:FF"


def _api_client(tou_info: dict | None = None) -> MagicMock:
    """A mock REST client whose TOU read returns a TOUInfo-shaped dump."""
    api = MagicMock()
    dump = MagicMock()
    dump.model_dump.return_value = tou_info or {
        "name": "EV Rate A",
        "utility": "PG&E",
        "zip_code": 94103,
        "schedule": [],
    }
    api.get_tou_info = AsyncMock(return_value=dump)
    return api


@pytest.fixture
def wired(coordinator):
    """A coordinator with one known device and a connected MQTT manager."""
    coordinator.mqtt_manager = _connected_mqtt_manager()
    coordinator.api_client = _api_client()
    device = _device(MAC)
    device.device_info.additional_value = "ADDL"
    coordinator.devices = [device]
    coordinator._update_device_cache()
    return coordinator


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("async_control_device", (MAC, "set_power")),
        ("async_send_command", (MAC, "set_power")),
        ("async_update_reservations", (MAC, [])),
        ("async_request_reservations", (MAC,)),
        ("async_configure_tou_schedule", (MAC, [])),
        ("async_request_device_info", (MAC,)),
    ],
)
@pytest.mark.asyncio
async def test_commands_fail_closed_without_mqtt(wired, method, args):
    """Every device command refuses rather than raising when MQTT is absent.

    Uses a coordinator that *does* know the device, so the MQTT guard is the
    only thing that can produce False here -- otherwise the unknown-device
    guard would mask its removal.
    """
    wired.mqtt_manager = None

    assert await getattr(wired, method)(*args) is False


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("async_control_device", ("99:99:99:99:99:99", "set_power")),
        ("async_send_command", ("99:99:99:99:99:99", "set_power")),
        ("async_update_reservations", ("99:99:99:99:99:99", [])),
        ("async_request_reservations", ("99:99:99:99:99:99",)),
        ("async_configure_tou_schedule", ("99:99:99:99:99:99", [])),
        ("async_request_tou_settings", ("99:99:99:99:99:99",)),
    ],
)
@pytest.mark.asyncio
async def test_commands_fail_closed_for_unknown_device(wired, method, args):
    """An unrecognised MAC never reaches the MQTT layer."""
    assert await getattr(wired, method)(*args) is False
    wired.mqtt_manager.send_command.assert_not_called()


@pytest.mark.asyncio
async def test_control_device_delegates_kwargs(wired):
    """Control arguments are forwarded verbatim to the MQTT manager."""
    assert await wired.async_control_device(MAC, "set_power", power_on=True)

    wired.mqtt_manager.send_command.assert_awaited_once_with(
        wired._devices_by_mac[MAC], "set_power", power_on=True
    )


@pytest.mark.asyncio
async def test_send_command_delegates_kwargs(wired):
    """async_send_command forwards the command name and arguments."""
    assert await wired.async_send_command(MAC, "reset_air_filter", extra=1)

    wired.mqtt_manager.send_command.assert_awaited_once_with(
        wired._devices_by_mac[MAC], "reset_air_filter", extra=1
    )


@pytest.mark.asyncio
async def test_update_reservations_passes_list_and_enabled_flag(wired):
    """The reservation list and its enable flag reach the device together."""
    entries = [{"week": 1, "hour": 6, "min": 0}]

    assert await wired.async_update_reservations(MAC, entries, enabled=False)

    wired.mqtt_manager.send_command.assert_awaited_once_with(
        wired._devices_by_mac[MAC],
        "update_reservations",
        reservations=entries,
        enabled=False,
    )


# ---------------------------------------------------------------------------
# TOU commands additionally require a controller serial from device info
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method", ["async_configure_tou_schedule", "async_request_tou_settings"]
)
@pytest.mark.asyncio
async def test_tou_refuses_before_device_info_arrives(wired, method):
    """Without device features there is no controller serial, so no command."""
    wired.device_features.clear()

    args = (MAC, []) if "configure" in method else (MAC,)
    assert await getattr(wired, method)(*args) is False
    wired.mqtt_manager.send_command.assert_not_called()
    wired.api_client.get_tou_info.assert_not_called()


@pytest.mark.parametrize(
    "method", ["async_configure_tou_schedule", "async_request_tou_settings"]
)
@pytest.mark.asyncio
async def test_tou_refuses_when_serial_is_blank(wired, method):
    """Features that carry an empty serial are treated as not-yet-known."""
    features = MagicMock()
    features.controller_serial_number = ""
    wired.device_features[MAC] = features

    args = (MAC, []) if "configure" in method else (MAC,)
    assert await getattr(wired, method)(*args) is False
    wired.mqtt_manager.send_command.assert_not_called()
    wired.api_client.get_tou_info.assert_not_called()


@pytest.mark.asyncio
async def test_configure_tou_sends_serial_periods_and_flag(wired):
    """A known serial is attached to the TOU configuration command."""
    features = MagicMock()
    features.controller_serial_number = "CTRL-123"
    wired.device_features[MAC] = features
    periods = [{"start": 0, "end": 6}]

    assert await wired.async_configure_tou_schedule(MAC, periods, enabled=True)

    wired.mqtt_manager.send_command.assert_awaited_once_with(
        wired._devices_by_mac[MAC],
        "configure_tou_schedule",
        controller_serial_number="CTRL-123",
        periods=periods,
        enabled=True,
    )


@pytest.mark.asyncio
async def test_request_tou_settings_fails_closed_without_api_client(wired):
    """The TOU read is REST, so it refuses when the API client is absent."""
    wired.api_client = None
    features = MagicMock()
    features.controller_serial_number = "CTRL-123"
    wired.device_features[MAC] = features

    assert await wired.async_request_tou_settings(MAC) is False


@pytest.mark.asyncio
async def test_request_tou_settings_reads_over_rest(wired, mock_hass):
    """The plan is read over REST and published like an MQTT reply.

    The device answers no MQTT read for its TOU schedule, so the plan comes
    from `/device/tou`, keyed by the controller serial number.
    """
    features = MagicMock()
    features.controller_serial_number = "CTRL-123"
    wired.device_features[MAC] = features
    wired.api_client = _api_client(
        {
            "name": "EV Rate A",
            "utility": "PG&E",
            "zip_code": 94103,
            "schedule": [
                {
                    "season": 3087,
                    "intervals": [
                        {
                            "week": 124,
                            "startHour": 0,
                            "startMinute": 0,
                            "endHour": 6,
                            "endMinute": 59,
                            "priceMin": 31794,
                            "priceMax": 31794,
                            "decimalPoint": 5,
                        }
                    ],
                }
            ],
        }
    )
    status = MagicMock()
    status.tou_status = 1
    wired.data = {MAC: {"status": status}}
    wired.async_update_listeners = MagicMock()

    assert await wired.async_request_tou_settings(MAC)

    wired.api_client.get_tou_info.assert_awaited_once_with(
        mac_address=MAC,
        additional_value="ADDL",
        controller_id="CTRL-123",
    )
    wired.mqtt_manager.send_command.assert_not_called()

    stored = wired.tou_schedules[MAC]
    assert stored["name"] == "EV Rate A"
    assert stored["enabled"] is True
    assert stored["reservation"] == [
        {
            "season": 3087,
            "week": 124,
            "start_hour": 0,
            "start_min": 0,
            "end_hour": 6,
            "end_min": 59,
            "price_min": 31794,
            "price_max": 31794,
            "decimal_point": 5,
            "start_time": "00:00",
            "end_time": "06:59",
            "decoded_price_min": 0.31794,
            "decoded_price_max": 0.31794,
        }
    ]
    mock_hass.bus.async_fire.assert_called_once_with(
        "nwp500_tou_updated", {"mac_address": MAC, "tou_data": stored}
    )


@pytest.mark.asyncio
async def test_request_tou_settings_survives_a_malformed_plan(wired):
    """The library leaves each interval a raw dict, unvalidated.

    A non-numeric protocol value therefore reaches the conversion. Left to
    escape it would fail the whole update cycle through the periodic
    refresh, which catches only transport errors.
    """
    features = MagicMock()
    features.controller_serial_number = "CTRL-123"
    wired.device_features[MAC] = features
    wired.api_client = _api_client(
        {
            "schedule": [
                {
                    "season": 3087,
                    "intervals": [{"startHour": "not-a-number"}],
                }
            ]
        }
    )

    assert await wired.async_request_tou_settings(MAC) is False
    assert MAC not in wired.tou_schedules


@pytest.mark.asyncio
async def test_request_tou_settings_reports_a_failed_read(wired):
    """A REST failure is reported, not raised, and stores nothing."""
    features = MagicMock()
    features.controller_serial_number = "CTRL-123"
    wired.device_features[MAC] = features
    wired.api_client.get_tou_info = AsyncMock(side_effect=TimeoutError)

    assert await wired.async_request_tou_settings(MAC) is False
    assert MAC not in wired.tou_schedules


# ---------------------------------------------------------------------------
# async_request_device_info
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_device_info_targets_a_single_device(wired):
    """A MAC argument narrows the request to that device alone."""
    other = _device("11:22:33:44:55:66")
    wired.devices = [wired._devices_by_mac[MAC], other]
    wired._update_device_cache()

    assert await wired.async_request_device_info(MAC) is True

    wired.mqtt_manager.request_device_info.assert_awaited_once_with(
        wired._devices_by_mac[MAC]
    )


@pytest.mark.asyncio
async def test_request_device_info_fans_out_when_no_mac_given(wired):
    """Omitting the MAC requests info for every known device."""
    other = _device("11:22:33:44:55:66")
    wired.devices = [wired._devices_by_mac[MAC], other]
    wired._update_device_cache()

    assert await wired.async_request_device_info() is True
    assert wired.mqtt_manager.request_device_info.await_count == 2


@pytest.mark.asyncio
async def test_request_device_info_unknown_mac_requests_nothing(wired):
    """An unknown MAC must not silently fall back to broadcasting."""
    assert await wired.async_request_device_info("99:99:99:99:99:99") is False
    wired.mqtt_manager.request_device_info.assert_not_called()


@pytest.mark.asyncio
async def test_request_device_info_survives_a_partial_failure(wired):
    """One device failing does not abort the others, and still reports True."""
    other = _device("11:22:33:44:55:66")
    wired.devices = [wired._devices_by_mac[MAC], other]
    wired._update_device_cache()
    wired.mqtt_manager.request_device_info = AsyncMock(
        side_effect=[RuntimeError("boom"), None]
    )

    assert await wired.async_request_device_info() is True
    assert wired.mqtt_manager.request_device_info.await_count == 2


@pytest.mark.asyncio
async def test_request_device_info_reports_total_failure(wired):
    """If every device errors the call reports failure rather than success."""
    wired.mqtt_manager.request_device_info = AsyncMock(
        side_effect=RuntimeError("boom")
    )

    assert await wired.async_request_device_info() is False


# ---------------------------------------------------------------------------
# async_fetch_reservations
#
# set_reservation does a read-modify-write against a full-list replacement,
# so a stale or empty read is a correctness hazard, not just a slow path.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_reservations_returns_the_devices_reply(coordinator):
    """The value delivered over MQTT is handed back to the caller."""
    coordinator.async_update_listeners = MagicMock()
    response = {"reservation_use": 1, "reservation": [{"hour": 6}]}

    async def reply(mac):
        # Stand in for the device answering over MQTT.
        coordinator._handle_reservation_update_in_loop(mac, response)
        return True

    coordinator.async_request_reservations = AsyncMock(side_effect=reply)

    assert await coordinator.async_fetch_reservations(MAC) == response
    # The waiter must not be left behind once it has been satisfied.
    assert MAC not in coordinator._reservation_waiters


@pytest.mark.asyncio
async def test_fetch_reservations_returns_none_when_request_fails(
    coordinator,
):
    """A publish failure short-circuits instead of waiting out the timeout."""
    coordinator.async_request_reservations = AsyncMock(return_value=False)

    # The deadline is the assertion: the default 10s wait must never be
    # entered when the request could not be published at all.
    async with asyncio.timeout(1):
        assert await coordinator.async_fetch_reservations(MAC) is None
    assert MAC not in coordinator._reservation_waiters


@pytest.mark.asyncio
async def test_fetch_reservations_times_out_and_cleans_up(coordinator):
    """A silent device yields None, and leaves no waiter behind."""
    coordinator.async_request_reservations = AsyncMock(return_value=True)

    assert await coordinator.async_fetch_reservations(MAC, timeout=0.01) is None
    assert MAC not in coordinator._reservation_waiters


@pytest.mark.asyncio
async def test_fetch_reservations_removes_only_its_own_waiter(coordinator):
    """Concurrent fetches for one device do not clear each other's waiters."""
    coordinator.async_request_reservations = AsyncMock(return_value=True)

    other: asyncio.Future = asyncio.get_running_loop().create_future()
    coordinator._reservation_waiters[MAC] = [other]

    assert await coordinator.async_fetch_reservations(MAC, timeout=0.01) is None

    # The unrelated waiter survives; only the timed-out one was removed.
    assert coordinator._reservation_waiters[MAC] == [other]


# ---------------------------------------------------------------------------
# Reservation / TOU push handlers
# ---------------------------------------------------------------------------


def test_reservation_update_caches_wakes_waiters_and_fires_event(
    coordinator, mock_hass
):
    """A pushed schedule is cached, released to waiters, and announced."""
    coordinator.async_update_listeners = MagicMock()
    loop = asyncio.new_event_loop()
    try:
        waiter: asyncio.Future = loop.create_future()
        coordinator._reservation_waiters[MAC] = [waiter]
        response = {"reservation_use": 1, "reservation": [{"hour": 6}]}

        coordinator._handle_reservation_update_in_loop(MAC, response)

        assert coordinator.reservation_schedules[MAC] == response
        assert waiter.result() == response
        mock_hass.bus.async_fire.assert_called_once_with(
            "nwp500_reservations_updated",
            {
                "mac_address": MAC,
                "reservation_use": 1,
                "reservations": [{"hour": 6}],
            },
        )
        coordinator.async_update_listeners.assert_called_once()
    finally:
        loop.close()


def test_reservation_update_skips_an_already_resolved_waiter(
    coordinator, mock_hass
):
    """A waiter resolved by an earlier reply is not set twice."""
    coordinator.async_update_listeners = MagicMock()
    loop = asyncio.new_event_loop()
    try:
        waiter: asyncio.Future = loop.create_future()
        waiter.set_result({"first": True})
        coordinator._reservation_waiters[MAC] = [waiter]

        coordinator._handle_reservation_update_in_loop(MAC, {"second": True})

        assert waiter.result() == {"first": True}
        # Without the guard, set_result would raise InvalidStateError, which
        # the handler's broad except would swallow -- silently skipping the
        # event and the listener notification below. Assert it got past them.
        mock_hass.bus.async_fire.assert_called_once()
        coordinator.async_update_listeners.assert_called_once()
    finally:
        loop.close()


def test_reservation_update_swallows_handler_errors(coordinator, mock_hass):
    """A push handler must never propagate into the MQTT callback thread."""
    coordinator.async_update_listeners = MagicMock(
        side_effect=RuntimeError("listener exploded")
    )

    coordinator._handle_reservation_update_in_loop(MAC, {"reservation": []})

    assert coordinator.reservation_schedules[MAC] == {"reservation": []}


def test_tou_update_caches_and_fires_event(coordinator, mock_hass):
    """A pushed TOU schedule is cached and announced on the bus."""
    coordinator.async_update_listeners = MagicMock()
    response = {"tou_use": 1}

    coordinator._handle_tou_update_in_loop(MAC, response)

    assert coordinator.tou_schedules[MAC] == response
    mock_hass.bus.async_fire.assert_called_once_with(
        "nwp500_tou_updated", {"mac_address": MAC, "tou_data": response}
    )
    coordinator.async_update_listeners.assert_called_once()


def test_tou_update_swallows_handler_errors(coordinator, mock_hass):
    """TOU push handling is likewise isolated from callback-thread failures."""
    coordinator.async_update_listeners = MagicMock(
        side_effect=RuntimeError("listener exploded")
    )

    coordinator._handle_tou_update_in_loop(MAC, {"tou_use": 1})

    assert coordinator.tou_schedules[MAC] == {"tou_use": 1}


# ---------------------------------------------------------------------------
# async_shutdown
#
# async_setup_entry calls this on a coordinator whose first refresh raised, so
# it must cope with a half-built object as readily as a fully wired one.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_is_safe_on_a_coordinator_that_never_started(
    coordinator,
):
    """Nothing was wired up, so shutdown must be a no-op rather than a crash.

    This is the path async_setup_entry takes when the very first refresh
    fails before any client exists.
    """
    coordinator.mqtt_manager = None
    coordinator.auth_client = None
    coordinator._reconnect_task = None

    await coordinator.async_shutdown()

    assert coordinator.api_client is None


@pytest.mark.asyncio
async def test_shutdown_releases_clients_and_caches(coordinator):
    """A fully wired coordinator drops every connection and cached mapping."""
    mqtt = MagicMock()
    mqtt.disconnect = AsyncMock()
    auth = MagicMock()
    auth.close = AsyncMock()
    coordinator.mqtt_manager = mqtt
    coordinator.auth_client = auth
    coordinator.api_client = MagicMock()
    coordinator.device_features[MAC] = MagicMock()
    coordinator.reservation_schedules[MAC] = {}
    coordinator.tou_schedules[MAC] = {}

    await coordinator.async_shutdown()

    mqtt.disconnect.assert_awaited_once()
    auth.close.assert_awaited_once()
    assert coordinator.mqtt_manager is None
    assert coordinator.auth_client is None
    assert coordinator.api_client is None
    assert coordinator.device_features == {}
    assert coordinator.reservation_schedules == {}
    assert coordinator.tou_schedules == {}


@pytest.mark.asyncio
async def test_shutdown_cancels_a_pending_reconnect(coordinator):
    """A reconnect in flight is cancelled and awaited, not abandoned."""
    started = asyncio.Event()

    async def never_finishes():
        started.set()
        await asyncio.sleep(3600)

    task = asyncio.create_task(never_finishes())
    await started.wait()
    coordinator._reconnect_task = task
    coordinator.mqtt_manager = None
    coordinator.auth_client = None

    await coordinator.async_shutdown()

    assert task.cancelled()


@pytest.mark.asyncio
async def test_shutdown_leaves_a_finished_reconnect_alone(coordinator):
    """An already-completed reconnect task is not re-awaited."""

    async def done_immediately():
        return None

    task = asyncio.create_task(done_immediately())
    await task
    coordinator._reconnect_task = task
    coordinator.mqtt_manager = None
    coordinator.auth_client = None

    await coordinator.async_shutdown()

    assert not task.cancelled()


@pytest.mark.asyncio
async def test_shutdown_tolerates_a_failing_auth_close(coordinator):
    """A broken auth session must not block the rest of the teardown."""
    coordinator.mqtt_manager = None
    coordinator.auth_client = MagicMock()
    coordinator.auth_client.close = AsyncMock(side_effect=OSError("socket"))
    coordinator.device_features[MAC] = MagicMock()

    await coordinator.async_shutdown()

    assert coordinator.auth_client is None
    assert coordinator.device_features == {}


# ---------------------------------------------------------------------------
# Token persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_tokens_does_nothing_without_an_auth_client(
    coordinator, mock_hass
):
    """There is nothing to persist before authentication has happened."""
    coordinator.auth_client = None

    await coordinator._save_tokens()

    mock_hass.config_entries.async_update_entry.assert_not_called()


@pytest.mark.asyncio
async def test_save_tokens_does_nothing_when_client_holds_no_tokens(
    coordinator, mock_hass
):
    """A client that never obtained tokens must not blank out stored ones."""
    coordinator.auth_client = MagicMock()
    coordinator.auth_client.current_tokens = None

    await coordinator._save_tokens()

    mock_hass.config_entries.async_update_entry.assert_not_called()


@pytest.mark.asyncio
async def test_save_tokens_merges_into_existing_entry_data(
    coordinator, mock_hass, mock_entry
):
    """Credentials already in the entry survive the token write."""
    from custom_components.nwp500.const import CONF_TOKEN_DATA

    tokens = MagicMock()
    tokens.to_dict.return_value = {"access_token": "abc"}
    tokens.expires_at = "2026-01-01T00:00:00Z"
    coordinator.auth_client = MagicMock()
    coordinator.auth_client.current_tokens = tokens

    await coordinator._save_tokens()

    _entry, kwargs = (
        mock_hass.config_entries.async_update_entry.call_args[0],
        mock_hass.config_entries.async_update_entry.call_args[1],
    )
    assert kwargs["data"][CONF_TOKEN_DATA] == {"access_token": "abc"}
    # Existing credentials are preserved, not replaced.
    assert kwargs["data"]["email"] == mock_entry.data["email"]
    assert kwargs["data"]["password"] == mock_entry.data["password"]


# ---------------------------------------------------------------------------
# Unit system changes
#
# A half-applied change would mix Celsius and Fahrenheit readings in a water
# heater, so the cache clear matters more than the bookkeeping around it.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unit_change_clears_every_cache_holding_scaled_values(
    coordinator,
):
    """Cached readings captured in the old scale are discarded outright."""
    coordinator.unit_system = "metric"
    coordinator.mqtt_manager = MagicMock()
    coordinator.data = {MAC: {"status": MagicMock()}}
    coordinator.device_features[MAC] = MagicMock()

    await coordinator._atomic_unit_system_change("us_customary")

    assert coordinator.unit_system == "us_customary"
    assert coordinator.mqtt_manager.unit_system == "us_customary"
    assert coordinator.data == {}
    assert coordinator.device_features == {}
    assert coordinator._unit_change_in_progress is False


@pytest.mark.asyncio
async def test_unit_change_works_without_an_mqtt_manager(coordinator):
    """A change applied before MQTT exists still updates the coordinator."""
    coordinator.mqtt_manager = None
    coordinator.data = None

    await coordinator._atomic_unit_system_change("metric")

    assert coordinator.unit_system == "metric"
    assert coordinator._unit_change_in_progress is False


@pytest.mark.asyncio
async def test_unit_change_clears_its_flag_even_when_it_fails(coordinator):
    """A stuck in-progress flag would wedge every later update."""
    coordinator.mqtt_manager = MagicMock()
    coordinator.device_features = MagicMock()
    coordinator.device_features.clear.side_effect = RuntimeError("boom")
    coordinator.data = {}

    with pytest.raises(RuntimeError):
        await coordinator._atomic_unit_system_change("metric")

    assert coordinator._unit_change_in_progress is False


# ---------------------------------------------------------------------------
# Diagnostic accessors and small helpers
# ---------------------------------------------------------------------------


def test_performance_stats_before_any_update(coordinator):
    """A fresh coordinator reports zeros rather than dividing by zero."""
    assert coordinator.get_performance_stats() == {
        "update_count": 0,
        "average_time": 0.0,
        "slowest_time": 0.0,
        "total_time": 0.0,
    }


def test_performance_stats_average_over_recorded_updates(coordinator):
    """The average is the recorded total divided by the update count."""
    coordinator._update_count = 4
    coordinator._total_update_time = 10.0
    coordinator._slowest_update = 4.0

    stats = coordinator.get_performance_stats()

    assert stats["update_count"] == 4
    assert stats["average_time"] == 2.5
    assert stats["slowest_time"] == 4.0
    assert stats["total_time"] == 10.0


def test_mqtt_telemetry_without_a_manager(coordinator):
    """Telemetry is still readable before MQTT exists, reporting disconnected."""
    coordinator.mqtt_manager = None

    telemetry = coordinator.get_mqtt_telemetry()

    assert telemetry["mqtt_connected"] is False
    assert telemetry["mqtt_connected_since"] is None


def test_mqtt_telemetry_reflects_the_live_connection(coordinator):
    """Connection state and counters come straight from the manager."""
    mgr = MagicMock()
    mgr.is_connected = True
    mgr.connected_since = 1234.0
    coordinator.mqtt_manager = mgr
    coordinator._total_requests_sent = 7
    coordinator._total_responses_received = 6
    coordinator._consecutive_timeouts = 1

    telemetry = coordinator.get_mqtt_telemetry()

    assert telemetry["mqtt_connected"] is True
    assert telemetry["mqtt_connected_since"] == 1234.0
    assert telemetry["total_requests_sent"] == 7
    assert telemetry["total_responses_received"] == 6
    assert telemetry["consecutive_timeouts"] == 1


def test_device_cache_is_rebuilt_from_the_device_list(coordinator):
    """Refreshing the cache drops devices that are no longer present."""
    first, second = _device(MAC), _device("11:22:33:44:55:66")
    coordinator.devices = [first, second]
    coordinator._update_device_cache()
    assert set(coordinator._devices_by_mac) == {MAC, "11:22:33:44:55:66"}

    coordinator.devices = [second]
    coordinator._update_device_cache()
    assert set(coordinator._devices_by_mac) == {"11:22:33:44:55:66"}


def test_field_unit_is_stripped_when_present(coordinator):
    """Units arrive padded from the library and are normalised."""
    status = MagicMock()
    status.get_field_unit.return_value = "  °F  "

    assert coordinator.get_field_unit_safe(status, "dhw_temperature") == "°F"


@pytest.mark.parametrize(
    "returned", [None, "", 42], ids=["none", "empty", "not-a-string"]
)
def test_field_unit_rejects_unusable_values(coordinator, returned):
    """Anything that is not a non-empty string is reported as no unit."""
    status = MagicMock()
    status.get_field_unit.return_value = returned

    assert coordinator.get_field_unit_safe(status, "dhw_temperature") is None


def test_field_unit_without_a_status(coordinator):
    """No status means no unit, without touching the library."""
    assert coordinator.get_field_unit_safe(None, "dhw_temperature") is None


@pytest.mark.parametrize(
    "err", [AttributeError, TypeError, KeyError, ValueError, ImportError]
)
def test_field_unit_swallows_library_errors(coordinator, err):
    """Entities read units on every state write; a raise there is not useful."""
    status = MagicMock()
    status.get_field_unit.side_effect = err("nope")

    assert coordinator.get_field_unit_safe(status, "dhw_temperature") is None


# ---------------------------------------------------------------------------
# _async_refresh_schedules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_refresh_requests_both_schedules(coordinator):
    """A refresh re-reads reservations and TOU settings for the device."""
    coordinator.async_request_reservations = AsyncMock(return_value=True)
    coordinator.async_request_tou_settings = AsyncMock(return_value=True)

    await coordinator._async_refresh_schedules(MAC)

    coordinator.async_request_reservations.assert_awaited_once_with(MAC)
    coordinator.async_request_tou_settings.assert_awaited_once_with(MAC)


@pytest.mark.asyncio
async def test_schedule_refresh_absorbs_a_timeout(coordinator):
    """A slow schedule read must not surface as a failed status request.

    The caller counts TimeoutError toward a forced MQTT reconnect, so letting
    one escape here would make a slow schedule look like a dead connection.
    """
    coordinator.async_request_reservations = AsyncMock(side_effect=TimeoutError)
    coordinator.async_request_tou_settings = AsyncMock(return_value=True)

    await coordinator._async_refresh_schedules(MAC)

    # The TOU read still happens despite the reservation read timing out.
    coordinator.async_request_tou_settings.assert_awaited_once_with(MAC)


@pytest.mark.asyncio
async def test_schedule_refresh_absorbs_transport_errors(coordinator):
    """Transport failures are logged and ignored, never raised."""
    coordinator.async_request_reservations = AsyncMock(side_effect=OSError)
    coordinator.async_request_tou_settings = AsyncMock(side_effect=RuntimeError)

    await coordinator._async_refresh_schedules(MAC)


# ---------------------------------------------------------------------------
# _setup_clients
#
# The distinction that matters here is retriable vs non-retriable auth
# failure: a transient network blip must not push the user into a reauth
# flow, while a genuinely rejected credential must.
# ---------------------------------------------------------------------------


@pytest.fixture
def setup_clients_env(coordinator, mock_hass):
    """Patch the nwp500 SDK surface that _setup_clients reaches for."""
    auth = MagicMock()
    auth.__aenter__ = AsyncMock(return_value=auth)
    auth.close = AsyncMock()
    auth.current_tokens = None

    api = MagicMock()
    api.list_devices = AsyncMock(return_value=[_device(MAC)])

    mqtt = MagicMock()
    mqtt.setup = AsyncMock(return_value=True)
    mqtt.subscribe_device = AsyncMock()
    mqtt.start_periodic_requests = AsyncMock()
    mqtt.request_device_info = AsyncMock()
    mqtt.send_command = AsyncMock(return_value=True)
    mqtt.disconnect = AsyncMock()

    with (
        patch("nwp500.NavienAuthClient", return_value=auth),
        patch("nwp500.NavienAPIClient", return_value=api),
        patch(
            "custom_components.nwp500.coordinator.NWP500MqttManager",
            return_value=mqtt,
        ),
        patch(
            "custom_components.nwp500.coordinator.ha_instance_id.async_get",
            AsyncMock(return_value="ha-id"),
        ),
    ):
        coordinator.hass = mock_hass
        yield coordinator, auth, api, mqtt


@pytest.mark.asyncio
async def test_setup_clients_connects_and_primes_each_device(
    setup_clients_env,
):
    """A successful setup subscribes, starts polling, and asks for device info."""
    coordinator, _auth, _api, mqtt = setup_clients_env

    await coordinator._setup_clients()

    assert list(coordinator._devices_by_mac) == [MAC]
    mqtt.subscribe_device.assert_awaited_once()
    mqtt.start_periodic_requests.assert_awaited_once()
    mqtt.request_device_info.assert_awaited_once()
    mqtt.send_command.assert_awaited_once()


@pytest.mark.asyncio
async def test_setup_clients_continues_in_api_only_mode(setup_clients_env):
    """A failed MQTT connect degrades to API-only instead of failing setup."""
    coordinator, _auth, _api, mqtt = setup_clients_env
    mqtt.setup = AsyncMock(return_value=False)

    await coordinator._setup_clients()

    mqtt.subscribe_device.assert_not_called()
    assert list(coordinator._devices_by_mac) == [MAC]


@pytest.mark.asyncio
async def test_setup_clients_tolerates_priming_failures(setup_clients_env):
    """Initial device-info and reservation reads are best-effort."""
    coordinator, _auth, _api, mqtt = setup_clients_env
    mqtt.request_device_info = AsyncMock(side_effect=RuntimeError("boom"))
    mqtt.send_command = AsyncMock(side_effect=RuntimeError("boom"))

    await coordinator._setup_clients()

    assert list(coordinator._devices_by_mac) == [MAC]


@pytest.mark.asyncio
async def test_setup_clients_fails_when_the_account_has_no_devices(
    setup_clients_env,
):
    """An empty account is a setup failure with an actionable message."""
    coordinator, _auth, api, _mqtt = setup_clients_env
    api.list_devices = AsyncMock(return_value=[])

    with pytest.raises(UpdateFailed, match="No devices found"):
        await coordinator._setup_clients()


@pytest.mark.asyncio
async def test_invalid_credentials_start_a_reauth_flow(setup_clients_env):
    """A rejected credential is not retriable, so ask the user to re-auth."""
    from nwp500.exceptions import InvalidCredentialsError

    coordinator, auth, _api, _mqtt = setup_clients_env
    auth.__aenter__ = AsyncMock(side_effect=InvalidCredentialsError("nope"))

    with pytest.raises(UpdateFailed, match="Authentication failed"):
        await coordinator._setup_clients()

    coordinator.entry.async_start_reauth.assert_called_once_with(
        coordinator.hass
    )


@pytest.mark.asyncio
async def test_retriable_auth_error_does_not_nag_the_user(setup_clients_env):
    """A transient network failure during auth must not trigger reauth.

    nwp500-python marks transient auth/refresh failures retriable; treating
    those as credential failures would push a reauth notification at every
    network blip.
    """
    from nwp500.exceptions import TokenRefreshError

    coordinator, auth, _api, _mqtt = setup_clients_env
    auth.__aenter__ = AsyncMock(
        side_effect=TokenRefreshError("network", retriable=True)
    )

    with pytest.raises(UpdateFailed, match="Authentication error"):
        await coordinator._setup_clients()

    coordinator.entry.async_start_reauth.assert_not_called()


@pytest.mark.asyncio
async def test_non_retriable_auth_error_starts_reauth(setup_clients_env):
    """A genuine token failure does ask the user to re-authenticate."""
    from nwp500.exceptions import AuthenticationError

    coordinator, auth, _api, _mqtt = setup_clients_env
    auth.__aenter__ = AsyncMock(
        side_effect=AuthenticationError("rejected", retriable=False)
    )

    with pytest.raises(UpdateFailed, match="Authentication error"):
        await coordinator._setup_clients()

    coordinator.entry.async_start_reauth.assert_called_once_with(
        coordinator.hass
    )


@pytest.mark.parametrize(
    "err", [RuntimeError("x"), OSError("x"), TimeoutError("x")]
)
@pytest.mark.asyncio
async def test_transport_failures_become_update_failed(setup_clients_env, err):
    """Connection-level failures are reported as retriable setup failures."""
    coordinator, _auth, api, _mqtt = setup_clients_env
    api.list_devices = AsyncMock(side_effect=err)

    with pytest.raises(UpdateFailed, match="Failed to connect"):
        await coordinator._setup_clients()

    coordinator.entry.async_start_reauth.assert_not_called()


# ---------------------------------------------------------------------------
# Stored token restore
#
# Reusing a valid token skips a full authentication on every HA restart, so
# the expiry and corruption branches decide real API load.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_stored_tokens_are_handed_to_the_auth_client(
    setup_clients_env,
):
    """A live token is passed through so startup can skip authentication."""
    from custom_components.nwp500.const import CONF_TOKEN_DATA

    coordinator, _auth, _api, _mqtt = setup_clients_env
    coordinator.entry.data = {
        "email": "test@example.com",
        "password": "password",
        CONF_TOKEN_DATA: {"access_token": "abc"},
    }
    restored = MagicMock()
    restored.is_expired = False

    with (
        patch("nwp500.auth.AuthTokens.model_validate", return_value=restored),
        patch("nwp500.NavienAuthClient") as auth_cls,
    ):
        auth_cls.return_value.__aenter__ = AsyncMock()
        auth_cls.return_value.current_tokens = None
        await coordinator._setup_clients()

    assert auth_cls.call_args.kwargs["stored_tokens"] is restored


@pytest.mark.asyncio
async def test_expired_stored_tokens_are_discarded(setup_clients_env):
    """An expired token is dropped so a full authentication happens."""
    from custom_components.nwp500.const import CONF_TOKEN_DATA

    coordinator, _auth, _api, _mqtt = setup_clients_env
    coordinator.entry.data = {
        "email": "test@example.com",
        "password": "password",
        CONF_TOKEN_DATA: {"access_token": "abc"},
    }
    restored = MagicMock()
    restored.is_expired = True

    with (
        patch("nwp500.auth.AuthTokens.model_validate", return_value=restored),
        patch("nwp500.NavienAuthClient") as auth_cls,
    ):
        auth_cls.return_value.__aenter__ = AsyncMock()
        auth_cls.return_value.current_tokens = None
        await coordinator._setup_clients()

    assert auth_cls.call_args.kwargs["stored_tokens"] is None


@pytest.mark.parametrize(
    "err", [KeyError("k"), ValueError("v"), TypeError("t")]
)
@pytest.mark.asyncio
async def test_corrupt_stored_tokens_fall_back_to_full_auth(
    setup_clients_env, err
):
    """Unreadable stored tokens must not wedge setup permanently."""
    from custom_components.nwp500.const import CONF_TOKEN_DATA

    coordinator, _auth, _api, _mqtt = setup_clients_env
    coordinator.entry.data = {
        "email": "test@example.com",
        "password": "password",
        CONF_TOKEN_DATA: {"garbage": True},
    }

    with (
        patch("nwp500.auth.AuthTokens.model_validate", side_effect=err),
        patch("nwp500.NavienAuthClient") as auth_cls,
    ):
        auth_cls.return_value.__aenter__ = AsyncMock()
        auth_cls.return_value.current_tokens = None
        await coordinator._setup_clients()

    assert auth_cls.call_args.kwargs["stored_tokens"] is None


@pytest.mark.asyncio
async def test_missing_library_is_reported_as_update_failed(coordinator):
    """A missing SDK surfaces as a setup failure, not an import traceback."""
    import builtins

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "nwp500":
            raise ImportError("no module named nwp500")
        return real_import(name, *args, **kwargs)

    with patch.object(builtins, "__import__", side_effect=blocked):
        with pytest.raises(UpdateFailed, match="not available"):
            await coordinator._setup_clients()


# ---------------------------------------------------------------------------
# MQTT reconnect callbacks handed to the library
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconnect_callback_schedules_a_refresh(setup_clients_env):
    """CONNECTION_RESUMED triggers an immediate coordinator refresh."""
    coordinator, _auth, _api, _mqtt = setup_clients_env

    with patch(
        "custom_components.nwp500.coordinator.NWP500MqttManager"
    ) as mqtt_cls:
        mqtt_cls.return_value.setup = AsyncMock(return_value=False)
        await coordinator._setup_clients()
        on_reconnected = mqtt_cls.call_args.kwargs["on_reconnected"]

    # The mock never awaits the coroutine, so close it to avoid a
    # "never awaited" RuntimeWarning masking real ones.
    coordinator.hass.async_create_task = MagicMock(
        side_effect=lambda coro: coro.close()
    )
    on_reconnected()

    coordinator.hass.async_create_task.assert_called_once()


@pytest.mark.asyncio
async def test_reconnection_failed_callback_fires_once_per_outage(
    setup_clients_env,
):
    """Repeat failure callbacks must not queue a refresh storm."""
    coordinator, _auth, _api, _mqtt = setup_clients_env

    with patch(
        "custom_components.nwp500.coordinator.NWP500MqttManager"
    ) as mqtt_cls:
        mqtt_cls.return_value.setup = AsyncMock(return_value=False)
        await coordinator._setup_clients()
        on_failed = mqtt_cls.call_args.kwargs["on_reconnection_failed"]

    coordinator.hass.async_create_task = MagicMock(
        side_effect=lambda coro: coro.close()
    )
    on_failed(3)
    on_failed(4)

    assert coordinator._mqtt_reconnection_failed_attempts == 3
    coordinator.hass.async_create_task.assert_called_once()


# ---------------------------------------------------------------------------
# Status push handling
# ---------------------------------------------------------------------------


def test_status_for_a_known_but_unseen_device_creates_its_entry(coordinator):
    """The first status for a known device populates its data slot."""
    coordinator.async_update_listeners = MagicMock()
    device = _device(MAC)
    coordinator.devices = [device]
    coordinator._update_device_cache()
    coordinator.data = {}
    status = MagicMock()

    coordinator._handle_status_update_in_loop(MAC, status)

    assert coordinator.data[MAC]["device"] is device
    assert coordinator.data[MAC]["status"] is status
    coordinator.async_update_listeners.assert_called_once()


def test_status_for_an_unknown_device_is_ignored(coordinator):
    """Traffic for a device this entry does not own is dropped silently."""
    coordinator.async_update_listeners = MagicMock()
    coordinator.data = {}

    coordinator._handle_status_update_in_loop("99:99:99:99:99:99", MagicMock())

    assert coordinator.data == {}
    coordinator.async_update_listeners.assert_not_called()


def test_reservation_push_hops_to_the_event_loop(coordinator, mock_hass):
    """MQTT callbacks arrive off-loop and must be marshalled onto it."""
    response = {"reservation": []}

    coordinator._on_reservation_update(MAC, response)

    args = mock_hass.loop.call_soon_threadsafe.call_args[0]
    assert args[0] == coordinator._handle_reservation_update_in_loop
    assert args[1:] == (MAC, response)


def test_tou_push_hops_to_the_event_loop(coordinator, mock_hass):
    """The TOU callback is marshalled onto the loop the same way."""
    response = {"tou_use": 1}

    coordinator._on_tou_update(MAC, response)

    args = mock_hass.loop.call_soon_threadsafe.call_args[0]
    assert args[0] == coordinator._handle_tou_update_in_loop
    assert args[1:] == (MAC, response)


# ---------------------------------------------------------------------------
# _async_update_data boundaries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_data_applies_a_changed_unit_system(
    coordinator, mock_hass
):
    """A unit system that no longer matches HA is transitioned before polling."""
    from homeassistant.const import UnitOfTemperature

    mock_hass.config.units.temperature_unit = UnitOfTemperature.CELSIUS
    coordinator.unit_system = "us_customary"
    coordinator.auth_client = AsyncMock()
    coordinator._atomic_unit_system_change = AsyncMock()

    with patch("nwp500.unit_system.set_unit_system"):
        await coordinator._async_update_data()

    coordinator._atomic_unit_system_change.assert_awaited_once_with("metric")


@pytest.mark.asyncio
async def test_update_data_wraps_unexpected_polling_failures(
    coordinator, mock_hass
):
    """An unexpected error while polling becomes UpdateFailed for HA."""
    device = _device(MAC)
    coordinator.devices = [device]
    coordinator.data = {}
    coordinator.auth_client = AsyncMock()
    coordinator.unit_system = "us_customary"
    mock_hass.config.units.temperature_unit = "\u00b0F"

    mqtt = MagicMock()
    mqtt.is_connected = True
    mqtt.request_status = AsyncMock(side_effect=ValueError("something odd"))
    coordinator.mqtt_manager = mqtt

    with patch("nwp500.unit_system.set_unit_system"):
        with pytest.raises(UpdateFailed, match="Error communicating with API"):
            await coordinator._async_update_data()


def test_status_push_swallows_handler_errors(coordinator):
    """Status handling is isolated from callback-thread failures too."""
    device = _device(MAC)
    coordinator.devices = [device]
    coordinator._update_device_cache()
    coordinator.data = {MAC: {"device": device, "status": None}}
    coordinator.async_update_listeners = MagicMock(
        side_effect=RuntimeError("listener exploded")
    )

    coordinator._handle_status_update_in_loop(MAC, MagicMock())

    assert coordinator.data[MAC]["status"] is not None


# ---------------------------------------------------------------------------
# _async_update_data failure envelope
#
# Everything the update does, including client bootstrap, has to end up as
# UpdateFailed for the HA layer -- while deliberate UpdateFailed messages
# survive intact rather than being re-wrapped.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_failure_during_update_becomes_update_failed(
    coordinator, mock_hass
):
    """An unexpected error from client bootstrap is wrapped, not leaked.

    _setup_clients() runs inside the update, so anything escaping it must
    reach Home Assistant as UpdateFailed like any other update failure.
    """
    from homeassistant.const import UnitOfTemperature

    mock_hass.config.units.temperature_unit = UnitOfTemperature.CELSIUS
    coordinator.unit_system = "metric"
    coordinator.auth_client = None
    coordinator._setup_clients = AsyncMock(
        side_effect=ValueError("something odd")
    )

    with patch("nwp500.unit_system.set_unit_system"):
        with pytest.raises(UpdateFailed, match="Error communicating with API"):
            await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_setup_clients_own_update_failed_is_not_rewrapped(
    coordinator, mock_hass
):
    """_setup_clients already reports actionable failures; keep its wording."""
    from homeassistant.const import UnitOfTemperature

    mock_hass.config.units.temperature_unit = UnitOfTemperature.CELSIUS
    coordinator.unit_system = "metric"
    coordinator.auth_client = None
    coordinator._setup_clients = AsyncMock(
        side_effect=UpdateFailed("No devices found for this account.")
    )

    with patch("nwp500.unit_system.set_unit_system"):
        with pytest.raises(UpdateFailed) as excinfo:
            await coordinator._async_update_data()

    assert str(excinfo.value) == "No devices found for this account."


@pytest.mark.asyncio
async def test_permanent_reconnect_failure_keeps_its_reauth_message(
    coordinator, mock_hass
):
    """The reauth instruction must not be buried under a transport error."""
    from homeassistant.const import UnitOfTemperature

    mock_hass.config.units.temperature_unit = UnitOfTemperature.CELSIUS
    coordinator.unit_system = "metric"
    coordinator.auth_client = AsyncMock()
    coordinator._mqtt_reconnection_failed_attempts = 5

    with patch("nwp500.unit_system.set_unit_system"):
        with pytest.raises(UpdateFailed, match="Please re-authenticate"):
            await coordinator._async_update_data()

    coordinator.entry.async_start_reauth.assert_called_once_with(
        coordinator.hass
    )


# ---------------------------------------------------------------------------
# _async_refresh_device_metadata
#
# `/device/list` carries the cloud-recorded fault and descaling window, which
# the device never sends over MQTT. It is otherwise read once, at setup.
# ---------------------------------------------------------------------------


def _metadata_coordinator(coordinator, devices=None):
    coordinator.api_client = MagicMock()
    coordinator.api_client.list_devices = AsyncMock(
        return_value=devices if devices is not None else [_device(MAC)]
    )
    coordinator.devices = [_device(MAC)]
    coordinator._update_device_cache()
    return coordinator


@pytest.mark.asyncio
async def test_device_metadata_is_refreshed_on_its_own_cycle(coordinator):
    """The re-read is periodic, not every update cycle."""
    from custom_components.nwp500.const import DEVICE_METADATA_REFRESH_CYCLES

    refreshed = _device(MAC)
    coord = _metadata_coordinator(coordinator, [refreshed])

    for _ in range(DEVICE_METADATA_REFRESH_CYCLES - 1):
        await coord._async_refresh_device_metadata()
    coord.api_client.list_devices.assert_not_called()

    await coord._async_refresh_device_metadata()

    coord.api_client.list_devices.assert_awaited_once()
    assert coord.devices == [refreshed]
    assert coord._devices_by_mac[MAC] is refreshed


@pytest.mark.asyncio
async def test_failed_metadata_refresh_keeps_the_known_devices(coordinator):
    """A refresh failure must not empty the device list entities are keyed on."""
    coord = _metadata_coordinator(coordinator)
    known = coord.devices
    coord.api_client.list_devices = AsyncMock(side_effect=TimeoutError)
    coord._device_metadata_counter = -1

    await coord._async_refresh_device_metadata()

    assert coord.devices is known


@pytest.mark.asyncio
async def test_metadata_refresh_keeps_a_device_the_cloud_omitted(coordinator):
    """A device missing from one listing must not disappear.

    Entities and MQTT subscriptions are keyed to the devices known at
    setup, so dropping one here would break every service call for it
    until the integration was reloaded.
    """
    other = _device("11:22:33:44:55:66")
    coord = _metadata_coordinator(coordinator)
    coord.devices = [coord.devices[0], other]
    coord._update_device_cache()
    # The listing comes back with only the first device.
    coord.api_client.list_devices = AsyncMock(return_value=[_device(MAC)])
    coord._device_metadata_counter = -1

    await coord._async_refresh_device_metadata()

    assert coord._devices_by_mac["11:22:33:44:55:66"] is other


@pytest.mark.asyncio
async def test_metadata_refresh_does_not_adopt_an_unknown_device(coordinator):
    """A device new to the account has no entities and no subscription.

    Adding it here would put it in the coordinator's data with neither,
    so membership changes are left to a reload.
    """
    coord = _metadata_coordinator(
        coordinator, [_device(MAC), _device("99:99:99:99:99:99")]
    )
    coord._device_metadata_counter = -1

    await coord._async_refresh_device_metadata()

    assert list(coord._devices_by_mac) == [MAC]


@pytest.mark.asyncio
async def test_metadata_refresh_absorbs_a_malformed_response(coordinator):
    """The library validates rows outside its own error wrapper.

    A surprise in the payload arrives as a ValidationError or a
    JSONDecodeError -- both ValueError. Letting one escape would fail the
    whole update cycle over metadata nothing else depends on.
    """
    coord = _metadata_coordinator(coordinator)
    known = coord.devices
    coord.api_client.list_devices = AsyncMock(
        side_effect=ValueError("1 validation error for Device")
    )
    coord._device_metadata_counter = -1

    await coord._async_refresh_device_metadata()

    assert coord.devices is known


@pytest.mark.asyncio
async def test_empty_metadata_refresh_keeps_the_known_devices(coordinator):
    """An empty listing is treated as a bad answer, not as "no devices"."""
    coord = _metadata_coordinator(coordinator, [])
    known = coord.devices
    coord._device_metadata_counter = -1

    await coord._async_refresh_device_metadata()

    assert coord.devices is known


@pytest.mark.asyncio
async def test_metadata_refresh_without_an_api_client_is_a_no_op(coordinator):
    """Called before setup completes, it neither raises nor advances."""
    coordinator.api_client = None

    await coordinator._async_refresh_device_metadata()

    assert coordinator._device_metadata_counter == 0


def test_cloud_error_and_descaling_are_read_from_the_device(coordinator):
    """The accessors read the REST blocks the sensors expose."""
    device = _device(MAC)
    coordinator.devices = [device]
    coordinator._update_device_cache()

    assert coordinator.get_device_error(MAC) is device.error
    assert coordinator.get_device_descaling(MAC) is device.descaling


def test_cloud_metadata_of_an_unknown_device_is_none(coordinator):
    """An unrecognised MAC reports nothing rather than raising."""
    assert coordinator.get_device_error("99:99:99:99:99:99") is None
    assert coordinator.get_device_descaling("99:99:99:99:99:99") is None


# ---------------------------------------------------------------------------
# async_fetch_energy_usage
#
# A report pulled on demand: the device answers only when asked, nothing is
# cached, and no entity reads the result.
# ---------------------------------------------------------------------------


def _energy_response(year: int, months: list[int]) -> dict:
    """A reply covering the given period."""
    return {
        "total": {"heat_pump_usage": 5},
        "usage": [
            {"year": year, "month": month, "data": []} for month in months
        ],
    }


@pytest.mark.asyncio
async def test_fetch_energy_usage_returns_the_devices_reply(coordinator):
    """The response delivered over MQTT is handed back to the caller."""
    response = _energy_response(2026, [8])

    async def reply(mac, command, **kwargs):
        assert command == "request_energy_usage"
        assert kwargs == {"year": 2026, "months": [8]}
        coordinator._handle_energy_usage_in_loop(mac, response)
        return True

    coordinator.async_send_command = AsyncMock(side_effect=reply)

    assert (
        await coordinator.async_fetch_energy_usage(MAC, 2026, [8]) == response
    )
    assert coordinator._energy_request is None


@pytest.mark.asyncio
async def test_a_late_reply_is_not_handed_to_the_next_request(coordinator):
    """A straggler from a timed-out request must not answer the next one.

    The reply topic is shared, so the payload carries nothing tying it to
    the request that produced it; the pending slot is cleared on timeout
    precisely so a late arrival has nobody to be given to.
    """
    coordinator.async_send_command = AsyncMock(return_value=True)

    # First request gives up.
    assert (
        await coordinator.async_fetch_energy_usage(MAC, 2026, [7], timeout=0.01)
        is None
    )

    # Its reply arrives while a second request is outstanding.
    late = _energy_response(2026, [7])

    async def reply_late(mac, command, **kwargs):
        coordinator._handle_energy_usage_in_loop(mac, late)
        return True

    coordinator.async_send_command = AsyncMock(side_effect=reply_late)

    assert (
        await coordinator.async_fetch_energy_usage(MAC, 2026, [8], timeout=0.01)
        is None
    )


@pytest.mark.asyncio
async def test_an_empty_late_reply_is_not_handed_to_the_next_request(
    coordinator,
):
    """A reply carrying no months matches every request.

    So once a request has gone unanswered it cannot be told apart from a
    straggler -- and accepting it would hand back an all-zero report as
    though the device had measured it.
    """
    coordinator.async_send_command = AsyncMock(return_value=True)

    assert (
        await coordinator.async_fetch_energy_usage(MAC, 2026, [7], timeout=0.01)
        is None
    )

    empty = {"total": {"heat_pump_usage": 0}, "usage": []}

    async def reply_empty(mac, command, **kwargs):
        coordinator._handle_energy_usage_in_loop(mac, empty)
        return True

    coordinator.async_send_command = AsyncMock(side_effect=reply_empty)

    assert (
        await coordinator.async_fetch_energy_usage(MAC, 2026, [8], timeout=0.01)
        is None
    )


@pytest.mark.asyncio
async def test_an_empty_reply_answers_when_no_request_has_timed_out(
    coordinator,
):
    """Nothing can be in flight yet, so an empty reply means no data.

    A device asked about a month it has nothing recorded for answers
    exactly this way, and reporting that as a failure would be wrong.
    """
    empty = {"total": {}, "usage": []}

    async def reply_empty(mac, command, **kwargs):
        coordinator._handle_energy_usage_in_loop(mac, empty)
        return True

    coordinator.async_send_command = AsyncMock(side_effect=reply_empty)

    assert await coordinator.async_fetch_energy_usage(MAC, 2026, [8]) == empty


@pytest.mark.asyncio
async def test_a_reply_for_another_period_is_ignored(coordinator):
    """August's usage is not an answer to a question about September."""

    async def wrong_period(mac, command, **kwargs):
        coordinator._handle_energy_usage_in_loop(
            mac, _energy_response(2026, [8])
        )
        return True

    coordinator.async_send_command = AsyncMock(side_effect=wrong_period)

    assert (
        await coordinator.async_fetch_energy_usage(MAC, 2026, [9], timeout=0.01)
        is None
    )


@pytest.mark.asyncio
async def test_a_reply_dispatched_under_another_mac_still_answers(coordinator):
    """One reply is delivered to every subscribed device's callback.

    The MAC a callback was registered under says nothing about which
    device replied, so matching on it would drop the answer whenever the
    other device's callback fired first.
    """
    response = _energy_response(2026, [8])

    async def reply_as_other_device(mac, command, **kwargs):
        coordinator._handle_energy_usage_in_loop("11:22:33:44:55:66", response)
        return True

    coordinator.async_send_command = AsyncMock(
        side_effect=reply_as_other_device
    )

    assert (
        await coordinator.async_fetch_energy_usage(MAC, 2026, [8]) == response
    )


@pytest.mark.asyncio
async def test_a_month_with_no_data_is_still_an_answer(coordinator):
    """The device omits months it has nothing for; that is not a mismatch."""
    response = _energy_response(2026, [7])

    async def reply(mac, command, **kwargs):
        coordinator._handle_energy_usage_in_loop(mac, response)
        return True

    coordinator.async_send_command = AsyncMock(side_effect=reply)

    assert (
        await coordinator.async_fetch_energy_usage(MAC, 2026, [7, 8])
        == response
    )


@pytest.mark.asyncio
async def test_fetch_energy_usage_returns_none_when_request_fails(coordinator):
    """A publish failure short-circuits instead of waiting out the timeout."""
    coordinator.async_send_command = AsyncMock(return_value=False)

    async with asyncio.timeout(1):
        assert (
            await coordinator.async_fetch_energy_usage(MAC, 2026, [8]) is None
        )
    assert coordinator._energy_request is None


@pytest.mark.asyncio
async def test_fetch_energy_usage_times_out_and_cleans_up(coordinator):
    """A silent device yields None, and leaves no waiter behind."""
    coordinator.async_send_command = AsyncMock(return_value=True)

    assert (
        await coordinator.async_fetch_energy_usage(MAC, 2026, [8], timeout=0.01)
        is None
    )
    assert coordinator._energy_request is None


@pytest.mark.asyncio
async def test_energy_reports_are_answered_one_at_a_time(coordinator):
    """The reply topic is keyed by client, not by device.

    Two questions in flight at once could therefore be crossed, so the
    second must wait for the first to be answered.
    """
    in_flight = 0
    peak = 0

    async def slow_request(mac, command, **kwargs):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return True

    coordinator.async_send_command = AsyncMock(side_effect=slow_request)

    await asyncio.gather(
        coordinator.async_fetch_energy_usage(MAC, 2026, [8], timeout=0.01),
        coordinator.async_fetch_energy_usage(MAC, 2026, [9], timeout=0.01),
    )

    assert peak == 1


def test_energy_usage_is_not_cached(coordinator, mock_hass):
    """Nothing subscribes to this, so nothing stores it either."""
    coordinator.async_update_listeners = MagicMock()

    coordinator._handle_energy_usage_in_loop(MAC, {"total": {}, "usage": []})

    assert not hasattr(coordinator, "energy_usage")
    mock_hass.bus.async_fire.assert_not_called()
    coordinator.async_update_listeners.assert_not_called()


def test_energy_usage_skips_an_already_resolved_waiter(coordinator):
    """A waiter abandoned by a timeout must not be resolved twice."""
    loop = asyncio.new_event_loop()
    try:
        done: asyncio.Future = loop.create_future()
        done.set_result({"stale": True})
        coordinator._energy_request = {
            "mac_address": MAC,
            "year": 2026,
            "months": [8],
            "future": done,
        }

        coordinator._handle_energy_usage_in_loop(MAC, {"total": {}})

        assert done.result() == {"stale": True}
    finally:
        loop.close()


def test_an_unsolicited_energy_reply_is_discarded(coordinator):
    """Nothing outstanding means nothing to hand it to."""
    coordinator._energy_request = None

    coordinator._handle_energy_usage_in_loop(MAC, {"total": {}, "usage": []})

    assert coordinator._energy_request is None


@pytest.mark.asyncio
async def test_empty_replies_work_again_after_a_matched_reply(coordinator):
    """One timeout must not disable empty replies for good.

    A reply matching the outstanding period accounts for whatever was in
    flight, so the next empty reply is once again just a device with nothing
    recorded. The straggler flag used to latch on for the life of the
    coordinator, after which every genuinely empty period reported as a
    timeout.
    """
    # 1. A request times out, so a straggler becomes possible.
    coordinator.async_send_command = AsyncMock(return_value=True)
    assert (
        await coordinator.async_fetch_energy_usage(MAC, 2026, [7], timeout=0.01)
        is None
    )
    assert coordinator._energy_straggler_possible is True

    # 2. A reply matching the period asked for accounts for what was in flight.
    matched = _energy_response(2026, [8])

    async def reply_matched(mac, command, **kwargs):
        coordinator._handle_energy_usage_in_loop(mac, matched)
        return True

    coordinator.async_send_command = AsyncMock(side_effect=reply_matched)
    assert await coordinator.async_fetch_energy_usage(MAC, 2026, [8]) == matched
    assert coordinator._energy_straggler_possible is False

    # 3. An empty reply is therefore trustworthy again: a month with no data.
    empty = {"total": {}, "usage": []}

    async def reply_empty(mac, command, **kwargs):
        coordinator._handle_energy_usage_in_loop(mac, empty)
        return True

    coordinator.async_send_command = AsyncMock(side_effect=reply_empty)
    assert await coordinator.async_fetch_energy_usage(MAC, 2026, [9]) == empty


# ---------------------------------------------------------------------------
# _async_request_initial_tou
#
# The TOU plan is read once at setup so its sensor is populated from the
# start, instead of staying unknown until the first periodic schedule
# refresh -- roughly twenty minutes after every restart.
# ---------------------------------------------------------------------------


def _device_with_mac(mac: str = MAC) -> MagicMock:
    device = MagicMock()
    device.device_info.mac_address = mac
    return device


@pytest.mark.asyncio
async def test_initial_tou_read_happens_when_device_info_has_arrived(
    coordinator,
):
    """With the controller serial known, the plan is read at setup."""
    features = MagicMock()
    features.controller_serial_number = "56496061BT22230408"
    coordinator.device_features = {MAC: features}
    coordinator.async_request_tou_settings = AsyncMock(return_value=True)

    await coordinator._async_request_initial_tou(_device_with_mac())

    coordinator.async_request_tou_settings.assert_awaited_once_with(MAC)


@pytest.mark.asyncio
async def test_initial_tou_read_is_skipped_before_device_info_arrives(
    coordinator,
):
    """A device that has not reported yet is skipped, not failed.

    The read is keyed by the controller serial number, which only the MQTT
    device-info response publishes. Calling anyway would log an error for a
    condition the periodic refresh recovers from on its own.
    """
    coordinator.device_features = {}
    coordinator.async_request_tou_settings = AsyncMock(return_value=True)

    await coordinator._async_request_initial_tou(_device_with_mac())

    coordinator.async_request_tou_settings.assert_not_awaited()


@pytest.mark.asyncio
async def test_initial_tou_read_is_skipped_when_the_serial_is_blank(
    coordinator,
):
    """Features present but no serial is the same not-ready condition."""
    features = MagicMock()
    features.controller_serial_number = ""
    coordinator.device_features = {MAC: features}
    coordinator.async_request_tou_settings = AsyncMock(return_value=True)

    await coordinator._async_request_initial_tou(_device_with_mac())

    coordinator.async_request_tou_settings.assert_not_awaited()


@pytest.mark.asyncio
async def test_initial_tou_read_never_fails_setup(coordinator):
    """A failed read is logged and swallowed; setup must still complete."""
    from nwp500.exceptions import APIError

    features = MagicMock()
    features.controller_serial_number = "56496061BT22230408"
    coordinator.device_features = {MAC: features}
    coordinator.async_request_tou_settings = AsyncMock(
        side_effect=APIError("cloud unavailable")
    )

    # Must not raise.
    await coordinator._async_request_initial_tou(_device_with_mac())

    coordinator.async_request_tou_settings.assert_awaited_once_with(MAC)

"""Le bouton physique : libgpiod (v2 et v1, remplacée par de fausses bibliothèques), anti-rebond, reconfiguration."""

import asyncio
import contextlib
import os
import sys
import types
from datetime import timedelta

import pytest

from mydock_dock import gpio_button
from mydock_dock.gpio_button import (
    GpioButton,
    GpioError,
    LibgpiodBackend,
    describe_pin,
)


class FakeBackend:
    """Un descripteur de tube : écrire dedans, c'est appuyer."""

    instances: list["FakeBackend"] = []

    def __init__(self, fail: str | None = None):
        self.fail = fail
        self.read_fd, self.write_fd = os.pipe()
        os.set_blocking(self.read_fd, False)
        self.opened: int | None = None
        self.closed = False
        FakeBackend.instances.append(self)

    def open(self, pin):
        if self.fail:
            raise GpioError(self.fail)
        self.opened = pin
        return self.read_fd

    def read(self):
        try:
            return len(os.read(self.read_fd, 1024))
        except BlockingIOError:
            return 0

    def close(self):
        if not self.closed:
            self.closed = True
            for fd in (self.read_fd, self.write_fd):
                with contextlib.suppress(OSError):
                    os.close(fd)

    def press(self):
        os.write(self.write_fd, b"x")


@pytest.fixture(autouse=True)
def reset():
    FakeBackend.instances = []


async def settle():
    for _ in range(5):
        await asyncio.sleep(0.01)


def button(presses, **kw):
    return GpioButton(lambda: presses.append(1), backend=kw.pop("backend", FakeBackend), **kw)


# --- le bouton ---------------------------------------------------------------------------------------------


def test_the_pin_is_described_with_its_physical_position():
    assert describe_pin(17) == "GPIO17 (broche 11)"
    assert describe_pin(27) == "GPIO27 (broche 13)"
    assert describe_pin(0) == "GPIO0"


async def test_a_press_calls_back_and_the_status_says_ready():
    presses, statuses = [], []
    b = button(presses, on_status=statuses.append)
    assert b.configure(asyncio.get_running_loop(), 17) is True
    assert b.ready
    assert b.status == "GPIO17 (broche 11) : prêt."
    assert statuses == ["GPIO17 (broche 11) : prêt."]
    assert FakeBackend.instances[0].opened == 17
    FakeBackend.instances[0].press()
    await settle()
    assert presses == [1]
    assert b.presses == 1
    b.stop()


async def test_bounces_are_one_press(monkeypatch):
    now = [100.0]
    presses = []
    b = button(presses, min_interval=0.35, clock=lambda: now[0])
    b.configure(asyncio.get_running_loop(), 17)
    backend = FakeBackend.instances[0]
    for _ in range(5):  # un appui qui rebondit
        backend.press()
        await settle()
        now[0] += 0.02
    assert presses == [1]
    now[0] += 1.0
    backend.press()
    await settle()
    assert presses == [1, 1]
    b.stop()


async def test_an_unavailable_line_is_reported_not_raised():
    presses, statuses = [], []
    b = button(
        presses,
        backend=lambda: FakeBackend(fail="GPIO17 : accès refusé"),
        on_status=statuses.append,
    )
    assert b.configure(asyncio.get_running_loop(), 17) is False
    assert not b.ready
    assert b.status == "GPIO17 : accès refusé"
    assert statuses == ["GPIO17 : accès refusé"]
    assert FakeBackend.instances[0].closed


async def test_reconfiguring_releases_the_old_line():
    presses = []
    b = button(presses)
    loop = asyncio.get_running_loop()
    b.configure(loop, 17)
    b.configure(loop, 22)
    first, second = FakeBackend.instances
    assert first.closed
    assert second.opened == 22
    assert b.pin == 22
    second.press()
    await settle()
    assert presses == [1]
    b.stop()
    assert second.closed
    assert not b.ready


async def test_a_reading_failure_stops_the_button_and_says_so():
    presses = []
    b = button(presses)
    b.configure(asyncio.get_running_loop(), 17)

    def boom():
        raise OSError("périphérique disparu")

    b._backend.read = boom
    FakeBackend.instances[0].press()
    await settle()
    assert not b.ready
    assert "lecture impossible" in (b.status or "")
    assert presses == []


# --- libgpiod ---------------------------------------------------------------------------------------------------


def fake_gpiod_v2(labels, *, fail_with=None):
    calls = {}

    class Request:
        fd = 41

        def read_edge_events(self):
            return ["e1", "e2"]

        def release(self):
            calls["released"] = True

    class Info:
        def __init__(self, label):
            self.label = label

    class Chip:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_info(self):
            return Info(labels[self.path])

    def request_lines(path, consumer, config):
        if fail_with:
            raise fail_with
        calls.update(path=path, consumer=consumer, config=config)
        return Request()

    class LineSettings:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    line = types.ModuleType("gpiod.line")
    line.Direction = types.SimpleNamespace(INPUT="INPUT")
    line.Bias = types.SimpleNamespace(PULL_UP="PULL_UP")
    line.Edge = types.SimpleNamespace(FALLING="FALLING")
    module = types.ModuleType("gpiod")
    module.Chip, module.LineSettings, module.request_lines, module.line = (
        Chip, LineSettings, request_lines, line,
    )  # fmt: skip
    return module, calls, line


def backend_with(module, chips, monkeypatch, line=None):
    if line is not None:
        monkeypatch.setitem(sys.modules, "gpiod", module)
        monkeypatch.setitem(sys.modules, "gpiod.line", line)
    backend = LibgpiodBackend(module)
    monkeypatch.setattr(backend, "_chips", lambda: chips)
    return backend


def test_v2_opens_the_pi_chip_with_pull_up_and_falling_edge(monkeypatch):
    module, calls, line = fake_gpiod_v2(
        {"/dev/gpiochip0": "gpio-brcmstb@107d508500", "/dev/gpiochip4": "pinctrl-rp1"}
    )
    backend = backend_with(module, ["/dev/gpiochip0", "/dev/gpiochip4"], monkeypatch, line)
    assert backend.is_v2
    assert backend.open(17) == 41
    assert calls["path"] == "/dev/gpiochip4"  # la puce « pinctrl-… », pas la première
    assert calls["consumer"] == "mydock-button"
    settings = calls["config"][17].kwargs
    assert settings["direction"] == "INPUT"
    assert settings["bias"] == "PULL_UP"
    assert settings["edge_detection"] == "FALLING"
    assert settings["debounce_period"] == timedelta(seconds=gpio_button.DEBOUNCE)
    assert backend.read() == 2
    backend.close()
    assert calls["released"]


def test_the_pi4_chip_is_found(monkeypatch):
    module, calls, line = fake_gpiod_v2({"/dev/gpiochip0": "pinctrl-bcm2711"})
    backend = backend_with(module, ["/dev/gpiochip0"], monkeypatch, line)
    backend.open(17)
    assert calls["path"] == "/dev/gpiochip0"


def test_no_chip_is_an_explicit_error(monkeypatch):
    module, _calls, line = fake_gpiod_v2({})
    backend = backend_with(module, [], monkeypatch, line)
    with pytest.raises(GpioError, match="aucune puce GPIO"):
        backend.open(17)


def test_a_chip_without_the_pi_label_is_an_error(monkeypatch):
    module, _calls, line = fake_gpiod_v2({"/dev/gpiochip0": "autre"})
    backend = backend_with(module, ["/dev/gpiochip0"], monkeypatch, line)
    with pytest.raises(GpioError, match="pinctrl"):
        backend.open(17)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (PermissionError("denied"), "groupe gpio"),
        (OSError(16, "Device or resource busy"), "déjà utilisée"),
        (ValueError("bizarre"), "bizarre"),
    ],
)
def test_libgpiod_errors_become_sentences(monkeypatch, error, expected):
    module, _calls, line = fake_gpiod_v2({"/dev/gpiochip0": "pinctrl-bcm2711"}, fail_with=error)
    backend = backend_with(module, ["/dev/gpiochip0"], monkeypatch, line)
    with pytest.raises(GpioError) as raised:
        backend.open(17)
    assert expected in str(raised.value)
    assert "GPIO17" in str(raised.value)


def test_a_missing_library_is_explained():
    backend = LibgpiodBackend.__new__(LibgpiodBackend)
    backend._gpiod = None
    backend._request = backend._line = backend._chip = None
    assert not backend.available
    with pytest.raises(GpioError, match="sudo apt install python3-libgpiod"):
        backend.open(17)


def test_v1_is_supported_too(monkeypatch):
    calls = {}

    class Line:
        def request(self, consumer, type, flags):
            calls.update(consumer=consumer, type=type, flags=flags)

        def event_get_fd(self):
            return 52

        def event_read(self):
            calls["read"] = True

        def release(self):
            calls["released"] = True

    class Chip:
        def __init__(self, path):
            self.path = path

        def label(self):
            return "pinctrl-bcm2711"

        def get_line(self, pin):
            calls["pin"] = pin
            return Line()

        def close(self):
            calls["closed"] = True

    module = types.ModuleType("gpiod")
    module.Chip, module.LINE_REQ_EV_FALLING_EDGE, module.LINE_REQ_FLAG_BIAS_PULL_UP = Chip, 3, 32
    backend = LibgpiodBackend(module)
    monkeypatch.setattr(backend, "_chips", lambda: ["/dev/gpiochip0"])
    assert not backend.is_v2
    assert backend.open(17) == 52
    assert (calls["pin"], calls["consumer"], calls["type"], calls["flags"]) == (
        17,
        "mydock-button",
        3,
        32,
    )
    assert backend.read() == 1
    backend.close()
    assert calls["released"]

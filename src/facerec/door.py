"""Door controller: a simulator for development and a Raspberry Pi GPIO driver for the real lock.

Both share the same timed logic, so what is tested on the simulator is what runs on the hardware.
The door fails closed: it is only open for ``open_seconds`` after a grant, and ``close()`` is called
when the program stops or crashes, so a lock is never left energised.
"""


class Door:
    """A door that opens for a fixed time after a grant (simulated: no hardware is driven)."""

    def __init__(self, open_seconds: float = 5.0):
        self.open_seconds = open_seconds
        self._open = False
        self._until = 0.0
        self.opened_count = 0

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self, now: float) -> None:
        if not self._open:
            self.opened_count += 1
        self._until = now + self.open_seconds
        self._set(True)

    def update(self, now: float) -> None:
        """Call on every frame: closes the door once the opening time is over."""
        if self._open and now >= self._until:
            self._set(False)

    def close(self) -> None:
        self._set(False)

    def seconds_left(self, now: float) -> float:
        return max(0.0, self._until - now) if self._open else 0.0

    def _set(self, opened: bool) -> None:
        self._open = opened
        self._drive(opened)

    def _drive(self, opened: bool) -> None:
        """Hardware hook: nothing to drive for the simulator."""


class GpioDoor(Door):
    """Raspberry Pi relay or electric strike on one GPIO pin (needs `gpiozero`, not tested on hardware).

    Wire the relay so that no current means a locked door (fail-secure strike) or follow the
    lock's datasheet; ``active_high=False`` suits the common low-level-trigger relay boards.
    """

    def __init__(self, pin: int, open_seconds: float = 5.0, active_high: bool = True):
        super().__init__(open_seconds)
        try:
            from gpiozero import OutputDevice
        except ImportError as err:
            raise RuntimeError("GPIO door needs gpiozero on a Raspberry Pi: pip install gpiozero") from err
        self._device = OutputDevice(pin, active_high=active_high, initial_value=False)
        self.close()

    def _drive(self, opened: bool) -> None:
        if opened:
            self._device.on()
        else:
            self._device.off()


def make_door(kind: str, open_seconds: float, pin: int | None = None) -> Door | None:
    if kind == "none":
        return None
    if kind == "sim":
        return Door(open_seconds)
    if kind == "gpio":
        if pin is None:
            raise RuntimeError("--door gpio needs --gpio-pin")
        return GpioDoor(pin, open_seconds)
    raise RuntimeError(f"unknown door type {kind!r}")

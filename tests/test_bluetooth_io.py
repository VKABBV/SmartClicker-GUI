import queue
import unittest

from uwb_capture import bluetooth_io


class FakeCompleted:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


class BluetoothIoTests(unittest.TestCase):
    def test_connected_bluez_devices_marks_connected_clicker(self) -> None:
        original_run = bluetooth_io.subprocess.run
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kwargs: object) -> FakeCompleted:
            calls.append(cmd)
            if cmd == ["bluetoothctl", "devices", "Connected"]:
                return FakeCompleted("Device AA:BB:CC:DD:EE:FF IMEC ML Clicker\n")
            if cmd == ["bluetoothctl", "info", "AA:BB:CC:DD:EE:FF"]:
                return FakeCompleted("Name: IMEC ML Clicker\nConnected: yes\n")
            return FakeCompleted("", returncode=1)

        bluetooth_io.subprocess.run = fake_run  # type: ignore[assignment]
        try:
            infos = bluetooth_io._connected_bluez_devices()
        finally:
            bluetooth_io.subprocess.run = original_run  # type: ignore[assignment]

        self.assertEqual(len(infos), 1)
        self.assertEqual(infos[0].address, "AA:BB:CC:DD:EE:FF")
        self.assertEqual(infos[0].name, "IMEC ML Clicker (connected)")
        self.assertIn(["bluetoothctl", "devices", "Connected"], calls)

    def test_scanner_reports_bluez_connected_clicker_when_discovery_is_empty(self) -> None:
        original_scanner = bluetooth_io.BleakScanner
        original_run = bluetooth_io.subprocess.run

        class EmptyScanner:
            @staticmethod
            async def discover(timeout: float) -> list[object]:
                return []

        def fake_run(cmd: list[str], **_kwargs: object) -> FakeCompleted:
            if cmd == ["bluetoothctl", "devices", "Connected"]:
                return FakeCompleted("Device AA:BB:CC:DD:EE:FF IMEC ML Clicker\n")
            if cmd == ["bluetoothctl", "info", "AA:BB:CC:DD:EE:FF"]:
                return FakeCompleted(
                    "Name: IMEC ML Clicker\n"
                    "UUID: IMEC Packet Service (494d4543-0001-4757-8000-000000000001)\n"
                )
            return FakeCompleted("", returncode=1)

        events: queue.Queue[tuple[str, object]] = queue.Queue()
        bluetooth_io.BleakScanner = EmptyScanner  # type: ignore[assignment]
        bluetooth_io.subprocess.run = fake_run  # type: ignore[assignment]
        try:
            bluetooth_io.BluetoothScanner(events, name_filter="IMEC ML Clicker").run()
        finally:
            bluetooth_io.BleakScanner = original_scanner
            bluetooth_io.subprocess.run = original_run  # type: ignore[assignment]

        event, payload = events.get_nowait()
        self.assertEqual(event, "devices")
        infos = list(payload)  # type: ignore[arg-type]
        self.assertEqual(infos[0].label, "AA:BB:CC:DD:EE:FF - IMEC ML Clicker (connected)")

    def test_scanner_reports_bluez_connected_clicker_when_discovery_fails(self) -> None:
        original_scanner = bluetooth_io.BleakScanner
        original_run = bluetooth_io.subprocess.run

        class FailingScanner:
            @staticmethod
            async def discover(timeout: float) -> list[object]:
                raise RuntimeError("scan failed")

        def fake_run(cmd: list[str], **_kwargs: object) -> FakeCompleted:
            if cmd == ["bluetoothctl", "devices", "Connected"]:
                return FakeCompleted("Device AA:BB:CC:DD:EE:FF Clicker\n")
            if cmd == ["bluetoothctl", "info", "AA:BB:CC:DD:EE:FF"]:
                return FakeCompleted(
                    "Name: Clicker\n"
                    "UUID: IMEC Packet Service (494d4543-0001-4757-8000-000000000001)\n"
                )
            return FakeCompleted("", returncode=1)

        events: queue.Queue[tuple[str, object]] = queue.Queue()
        bluetooth_io.BleakScanner = FailingScanner  # type: ignore[assignment]
        bluetooth_io.subprocess.run = fake_run  # type: ignore[assignment]
        try:
            bluetooth_io.BluetoothScanner(events, name_filter="IMEC ML Clicker").run()
        finally:
            bluetooth_io.BleakScanner = original_scanner
            bluetooth_io.subprocess.run = original_run  # type: ignore[assignment]

        event, payload = events.get_nowait()
        self.assertEqual(event, "devices")
        infos = list(payload)  # type: ignore[arg-type]
        self.assertEqual(infos[0].address, "AA:BB:CC:DD:EE:FF")

    def test_scanner_reports_known_clicker_when_not_advertising(self) -> None:
        original_scanner = bluetooth_io.BleakScanner
        original_run = bluetooth_io.subprocess.run

        class EmptyScanner:
            @staticmethod
            async def discover(timeout: float) -> list[object]:
                return []

        def fake_run(cmd: list[str], **_kwargs: object) -> FakeCompleted:
            if cmd == ["bluetoothctl", "devices", "Connected"]:
                return FakeCompleted("")
            if cmd == ["bluetoothctl", "devices"]:
                return FakeCompleted("Device AA:BB:CC:DD:EE:FF IMEC ML Clicker\n")
            if cmd == ["bluetoothctl", "info", "AA:BB:CC:DD:EE:FF"]:
                return FakeCompleted(
                    "Name: IMEC ML Clicker\n"
                    "Connected: no\n"
                    "UUID: IMEC Packet Service (494d4543-0001-4757-8000-000000000001)\n"
                )
            return FakeCompleted("", returncode=1)

        events: queue.Queue[tuple[str, object]] = queue.Queue()
        bluetooth_io.BleakScanner = EmptyScanner  # type: ignore[assignment]
        bluetooth_io.subprocess.run = fake_run  # type: ignore[assignment]
        try:
            bluetooth_io.BluetoothScanner(events, name_filter="IMEC ML Clicker").run()
        finally:
            bluetooth_io.BleakScanner = original_scanner
            bluetooth_io.subprocess.run = original_run  # type: ignore[assignment]

        event, payload = events.get_nowait()
        self.assertEqual(event, "devices")
        infos = list(payload)  # type: ignore[arg-type]
        self.assertEqual(infos[0].label, "AA:BB:CC:DD:EE:FF - IMEC ML Clicker")


if __name__ == "__main__":
    unittest.main()

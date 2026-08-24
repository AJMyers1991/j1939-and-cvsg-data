#!/usr/bin/env python
"""Live PACCAR/Kenworth CVSG gauge monitor using Saleae Logic 2.

The program repeatedly captures Channel 0 through Saleae's supported Logic 2
Automation API, decodes the pulse-width CVSG frames, displays current/session
values, and appends a summary to cvsg.log beside this script.

Double-click this file on Windows to run it. Press any key to stop cleanly.
"""

from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
LOG_PATH = SCRIPT_DIR / "cvsg.log"
LOGIC2_PATH = Path(os.environ.get("ProgramW6432", r"C:\Program Files")) / "Logic" / "Logic.exe"
CHANNEL = 0
CAPTURE_SECONDS = 1.0
# Logic 2 requires a rate supported by the attached model. The first rate that
# successfully starts a capture is retained for the rest of the session.
SAMPLE_RATE_CANDIDATES = (10_000_000, 12_000_000, 8_000_000, 5_000_000, 4_000_000, 2_000_000, 1_000_000)
INVALID_PAYLOAD = 0xF830
KPA_TO_PSI = 0.1450377377
KPA_TO_IN_H2O = 4.0146307866
KPA_TO_IN_HG = 0.2952998307


@dataclass(frozen=True)
class GaugeDefinition:
    """CVSG gauge metadata and engineering-unit conversion."""

    gauge_id: int
    name: str
    unit: str
    precision: int
    convert: Callable[[int], float]


@dataclass
class GaugeState:
    """Current and session-extreme values for one gauge."""

    definition: GaugeDefinition
    current: Optional[float] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    last_update: Optional[float] = None

    def update(self, value: float, now: float) -> None:
        self.current = value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)
        self.last_update = now

    def mark_unavailable(self, now: float) -> None:
        """Clear the live value without discarding valid session extremes."""
        self.current = None
        self.last_update = now


# Temperatures are converted to Fahrenheit and pressures to customary truck
# gauge units. The two restriction scalings remain inferred in CVSG.md; their
# engineering-unit conversions are therefore labeled as such in the names.
def celsius_tenths_to_fahrenheit(raw: int) -> float:
    return (raw / 10.0) * 9.0 / 5.0 + 32.0


def signed_16(raw: int) -> int:
    return raw - 0x10000 if raw & 0x8000 else raw


GAUGES = (
    GaugeDefinition(0x02, "Coolant temperature", "deg F", 1, celsius_tenths_to_fahrenheit),
    GaugeDefinition(0x03, "Fuel tank 1 level", "%", 1, lambda raw: raw / 10.0),
    GaugeDefinition(0x04, "Oil temperature", "deg F", 1, celsius_tenths_to_fahrenheit),
    GaugeDefinition(0x05, "Fuel tank 2 level", "%", 1, lambda raw: raw / 10.0),
    GaugeDefinition(0x06, "Engine oil pressure", "psi", 1, lambda raw: raw * 0.1 * KPA_TO_PSI),
    GaugeDefinition(0x09, "System voltage", "V", 2, lambda raw: raw / 100.0),
    GaugeDefinition(0x12, "Main transmission oil temp", "deg F", 1, celsius_tenths_to_fahrenheit),
    GaugeDefinition(0x15, "Transfer-case oil temp", "deg F", 1, celsius_tenths_to_fahrenheit),
    GaugeDefinition(0x16, "Manifold/boost pressure", "psi", 1, lambda raw: raw * 0.1 * KPA_TO_PSI),
    GaugeDefinition(0x17, "General oil temperature", "deg F", 1, celsius_tenths_to_fahrenheit),
    GaugeDefinition(0x18, "Aux transmission oil temp", "deg F", 1, celsius_tenths_to_fahrenheit),
    GaugeDefinition(0x19, "Front drive axle temp", "deg F", 1, celsius_tenths_to_fahrenheit),
    GaugeDefinition(0x1A, "Rear drive axle temp", "deg F", 1, celsius_tenths_to_fahrenheit),
    GaugeDefinition(0x1B, "Center drive axle temp", "deg F", 1, celsius_tenths_to_fahrenheit),
    GaugeDefinition(0x1D, "Primary air pressure", "psi", 1, lambda raw: raw * 0.1 * KPA_TO_PSI),
    GaugeDefinition(0x1E, "Secondary air pressure", "psi", 1, lambda raw: raw * 0.1 * KPA_TO_PSI),
    GaugeDefinition(0x1F, "Ammeter", "A", 1, lambda raw: signed_16(raw) / 10.0),
    GaugeDefinition(0x20, "Air-filter restriction (inferred)", "inH2O", 1, lambda raw: raw * 0.01 * KPA_TO_IN_H2O),
    GaugeDefinition(0x21, "Applied brake pressure", "psi", 1, lambda raw: raw * 0.1 * KPA_TO_PSI),
    GaugeDefinition(0x22, "Fuel-filter restriction (inferred)", "inHg", 1, lambda raw: raw * 0.01 * KPA_TO_IN_HG),
)
GAUGE_BY_ID = {gauge.gauge_id: gauge for gauge in GAUGES}


class CvsgDecoder:
    """Decode transition-only digital CSV data into CVSG gauge payloads."""

    def __init__(self, on_gauge_value: Callable[[int, int], None]) -> None:
        self.on_gauge_value = on_gauge_value
        self.bits: Optional[list[int]] = None
        self.frame_processed = False
        self.frames_seen = 0

    def accept_symbol(self, symbol: str) -> None:
        if symbol == "S":
            self.bits = []
            self.frame_processed = False
            return

        if self.bits is None:
            return

        self.bits.append(int(symbol))

        # ID, command, and payload occupy the first 32 bits after sync. Process
        # immediately; the final trailer bit may not have a following rising
        # edge before the inter-frame gap and is not required for passive reads.
        if len(self.bits) >= 32 and not self.frame_processed:
            gauge_id = bits_to_uint(self.bits[0:8])
            command = bits_to_uint(self.bits[8:16])
            payload = bits_to_uint(self.bits[16:32])
            self.frames_seen += 1
            self.frame_processed = True
            if command == 0x41:
                self.on_gauge_value(gauge_id, payload)

        if len(self.bits) >= 39:
            self.bits = None

    def decode_transitions(self, transitions: Iterable[tuple[float, int]]) -> int:
        """Decode timestamp/level rows and return the number of frames found."""
        before = self.frames_seen
        previous_level: Optional[int] = None
        rising_time: Optional[float] = None
        falling_time: Optional[float] = None

        for timestamp, level in transitions:
            if previous_level is None:
                previous_level = level
                continue
            if level == previous_level:
                continue

            if previous_level == 0 and level == 1:
                if rising_time is not None and falling_time is not None:
                    high_time = falling_time - rising_time
                    period = timestamp - rising_time
                    if 70e-6 <= period <= 90e-6:
                        self.accept_symbol(classify_symbol(high_time))
                    else:
                        # A long gap terminates any partial or completed frame.
                        self.bits = None
                rising_time = timestamp
                falling_time = None
            elif previous_level == 1 and level == 0:
                if rising_time is not None and timestamp > rising_time:
                    falling_time = timestamp

            previous_level = level

        return self.frames_seen - before


def classify_symbol(high_time: float) -> str:
    """Classify a CVSG pulse using thresholds documented in CVSG.md."""
    if high_time < 28e-6:
        return "0"
    if high_time < 52e-6:
        return "1"
    return "S"


def bits_to_uint(bits: Iterable[int]) -> int:
    value = 0
    for bit in bits:
        value = (value << 1) | bit
    return value


def read_transition_csv(path: Path) -> list[tuple[float, int]]:
    """Read Logic 2's transition CSV, tolerating minor header variations."""
    transitions: list[tuple[float, int]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames:
            raise ValueError("The exported digital CSV has no header.")

        time_column = next((name for name in reader.fieldnames if name.lower().startswith("time")), None)
        channel_column = next((name for name in reader.fieldnames if "channel 0" in name.lower()), None)
        if time_column is None or channel_column is None:
            raise ValueError(f"Unexpected digital CSV columns: {reader.fieldnames}")

        for row in reader:
            try:
                transitions.append((float(row[time_column]), int(float(row[channel_column]))))
            except (KeyError, TypeError, ValueError):
                # Ignore blank/incomplete lines sometimes present at export end.
                continue
    return transitions


def clear_console() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def format_value(state: GaugeState, value: Optional[float]) -> str:
    if value is None:
        return "NO-DATA"
    return f"{value:.{state.definition.precision}f} {state.definition.unit}"


def display(states: dict[int, GaugeState], device_text: str, sample_rate: int, frames: int, started: float) -> None:
    clear_console()
    print("PACCAR / Kenworth CVSG Live Gauge Monitor")
    print("=" * 86)
    print(f"Device: {device_text} | Channel: {CHANNEL} | Sample rate: {sample_rate:,} Sa/s")
    print(f"Session: {time.monotonic() - started:,.0f} seconds | Decoded frames: {frames}")
    print("Press any key to stop, disconnect, save cvsg.log, and close.")
    print("-" * 86)
    print(f"{'Gauge':34} {'Value':17} {'Minimum':17} {'Maximum':17}")
    print("-" * 86)
    for gauge in GAUGES:
        state = states[gauge.gauge_id]
        print(
            f"{gauge.name:34} "
            f"{format_value(state, state.current):17} "
            f"{format_value(state, state.minimum):17} "
            f"{format_value(state, state.maximum):17}"
        )
    print("-" * 86)
    print("Restriction scales are inferred; manifold pressure may be absolute or gauge pressure.")


def write_session_log(
    states: dict[int, GaugeState],
    started_at: datetime,
    ended_at: datetime,
    device_text: str,
    sample_rate: Optional[int],
    frames: int,
    error: Optional[str],
) -> None:
    """Append a complete session summary, including gauges with no data."""
    new_file = not LOG_PATH.exists() or LOG_PATH.stat().st_size == 0
    with LOG_PATH.open("a", encoding="utf-8", newline="\n") as log_file:
        if not new_file:
            log_file.write("\n")
        log_file.write("PACCAR / Kenworth CVSG session\n")
        log_file.write(f"Started: {started_at.isoformat(sep=' ', timespec='seconds')}\n")
        log_file.write(f"Ended:   {ended_at.isoformat(sep=' ', timespec='seconds')}\n")
        log_file.write(f"Device:  {device_text}\n")
        log_file.write(f"Channel: {CHANNEL}\n")
        log_file.write(f"Sample rate: {sample_rate if sample_rate is not None else 'NO-DATA'}\n")
        log_file.write(f"Decoded frames: {frames}\n")
        if error:
            log_file.write(f"Error: {error}\n")
        log_file.write("\nGauge | Minimum | Maximum\n")
        log_file.write("-" * 76 + "\n")
        for gauge in GAUGES:
            state = states[gauge.gauge_id]
            if state.minimum is None:
                log_file.write(f"{gauge.name} | NO-DATA | NO-DATA\n")
            else:
                log_file.write(
                    f"{gauge.name} | {format_value(state, state.minimum)} | "
                    f"{format_value(state, state.maximum)}\n"
                )


def ensure_automation_package():
    """Import Logic 2 Automation, installing it on first run if necessary."""
    try:
        from saleae import automation
        return automation
    except ImportError:
        print("Installing the required Saleae Logic 2 Automation package...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "logic2-automation>=1.0.11,<2"],
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        from saleae import automation
        return automation


def connect_manager(automation):
    """Use an existing automation server or launch a managed Logic 2 instance."""
    try:
        manager = automation.Manager.connect(port=10430, connect_timeout_seconds=2.0)
        return manager, False
    except Exception:
        if not LOGIC2_PATH.exists():
            raise FileNotFoundError(f"Logic 2 was not found at {LOGIC2_PATH}")
        manager = automation.Manager.launch(
            application_path=str(LOGIC2_PATH),
            connect_timeout_seconds=30.0,
            port=10430,
        )
        return manager, True


def capture_segment(automation, manager, device_id: str, sample_rate: int, export_dir: Path) -> Path:
    """Capture one short Channel 0 segment and export transition data."""
    if export_dir.exists():
        shutil.rmtree(export_dir)
    export_dir.mkdir(parents=True)

    device_configuration = automation.LogicDeviceConfiguration(
        enabled_digital_channels=[CHANNEL],
        digital_sample_rate=sample_rate,
    )
    capture_configuration = automation.CaptureConfiguration(
        capture_mode=automation.TimedCaptureMode(duration_seconds=CAPTURE_SECONDS),
    )
    with manager.start_capture(
        device_id=device_id,
        device_configuration=device_configuration,
        capture_configuration=capture_configuration,
    ) as capture:
        capture.wait()
        capture.export_raw_data_csv(directory=str(export_dir), digital_channels=[CHANNEL])

    csv_path = export_dir / "digital.csv"
    if not csv_path.exists():
        raise FileNotFoundError("Logic 2 did not create digital.csv.")
    return csv_path


def choose_sample_rate(automation, manager, device_id: str, export_dir: Path) -> tuple[int, Path]:
    errors: list[str] = []
    for rate in SAMPLE_RATE_CANDIDATES:
        try:
            return rate, capture_segment(automation, manager, device_id, rate, export_dir)
        except Exception as exc:
            errors.append(f"{rate:,} Sa/s: {exc}")
    raise RuntimeError("No supported Channel 0 sample rate succeeded:\n" + "\n".join(errors))


def key_pressed() -> bool:
    if os.name != "nt":
        return False
    import msvcrt
    if not msvcrt.kbhit():
        return False
    msvcrt.getwch()
    return True


def run_self_test() -> int:
    """Exercise timing, framing, signed conversion, and NO-DATA state offline."""
    decoded: list[tuple[int, int]] = []
    decoder = CvsgDecoder(lambda gauge_id, payload: decoded.append((gauge_id, payload)))

    frame_bits = f"{0x1F:08b}{0x41:08b}{0xFA24:016b}{0:07b}"
    symbols = "S" + frame_bits + "S"
    transitions: list[tuple[float, int]] = [(0.0, 0)]
    timestamp = 10e-6
    for symbol in symbols:
        high_width = {"0": 16.4e-6, "1": 40.4e-6, "S": 64.4e-6}[symbol]
        transitions.extend(((timestamp, 1), (timestamp + high_width, 0)))
        timestamp += 80e-6

    decoder.decode_transitions(transitions)
    assert decoded == [(0x1F, 0xFA24)], decoded
    assert GAUGE_BY_ID[0x1F].convert(0xFA24) == -150.0
    assert classify_symbol(16.4e-6) == "0"
    assert classify_symbol(40.4e-6) == "1"
    assert classify_symbol(64.4e-6) == "S"
    print("CVSG offline self-test passed.")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return run_self_test()

    if os.name != "nt":
        print("This live monitor requires Windows and Saleae Logic 2.")
        return 1

    states = {gauge.gauge_id: GaugeState(gauge) for gauge in GAUGES}
    started_at = datetime.now().astimezone()
    started_monotonic = time.monotonic()
    manager = None
    device_text = "NO-DATA"
    sample_rate: Optional[int] = None
    total_frames = 0
    fatal_error: Optional[str] = None

    try:
        automation = ensure_automation_package()
        print("Connecting to Saleae Logic 2...")
        manager, _manager_launched = connect_manager(automation)
        devices = manager.get_devices()
        if not devices:
            raise RuntimeError("Logic 2 reports no connected physical logic analyzer.")

        device = devices[0]
        device_text = f"{device.device_type.name} ({device.device_id})"

        with tempfile.TemporaryDirectory(prefix="cvsg-", dir=SCRIPT_DIR) as temp_dir:
            export_dir = Path(temp_dir) / "capture"
            sample_rate, first_csv = choose_sample_rate(
                automation, manager, device.device_id, export_dir
            )

            def on_value(gauge_id: int, payload: int) -> None:
                definition = GAUGE_BY_ID.get(gauge_id)
                if definition is None:
                    return
                if payload == INVALID_PAYLOAD:
                    states[gauge_id].mark_unavailable(time.monotonic())
                    return
                states[gauge_id].update(definition.convert(payload), time.monotonic())

            decoder = CvsgDecoder(on_value)
            total_frames += decoder.decode_transitions(read_transition_csv(first_csv))
            display(states, device_text, sample_rate, total_frames, started_monotonic)

            while not key_pressed():
                csv_path = capture_segment(
                    automation, manager, device.device_id, sample_rate, export_dir
                )
                total_frames += decoder.decode_transitions(read_transition_csv(csv_path))
                display(states, device_text, sample_rate, total_frames, started_monotonic)

    except KeyboardInterrupt:
        pass
    except Exception as exc:
        fatal_error = str(exc)
        clear_console()
        print("CVSG monitor could not continue.\n")
        print(fatal_error)
    finally:
        if manager is not None:
            try:
                manager.close()
            except Exception as exc:
                if fatal_error is None:
                    fatal_error = f"Logic 2 disconnect failed: {exc}"
        ended_at = datetime.now().astimezone()
        try:
            write_session_log(
                states,
                started_at,
                ended_at,
                device_text,
                sample_rate,
                total_frames,
                fatal_error,
            )
            print(f"\nDisconnected. Session summary saved to {LOG_PATH}")
        except Exception as exc:
            print(f"\nCould not save {LOG_PATH}: {exc}")
            fatal_error = fatal_error or str(exc)

    if fatal_error:
        # Keep startup/runtime errors visible when launched by double-click.
        print("\nPress any key to close.")
        import msvcrt
        msvcrt.getwch()
        return 1

    # Returning normally closes a console opened by double-click. If launched
    # from an existing terminal, only this process exits and the shell remains.
    time.sleep(0.35)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

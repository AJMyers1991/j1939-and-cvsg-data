#!/usr/bin/env python
"""Continuous PACCAR/Kenworth CVSG gauge monitor using native USB acquisition.

A bundled modern libusb runtime loads the open-source FX2LAFW firmware into the
Saleae Logic-compatible analyzer's volatile RAM and continuously acquires all
eight digital channels. Channel 0 is decoded in real time; no Logic 2 process,
segmented captures, exports, CSV files, or external Sigrok process are involved.

Run start-cvsg-monitor.cmd on Windows. Press any key to stop cleanly.
"""

from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
USB_RUNTIME_DIR = SCRIPT_DIR / "tools" / "usb1"
USB_DLL_PATH = USB_RUNTIME_DIR / "libusb-1.0.dll"
FIRMWARE_PATH = SCRIPT_DIR / "tools" / "fx2lafw-saleae-logic.fw"
LOG_PATH = SCRIPT_DIR / "cvsg.log"

# Keep the third-party USB wrapper private to this portable package.
if str(USB_RUNTIME_DIR.parent) not in sys.path:
    sys.path.insert(0, str(USB_RUNTIME_DIR.parent))
try:
    import usb1
except (ImportError, OSError) as exc:  # Reported clearly by ensure_runtime_files().
    usb1 = None  # type: ignore[assignment]
    USB_IMPORT_ERROR: Optional[BaseException] = exc
else:
    USB_IMPORT_ERROR = None

USB_VENDOR_ID = 0x0925
USB_PRODUCT_ID = 0x3881
USB_INTERFACE = 0
USB_ENDPOINT_IN = 0x82
USB_CONFIGURATION = 1
FX2_CPUCS_ADDRESS = 0xE600
FX2_FIRMWARE_CHUNK_SIZE = 4096
FX2_REENUMERATION_TIMEOUT_SECONDS = 5.0
TRANSFER_SIZE = 10_240
TRANSFER_COUNT = 32
TRANSFER_TIMEOUT_MS = 625
SAMPLE_QUEUE_CAPACITY = 256

CHANNEL = 0
CHANNEL_MASK = 1 << CHANNEL
SAMPLE_RATE = 1_000_000
INVALID_PAYLOAD = 0xF830
DISPLAY_INTERVAL_SECONDS = 0.10
STARTUP_TIMEOUT_SECONDS = 20.0

# At 1 MSa/s, each sample is exactly one microsecond. These thresholds are the
# documented CVSG timing limits converted to integer sample counts.
MIN_SYMBOL_PERIOD_SAMPLES = 70
MAX_SYMBOL_PERIOD_SAMPLES = 90
ZERO_ONE_THRESHOLD_SAMPLES = 28
ONE_SYNC_THRESHOLD_SAMPLES = 52

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
    last_raw_payload: Optional[int] = None

    def update(self, value: float, raw_payload: int, now: float) -> None:
        self.current = value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)
        self.last_update = now
        self.last_raw_payload = raw_payload

    def mark_unavailable(self, raw_payload: int, now: float) -> None:
        """Clear the live value without discarding valid session extremes."""
        self.current = None
        self.last_update = now
        self.last_raw_payload = raw_payload


def celsius_tenths_to_fahrenheit(raw: int) -> float:
    """Convert CVSG tenths of a degree Celsius to degrees Fahrenheit."""
    return (raw / 10.0) * 9.0 / 5.0 + 32.0


def signed_16(raw: int) -> int:
    """Interpret an unsigned 16-bit integer as two's-complement signed."""
    return raw - 0x10000 if raw & 0x8000 else raw


# Conventions intentionally match CVSG.md and the prior Logic 2 monitor.
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


def bits_to_uint(bits: Iterable[int]) -> int:
    """Convert an MSB-first bit sequence to an integer."""
    value = 0
    for bit in bits:
        value = (value << 1) | bit
    return value


class CvsgFrameDecoder:
    """Collect classified symbols into CVSG frames."""

    def __init__(self, on_gauge_value: Callable[[int, int], None]) -> None:
        self.on_gauge_value = on_gauge_value
        self.bits: Optional[list[int]] = None
        self.frame_processed = False
        self.frames_seen = 0

    def abort_partial_frame(self) -> None:
        """Discard framing state after an invalid symbol period or stream gap."""
        self.bits = None
        self.frame_processed = False

    def accept_symbol(self, symbol: str) -> None:
        """Accept one timing-classified symbol."""
        if symbol == "S":
            self.bits = []
            self.frame_processed = False
            return

        if self.bits is None:
            return

        self.bits.append(int(symbol))

        # The first 32 bits contain all passive gauge data. Process them without
        # waiting for the final trailer bit's following rising edge.
        if len(self.bits) >= 32 and not self.frame_processed:
            gauge_id = bits_to_uint(self.bits[0:8])
            command = bits_to_uint(self.bits[8:16])
            payload = bits_to_uint(self.bits[16:32])
            self.frames_seen += 1
            self.frame_processed = True
            if command == 0x41:
                self.on_gauge_value(gauge_id, payload)

        if len(self.bits) >= 39:
            self.abort_partial_frame()


class SampleStreamDecoder:
    """Convert packed FX2 samples into CVSG pulse-width symbols.

    The Saleae Logic-compatible FX2 streams all eight digital inputs in each
    byte. Channel 0 is bit zero. Run lengths are retained across pipe reads so
    chunk boundaries cannot split or corrupt a pulse measurement.
    """

    def __init__(self, frame_decoder: CvsgFrameDecoder) -> None:
        self.frame_decoder = frame_decoder
        self.previous_level: Optional[int] = None
        self.run_length = 0
        self.pending_high_samples: Optional[int] = None
        self.samples_seen = 0
        self.valid_symbols = 0
        self.rejected_periods = 0

    def feed(self, data: bytes) -> None:
        """Consume one raw binary block from the USB analyzer."""
        self.samples_seen += len(data)

        for sample in data:
            level = 1 if sample & CHANNEL_MASK else 0

            if self.previous_level is None:
                self.previous_level = level
                self.run_length = 1
                continue

            if level == self.previous_level:
                self.run_length += 1
                continue

            completed_run = self.run_length
            previous_level = self.previous_level
            self.previous_level = level
            self.run_length = 1

            if previous_level == 1 and level == 0:
                self.pending_high_samples = completed_run
                continue

            if previous_level == 0 and level == 1 and self.pending_high_samples is not None:
                high_samples = self.pending_high_samples
                period_samples = high_samples + completed_run
                self.pending_high_samples = None

                if MIN_SYMBOL_PERIOD_SAMPLES <= period_samples <= MAX_SYMBOL_PERIOD_SAMPLES:
                    self.frame_decoder.accept_symbol(classify_symbol_samples(high_samples))
                    self.valid_symbols += 1
                else:
                    self.rejected_periods += 1
                    self.frame_decoder.abort_partial_frame()


def classify_symbol_samples(high_samples: int) -> str:
    """Classify a one-microsecond-per-sample CVSG HIGH pulse."""
    if high_samples < ZERO_ONE_THRESHOLD_SAMPLES:
        return "0"
    if high_samples < ONE_SYNC_THRESHOLD_SAMPLES:
        return "1"
    return "S"


class MonitorRuntime:
    """Thread-safe gauge state and acquisition statistics."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.states = {gauge.gauge_id: GaugeState(gauge) for gauge in GAUGES}
        self.frame_decoder = CvsgFrameDecoder(self.on_value)
        self.sample_decoder = SampleStreamDecoder(self.frame_decoder)

    def on_value(self, gauge_id: int, payload: int) -> None:
        definition = GAUGE_BY_ID.get(gauge_id)
        if definition is None:
            return

        now = time.monotonic()
        with self.lock:
            state = self.states[gauge_id]
            if payload == INVALID_PAYLOAD:
                state.mark_unavailable(payload, now)
            else:
                state.update(definition.convert(payload), payload, now)

    def feed(self, data: bytes) -> None:
        # Decoder fields are owned by the stdout reader thread. Gauge updates
        # acquire the lock through on_value; display snapshots use the same lock.
        self.sample_decoder.feed(data)


class Fx2UsbStream:
    """Acquire continuous FX2LAFW samples through bundled modern libusb."""

    def __init__(self, runtime: MonitorRuntime) -> None:
        self.runtime = runtime
        self.stop_event = threading.Event()
        self.sample_queue: queue.Queue[Optional[bytes]] = queue.Queue(
            maxsize=SAMPLE_QUEUE_CAPACITY
        )
        self.context = None
        self.handle = None
        self.interface_claimed = False
        self.transfers: list[object] = []
        self.event_thread: Optional[threading.Thread] = None
        self.decoder_thread: Optional[threading.Thread] = None
        self.reader_error: Optional[str] = None
        self.empty_transfer_count = 0

    @staticmethod
    def _request_type_out() -> int:
        assert usb1 is not None
        return usb1.TYPE_VENDOR | usb1.RECIPIENT_DEVICE | usb1.ENDPOINT_OUT

    @staticmethod
    def _request_type_in() -> int:
        assert usb1 is not None
        return usb1.TYPE_VENDOR | usb1.RECIPIENT_DEVICE | usb1.ENDPOINT_IN

    def _matching_devices(self) -> list[object]:
        assert self.context is not None
        return [
            device
            for device in self.context.getDeviceList(skip_on_error=True)
            if device.getVendorID() == USB_VENDOR_ID
            and device.getProductID() == USB_PRODUCT_ID
        ]

    def _upload_firmware(self) -> None:
        matching = self._matching_devices()
        if not matching:
            raise RuntimeError(
                "Saleae Logic-compatible analyzer 0925:3881 was not found. "
                "Confirm it is connected and Logic 2 is closed."
            )

        handle = matching[0].open()
        try:
            handle.setConfiguration(USB_CONFIGURATION)
            handle.controlWrite(
                self._request_type_out(),
                0xA0,
                FX2_CPUCS_ADDRESS,
                0,
                b"\x01",
                timeout=1000,
            )
            firmware = FIRMWARE_PATH.read_bytes()
            for offset in range(0, len(firmware), FX2_FIRMWARE_CHUNK_SIZE):
                chunk = firmware[offset : offset + FX2_FIRMWARE_CHUNK_SIZE]
                written = handle.controlWrite(
                    self._request_type_out(),
                    0xA0,
                    offset,
                    0,
                    chunk,
                    timeout=1000,
                )
                if written != len(chunk):
                    raise RuntimeError(
                        f"Short FX2 firmware write at byte {offset}: "
                        f"{written}/{len(chunk)}"
                    )
            handle.controlWrite(
                self._request_type_out(),
                0xA0,
                FX2_CPUCS_ADDRESS,
                0,
                b"\x00",
                timeout=1000,
            )
        finally:
            handle.close()

    def _open_loaded_firmware(self) -> tuple[object, bytes, int]:
        assert usb1 is not None
        deadline = time.monotonic() + FX2_REENUMERATION_TIMEOUT_SECONDS
        last_error: Optional[BaseException] = None
        while time.monotonic() < deadline:
            time.sleep(0.10)
            for device in self._matching_devices():
                candidate = None
                try:
                    candidate = device.open()
                    version = bytes(
                        candidate.controlRead(
                            self._request_type_in(), 0xB0, 0, 0, 2, timeout=1000
                        )
                    )
                    if len(version) != 2 or version[0] != 1:
                        candidate.close()
                        continue
                    revision_data = bytes(
                        candidate.controlRead(
                            self._request_type_in(), 0xB2, 0, 0, 1, timeout=1000
                        )
                    )
                    revision = revision_data[0] if revision_data else -1
                    return candidate, version, revision
                except usb1.USBError as exc:
                    last_error = exc
                    if candidate is not None:
                        candidate.close()

        suffix = f" Last USB error: {last_error}" if last_error else ""
        raise RuntimeError(
            "FX2LAFW firmware did not re-enumerate within "
            f"{FX2_REENUMERATION_TIMEOUT_SECONDS:.0f} seconds.{suffix}"
        )

    def _set_reader_error(self, message: str) -> None:
        if self.reader_error is None:
            self.reader_error = message
        self.stop_event.set()

    def _on_transfer(self, transfer: object) -> None:
        """Copy one completed transfer, immediately resubmit it, then queue data."""
        assert usb1 is not None
        try:
            status = transfer.getStatus()
            if status in (usb1.TRANSFER_COMPLETED, usb1.TRANSFER_TIMED_OUT):
                actual_length = transfer.getActualLength()
                data = (
                    bytes(transfer.getBuffer()[:actual_length])
                    if actual_length > 0
                    else b""
                )
                if data:
                    self.empty_transfer_count = 0
                else:
                    self.empty_transfer_count += 1
                    if self.empty_transfer_count > TRANSFER_COUNT * 2:
                        self._set_reader_error(
                            "The FX2 analyzer stopped returning sample data."
                        )
                        return

                if not self.stop_event.is_set():
                    transfer.submit()
                if data:
                    try:
                        self.sample_queue.put_nowait(data)
                    except queue.Full:
                        self._set_reader_error(
                            "The CVSG decoder could not keep up with the USB sample stream."
                        )
                return

            if status == usb1.TRANSFER_CANCELLED and self.stop_event.is_set():
                return
            self._set_reader_error(f"USB sample transfer failed with status {status}.")
        except Exception as exc:
            self._set_reader_error(f"USB transfer callback failed: {exc}")

    def _decode_samples(self) -> None:
        try:
            while True:
                block = self.sample_queue.get()
                if block is None:
                    return
                self.runtime.feed(block)
        except Exception as exc:
            self._set_reader_error(f"Sample decoder failed: {exc}")

    def _handle_usb_events(self) -> None:
        assert self.context is not None
        assert usb1 is not None
        try:
            while not self.stop_event.is_set():
                self.context.handleEventsTimeout(0.10)
        except Exception as exc:
            self._set_reader_error(f"USB event loop failed: {exc}")
        finally:
            for transfer in self.transfers:
                try:
                    if transfer.isSubmitted():
                        transfer.cancel()
                except usb1.USBError:
                    pass

            deadline = time.monotonic() + 2.0
            while (
                any(transfer.isSubmitted() for transfer in self.transfers)
                and time.monotonic() < deadline
            ):
                try:
                    self.context.handleEventsTimeout(0.05)
                except usb1.USBError:
                    break

            while True:
                try:
                    self.sample_queue.put(None, timeout=0.10)
                    break
                except queue.Full:
                    if self.decoder_thread is None or not self.decoder_thread.is_alive():
                        break

    def start(self) -> None:
        ensure_runtime_files()
        assert usb1 is not None
        self.context = usb1.USBContext()
        try:
            self._upload_firmware()
            self.handle, version, revision = self._open_loaded_firmware()
            self.handle.claimInterface(USB_INTERFACE)
            self.interface_claimed = True

            self.decoder_thread = threading.Thread(
                target=self._decode_samples, name="cvsg-decoder", daemon=True
            )
            self.decoder_thread.start()

            self.transfers = []
            for _ in range(TRANSFER_COUNT):
                transfer = self.handle.getTransfer()
                transfer.setBulk(
                    USB_ENDPOINT_IN,
                    TRANSFER_SIZE,
                    callback=self._on_transfer,
                    timeout=TRANSFER_TIMEOUT_MS,
                )
                transfer.submit()
                self.transfers.append(transfer)

            self.event_thread = threading.Thread(
                target=self._handle_usb_events, name="fx2-usb-events", daemon=True
            )
            self.event_thread.start()

            # 48 MHz / (47 + 1) = 1 MHz. Flag 0x40 selects 48 MHz and
            # zero in the width bit selects one byte per eight-channel sample.
            start_command = bytes((0x40, 0x00, 0x2F))
            written = self.handle.controlWrite(
                self._request_type_out(),
                0xB1,
                0,
                0,
                start_command,
                timeout=1000,
            )
            if written != len(start_command):
                raise RuntimeError(
                    f"Short FX2 acquisition command: {written}/{len(start_command)}"
                )
            self.firmware_version = f"{version[0]}.{version[1]}"
            self.fx2_revision = revision
        except Exception:
            self.stop()
            raise

    def diagnostic_text(self) -> str:
        return self.reader_error or "No additional USB diagnostic text was returned."

    def is_alive(self) -> bool:
        return self.event_thread is not None and self.event_thread.is_alive()

    def stop(self) -> None:
        self.stop_event.set()
        if self.event_thread is not None:
            self.event_thread.join(timeout=3.0)
        if self.decoder_thread is not None:
            self.decoder_thread.join(timeout=3.0)

        if self.handle is not None:
            if self.interface_claimed:
                try:
                    self.handle.releaseInterface(USB_INTERFACE)
                except Exception:
                    pass
            self.handle.close()
            self.handle = None
            self.interface_claimed = False
        if self.context is not None:
            self.context.close()
            self.context = None


def ensure_runtime_files() -> None:
    """Reject incomplete copies with clear, actionable messages."""
    required = (
        USB_RUNTIME_DIR / "__init__.py",
        USB_DLL_PATH,
        FIRMWARE_PATH,
        USB_RUNTIME_DIR / "licenses" / "COPYING.LESSER",
    )
    missing = [path for path in required if not path.is_file()]
    if missing:
        missing_text = "\n".join(f"  {path}" for path in missing)
        raise FileNotFoundError(
            f"The bundled USB runtime is incomplete. Missing:\n{missing_text}"
        )
    if USB_IMPORT_ERROR is not None or usb1 is None:
        raise RuntimeError(f"Could not load the bundled USB runtime: {USB_IMPORT_ERROR}")


def enable_ansi_console() -> None:
    """Enable ANSI cursor control in a Windows 10+ console when possible."""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def clear_console() -> None:
    """Redraw in place without spawning `cls` ten times per second."""
    print("\033[2J\033[H", end="")


def key_pressed() -> bool:
    if os.name != "nt":
        return False
    import msvcrt

    if not msvcrt.kbhit():
        return False
    msvcrt.getwch()
    return True


def format_value(state: GaugeState, value: Optional[float]) -> str:
    if value is None:
        return "NO-DATA"
    return f"{value:.{state.definition.precision}f} {state.definition.unit}"


def display(runtime: MonitorRuntime, started: float) -> None:
    with runtime.lock:
        rows = [
            (
                gauge,
                format_value(runtime.states[gauge.gauge_id], runtime.states[gauge.gauge_id].current),
                format_value(runtime.states[gauge.gauge_id], runtime.states[gauge.gauge_id].minimum),
                format_value(runtime.states[gauge.gauge_id], runtime.states[gauge.gauge_id].maximum),
            )
            for gauge in GAUGES
        ]
        frames = runtime.frame_decoder.frames_seen
        samples = runtime.sample_decoder.samples_seen
        symbols = runtime.sample_decoder.valid_symbols
        rejected = runtime.sample_decoder.rejected_periods

    clear_console()
    print("PACCAR / Kenworth CVSG Live Gauge Monitor")
    print("=" * 91)
    print(f"Device: Saleae Logic-compatible FX2 (0925:3881) | Channel: {CHANNEL}")
    print(f"Continuous sample rate: {SAMPLE_RATE:,} Sa/s | Data received: {samples / 1_000_000:.1f} MB")
    print(
        f"Session: {time.monotonic() - started:,.0f} seconds | "
        f"Decoded frames: {frames:,} | Valid symbols: {symbols:,} | Rejected periods: {rejected:,}"
    )
    print("Press any key to stop, release the analyzer, save cvsg.log, and close.")
    print("-" * 91)
    print(f"{'Gauge':34} {'Value':17} {'Minimum':17} {'Maximum':17}")
    print("-" * 91)
    for gauge, current, minimum, maximum in rows:
        print(f"{gauge.name:34} {current:17} {minimum:17} {maximum:17}")
    print("-" * 91)
    print("Restriction scales are inferred; manifold pressure may be absolute or gauge pressure.")


def write_session_log(
    runtime: MonitorRuntime,
    started_at: datetime,
    ended_at: datetime,
    fatal_error: Optional[str],
) -> None:
    """Append every known gauge and its session extrema to cvsg.log."""
    with runtime.lock:
        states = runtime.states
        frames = runtime.frame_decoder.frames_seen
        samples = runtime.sample_decoder.samples_seen
        symbols = runtime.sample_decoder.valid_symbols
        rejected = runtime.sample_decoder.rejected_periods

        new_file = not LOG_PATH.exists() or LOG_PATH.stat().st_size == 0
        with LOG_PATH.open("a", encoding="utf-8", newline="\n") as log_file:
            if not new_file:
                log_file.write("\n")
            log_file.write("PACCAR / Kenworth CVSG session\n")
            log_file.write(f"Started: {started_at.isoformat(sep=' ', timespec='seconds')}\n")
            log_file.write(f"Ended:   {ended_at.isoformat(sep=' ', timespec='seconds')}\n")
            log_file.write("Device:  Saleae Logic-compatible FX2 (0925:3881)\n")
            log_file.write(f"Channel: {CHANNEL}\n")
            log_file.write(f"Sample rate: {SAMPLE_RATE}\n")
            log_file.write(f"Samples received: {samples}\n")
            log_file.write(f"Valid symbols: {symbols}\n")
            log_file.write(f"Rejected periods: {rejected}\n")
            log_file.write(f"Decoded frames: {frames}\n")
            if fatal_error:
                log_file.write(f"Error: {fatal_error}\n")
            log_file.write("\nGauge | Minimum | Maximum | Last raw payload\n")
            log_file.write("-" * 92 + "\n")
            for gauge in GAUGES:
                state = states[gauge.gauge_id]
                raw_text = "NO-DATA" if state.last_raw_payload is None else f"0x{state.last_raw_payload:04X}"
                if state.minimum is None:
                    log_file.write(f"{gauge.name} | NO-DATA | NO-DATA | {raw_text}\n")
                else:
                    log_file.write(
                        f"{gauge.name} | {format_value(state, state.minimum)} | "
                        f"{format_value(state, state.maximum)} | {raw_text}\n"
                    )


def synthetic_frame_samples(gauge_id: int, command: int, payload: int) -> bytes:
    """Build one idealized sample stream for offline decoder testing."""
    bits = f"{gauge_id:08b}{command:08b}{payload:016b}{0:07b}"
    symbols = "S" + bits + "S"
    output = bytearray([0] * 17)
    high_widths = {"0": 16, "1": 40, "S": 64}
    for symbol in symbols:
        high = high_widths[symbol]
        output.extend([1] * high)
        output.extend([0] * (80 - high))
    return bytes(output)


def run_self_test() -> int:
    """Exercise chunk boundaries, framing, signed values, and unavailable data."""
    decoded: list[tuple[int, int]] = []
    frame_decoder = CvsgFrameDecoder(lambda gauge_id, payload: decoded.append((gauge_id, payload)))
    sample_decoder = SampleStreamDecoder(frame_decoder)

    stream = (
        synthetic_frame_samples(0x1F, 0x41, 0xFA24)
        + bytes([0] * 113)
        + synthetic_frame_samples(0x09, 0x41, INVALID_PAYLOAD)
    )
    # Deliberately split pulses at irregular boundaries to verify stream state.
    boundaries = (1, 7, 31, 509, 1024, 67, 4096)
    offset = 0
    index = 0
    while offset < len(stream):
        size = boundaries[index % len(boundaries)]
        sample_decoder.feed(stream[offset : offset + size])
        offset += size
        index += 1

    assert decoded == [(0x1F, 0xFA24), (0x09, INVALID_PAYLOAD)], decoded
    assert GAUGE_BY_ID[0x1F].convert(0xFA24) == -150.0
    assert classify_symbol_samples(16) == "0"
    assert classify_symbol_samples(40) == "1"
    assert classify_symbol_samples(64) == "S"
    assert frame_decoder.frames_seen == 2
    print("CVSG continuous-stream offline self-test passed.")
    return 0


def run_diagnostics() -> int:
    """Check the packaged runtime without opening the analyzer."""
    ensure_runtime_files()
    assert usb1 is not None
    version = usb1.getVersion()
    print(f"Python USB wrapper: usb1 {usb1.__version__}")
    print(
        "Runtime: libusb "
        f"{version.major}.{version.minor}.{version.micro}.{version.nano}"
    )
    print(f"Firmware: {FIRMWARE_PATH}")
    print("Packaged runtime diagnostics passed; analyzer was not opened.")
    return 0


def wait_for_initial_samples(stream: Fx2UsbStream, runtime: MonitorRuntime) -> None:
    """Wait for acquisition or fail with concrete USB diagnostic output."""
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if key_pressed():
            raise KeyboardInterrupt
        if runtime.sample_decoder.samples_seen > 0:
            return
        if stream.reader_error:
            raise RuntimeError(f"Sample reader failed: {stream.reader_error}")
        if not stream.is_alive():
            raise RuntimeError(
                "The USB acquisition event loop stopped unexpectedly.\n\n"
                + stream.diagnostic_text()
            )
        time.sleep(0.05)

    details = stream.diagnostic_text()
    raise TimeoutError(
        f"No samples arrived within {STARTUP_TIMEOUT_SECONDS:.0f} seconds."
        f"\n\nUSB diagnostics:\n{details}"
    )


def run_live_monitor() -> int:
    if os.name != "nt":
        print("This live monitor requires Windows and the bundled USB runtime.")
        return 1

    enable_ansi_console()
    runtime = MonitorRuntime()
    stream = Fx2UsbStream(runtime)
    started_at = datetime.now().astimezone()
    started_monotonic = time.monotonic()
    fatal_error: Optional[str] = None

    try:
        clear_console()
        print("Connecting directly to the Saleae Logic-compatible analyzer...")
        print("Logic 2 must be closed. The analyzer remains passive on the CVSG data line.")
        print("Press any key to cancel.")
        stream.start()
        wait_for_initial_samples(stream, runtime)

        next_display = 0.0
        while True:
            if key_pressed():
                break
            if stream.reader_error:
                raise RuntimeError(f"Sample reader failed: {stream.reader_error}")
            if not stream.is_alive():
                raise RuntimeError(
                    "USB acquisition stopped unexpectedly.\n\n"
                    + stream.diagnostic_text()
                )

            now = time.monotonic()
            if now >= next_display:
                display(runtime, started_monotonic)
                next_display = now + DISPLAY_INTERVAL_SECONDS
            time.sleep(0.02)

    except KeyboardInterrupt:
        pass
    except Exception as exc:
        fatal_error = str(exc)
    finally:
        stream.stop()
        ended_at = datetime.now().astimezone()
        try:
            write_session_log(runtime, started_at, ended_at, fatal_error)
        except Exception as exc:
            fatal_error = fatal_error or f"Could not save {LOG_PATH}: {exc}"

    if fatal_error:
        clear_console()
        print("CVSG monitor could not continue.\n")
        print(fatal_error)
        print(f"\nSession diagnostics were saved to {LOG_PATH}")
        if not os.environ.get("CVSG_LAUNCHED_BY_CMD"):
            print("\nPress any key to close.")
            import msvcrt

            msvcrt.getwch()
        return 1

    print(f"\nDisconnected. Session summary saved to {LOG_PATH}")
    time.sleep(0.35)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--self-test", action="store_true", help="run the offline decoder test")
    mode.add_argument("--diagnose", action="store_true", help="check packaged files without opening USB")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        return run_self_test()
    if args.diagnose:
        return run_diagnostics()
    return run_live_monitor()


if __name__ == "__main__":
    raise SystemExit(main())

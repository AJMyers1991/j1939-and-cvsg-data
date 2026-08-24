# CVSG Monitor

Continuous, passive PACCAR/Kenworth CVSG monitoring through an eight-channel Saleae Logic-compatible FX2 analyzer.

## Start

1. Connect analyzer **Channel 0** to the CVSG signal and analyzer ground to the CVSG common/ground.
2. Close Saleae Logic 2. Logic 2 and the monitor cannot claim the analyzer simultaneously.
3. Double-click `start-cvsg-monitor.cmd`.
4. Press any key to stop.

The monitor releases the analyzer and appends a session summary to `cvsg.log` in this directory. Every known gauge is logged; gauges without valid data are recorded as `NO-DATA`.

## Architecture

```text
Analyzer (0925:3881)
  -> bundled usb1 wrapper and modern libusb
  -> volatile FX2LAFW firmware
  -> 32 continuously resubmitted asynchronous USB transfers
  -> continuous 1 MSa/s, eight-channel sample bytes
  -> Channel 0 pulse-width decoder
  -> live gauge table and cvsg.log
```

Logic 2, segmented captures, CSV exports, external Sigrok processes, system-wide Python packages, and driver replacement are not used.

## Bundled runtime

```text
tools\
├── fx2lafw-saleae-logic.fw
└── usb1\
    ├── __init__.py
    ├── _libusb1.py
    ├── _version.py
    ├── libusb1.py
    ├── libusb-1.0.dll
    └── licenses\
        ├── COPYING
        └── COPYING.LESSER
```

The USB runtime is private to this portable package; it is not installed into Windows or the user's Python environment. See `USB-RUNTIME.txt` for versions, sources, and licensing.

The analyzer's existing Saleae WinUSB driver is retained. Do not use Zadig or replace the driver.

## Offline checks

These commands do not open the analyzer:

```cmd
python.exe cvsg.py --self-test
python.exe cvsg.py --diagnose
```

The same options can be passed through the launcher:

```cmd
start-cvsg-monitor.cmd --self-test
start-cvsg-monitor.cmd --diagnose
```

## Returning to Logic 2

FX2LAFW is loaded only into volatile analyzer RAM. If Logic 2 does not immediately recognize the analyzer after the monitor exits:

1. Close the monitor and Logic 2.
2. Unplug the analyzer's USB cable.
3. Reconnect it.
4. Start Logic 2.

No EEPROM is written, no permanent firmware is flashed, and no Windows driver is changed.

## Interpretation cautions

- All output units are imperial.
- Air-filter and fuel-filter restriction scales remain inferred.
- Manifold pressure is intentionally not labeled as absolute or gauge pressure.
- Payload `0xF830` is unavailable data and never changes session minimum or maximum.
- This monitor is receive-only. It does not transmit onto CVSG.

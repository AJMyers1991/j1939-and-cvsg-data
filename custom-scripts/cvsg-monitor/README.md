# CVSG Monitor

Continuous, passive PACCAR/Kenworth CVSG monitoring through an eight-channel Saleae Logic-compatible FX2 analyzer.

## Start

1. Connect analyzer **Channel 0** to the CVSG signal and analyzer ground to the CVSG common/ground.
2. Close Saleae Logic 2. Logic 2 and Sigrok cannot claim the analyzer simultaneously.
3. Double-click `start-cvsg-monitor.cmd`.
4. Press any key to stop.

The monitor releases the analyzer and appends a session summary to `cvsg.log` in this directory. Every known gauge is logged; gauges without valid data are recorded as `NO-DATA`.

## Architecture

```text
Analyzer (0925:3881)
  -> bundled sigrok-cli / fx2lafw
  -> continuous 1 MSa/s, eight-channel binary samples
  -> Channel 0 pulse-width decoder
  -> live gauge table and cvsg.log
```

Logic 2, segmented captures, CSV exports, and Python packages are not used.

## Bundled runtime

The runtime is deliberately reduced to the four files required for this raw FX2 acquisition:

```text
tools\sigrok\
├── sigrok-cli.exe
├── python34.dll
├── COPYING
└── share\sigrok-firmware\fx2lafw-saleae-logic.fw
```

Sigrok's protocol-decoder library, bundled Python modules, unrelated device firmware, Zadig utilities, Start Menu shortcuts, and installer registration are not included. CVSG decoding is performed entirely by `cvsg.py`.

## Offline checks

These commands do not open the analyzer:

```cmd
py -3.11 cvsg.py --self-test
py -3.11 cvsg.py --diagnose
```

## Returning to Logic 2

The Sigrok firmware is loaded only into volatile analyzer RAM. If Logic 2 does not immediately recognize the analyzer after the monitor exits:

1. Close the monitor and Logic 2.
2. Unplug the analyzer's USB cable.
3. Reconnect it.
4. Start Logic 2.

No Zadig or driver replacement is required for the identified analyzer. Its existing Saleae driver uses WinUSB.

## Interpretation cautions

- All output units are imperial.
- Air-filter and fuel-filter restriction scales remain inferred.
- Manifold pressure is intentionally not labeled as absolute or gauge pressure.
- Payload `0xF830` is unavailable data and never changes session minimum or maximum.
- This monitor is receive-only. It does not transmit onto CVSG.

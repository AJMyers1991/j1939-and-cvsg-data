# PACCAR/Kenworth CVSG Auxiliary Gauge Bus

## System identification and wiring

The Kenworth Commercial Vehicle Smart Gauges (CVSG) data bus is a proprietary private data bus between the Common Electronic Control Unit (CECU) and the gauges on some Kenworth trucks produced approximately between 2005 and 2018.

A CVSG gauge can often be identified by examining the rear connection area of the gauge. A typical CVSG gauge has two matching four-pin connectors. In a CVSG gauge panel, most gauges are daisy-chained and connect only to the gauges immediately beside them rather than each gauge having its own dedicated data wire back to the CECU.  The first gauge in the chain will have a harness that runs back into the dash (assumedly to the CECU) while the last gauge in a chain will only have one of the two available connectors occupied.  Multiple gauge panels (the left 4 gauge panel close to the drivers' door vs the larger 4-14 gauge panel in the center of the dash) may not be connected directly together but are both connected to the CVSG harness somewhere inside the dash.

The presence of two four-pin connectors does not prove that a gauge uses CVSG data. Some gauges have the same connectors but also accept a direct sensor input. Such a gauge may use only the power, common, and illumination conductors from the shared connectors and may not participate in CVSG data communication.

The observed wiring scheme is:

| Wire color | Function |
|---|---|
| Yellow | +12 V DC gauge power |
| White | Common/ground (-) |
| Brown | +5 to +12 V DC gauge illumination only |
| Blue | CVSG data-bus positive/signal (+) |

The +12 V gauge supply and the CVSG data signal share the same white common/ground conductor. To observe CVSG traffic with a common USB logic analyzer, connect a digital input channel to the blue wire and connect the analyzer ground to the white wire. This is a single-ended signal referenced to the shared white ground; it is not a two-wire differential data pair.

Verify voltage levels and use suitable automotive input protection before connecting hardware. The wiring colors and production-year range above describe the investigated Kenworth implementation and should be verified against the wiring documentation for the specific truck before making permanent connections.

## Purpose and scope

This document describes how to capture and decode the CVSG data bus observed on a 2015 Kenworth T660. The bus carries auxiliary gauge information from the body control module (BCM) to the dashboard gauges.

The observed bus is:

- Unidirectional: BCM to gauges.
- A broadcast bus: the BCM repeatedly sends values for all configured gauges.
- Not CAN, J1939, or ordinary UART.
- Pulse-width encoded at approximately 12,500 symbols per second.
- Used for auxiliary gauges and their warning/telltale lamps.

Tachometer and speedometer data are not carried on this bus. No switch-input traffic has been observed on it.

This specification was derived from live vehicle captures and controlled gauge tests performed with PACCAR ESA. In each controlled test, a gauge was moved from its minimum value to its maximum value, returned to minimum, and then its telltale was switched on and off.

## Electrical and capture notes

The captures used one Saleae digital input, Channel 0, referenced to the other connected conductor/logic-analyzer ground. The raw CSV files contain transition timestamps in this form:

```csv
Time [s],Channel 0
0.000000000,0
0.000016375,1
0.000032750,0
```

The captures establish the digital timing and protocol but do not establish the vehicle-side voltage limits, polarity protection, or current-drive characteristics. Do not connect an ESP32 or other 3.3 V input directly to the truck wiring until the electrical levels have been measured. Use an automotive-rated protected input or appropriate comparator/level-conditioning circuit.

## Symbol encoding

Each symbol occupies approximately 80 microseconds, giving a symbol rate of approximately 12.5 ksym/s.

Measure each symbol from one rising edge to the next rising edge. The time from the rising edge to the intervening falling edge is the high-pulse width.

| Symbol | Typical HIGH time | Typical LOW time | Total period |
|---|---:|---:|---:|
| Binary `0` | 16.4 us | 63.6 us | 80.0 us |
| Binary `1` | 40.4 us | 39.6 us | 80.0 us |
| Synchronization `S` | 64.4 us | 15.6 us | 80.0 us |

Practical initial decoding thresholds are:

```text
HIGH < 28 us       => binary 0
28 us to < 52 us   => binary 1
HIGH >= 52 us      => synchronization symbol
```

Accept a rising-edge-to-rising-edge period of approximately 70-90 us. These thresholds leave substantial margin between the three observed pulse-width clusters.

The synchronization pulse is a third pulse-width symbol, not a data bit.

## Frame format

Every decoded message frame contains 40 symbols:

```text
1 synchronization symbol + 39 binary symbols
```

The 39 binary symbols are transmitted most-significant bit first and divide as follows:

```text
S | Gauge ID (8) | Command (8) | Payload (16) | Integrity field (7)
```

| Field | Length | Meaning |
|---|---:|---|
| Synchronization | 1 symbol | Approximately 64 us HIGH; starts a frame |
| Gauge ID | 8 bits | Destination/data type address |
| Command | 8 bits | Value, telltale, or bus-management command |
| Payload | 16 bits | Big-endian gauge value or command value |
| Integrity field | 7 bits | Deterministic check field; algorithm not yet solved |

The active portion of one frame is approximately 3.2 ms long. Frames are separated by gaps and scheduled repeatedly by the BCM.

### Bit and byte interpretation

After detecting the synchronization symbol, collect exactly 39 binary symbols. Number them `b0` through `b38` in arrival order:

```text
ID       = b0..b7
Command  = b8..b15
Payload  = b16..b31
Trailer  = b32..b38
```

Convert each field as an MSB-first binary integer. The payload is equivalent to:

```text
payload = (payload_high_byte << 8) | payload_low_byte
```

Some payloads must subsequently be interpreted as signed 16-bit values.

## Commands

### `0x41`: gauge value

Command `0x41` carries the live gauge value. Interpret and scale its 16-bit payload according to the gauge-ID table below.

### `0x48`: gauge telltale/warning lamp

Command `0x48` controls the telltale associated with the same gauge ID:

| Payload | Meaning |
|---:|---|
| `0x0000` | Telltale off |
| `0x0001` | Telltale on |

PACCAR ESA tests directly confirmed this behavior for all responding gauge addresses.

### `0x42`: recurring bus/status message

The recurring message `ID 0x00, command 0x42, payload 0x007F` has been observed frequently. It appears to be a bus heartbeat/status message, not a gauge reading.

## Gauge addresses and scaling

Temperatures use degrees Celsius on the wire. Pressure conversions shown below use:

```text
psi = kPa * 0.1450377377
```

| ID | Gauge | Payload interpretation | Status/notes |
|---:|---|---|---|
| `0x02` | Coolant temperature | `raw / 10` deg C | Confirmed |
| `0x03` | Fuel tank 1 level | `raw / 10` percent | Confirmed; ESA range 0-1000 |
| `0x04` | Oil temperature | `raw / 10` deg C | Confirmed by ESA |
| `0x05` | Fuel tank 2 level | `raw / 10` percent | Confirmed; ESA range 0-1000 |
| `0x06` | Engine oil pressure | `raw / 10` kPa; `raw * 0.0145037738` psi | Confirmed; ESA maximum 6880 |
| `0x09` | System voltage | `raw / 100` volts | Confirmed; ESA range 900-1800 |
| `0x12` | Main transmission oil temperature | `raw / 10` deg C | Confirmed by ESA |
| `0x15` | Transfer-case oil temperature | `raw / 10` deg C | ID confirmed by telltale; value stayed at 400 during its test |
| `0x16` | Manifold/boost pressure | `raw / 10` kPa; `raw * 0.0145037738` psi | Address confirmed; ESA range 0-5000. Whether the displayed value is absolute or gauge pressure needs confirmation |
| `0x17` | General oil temperature | `raw / 10` deg C | Confirmed by ESA |
| `0x18` | Auxiliary transmission oil temperature | `raw / 10` deg C | Confirmed by ESA |
| `0x19` | Front drive axle temperature | `raw / 10` deg C | Confirmed by ESA; sensor in test vehicle is inoperative |
| `0x1A` | Rear drive axle temperature | `raw / 10` deg C | Confirmed by live data and ESA |
| `0x1B` | Center drive axle temperature | `raw / 10` deg C | Both ESA center-axle choices use this same address |
| `0x1D` | Primary air pressure | `raw / 10` kPa; `raw * 0.0145037738` psi | Confirmed by ESA; range 0-10342 (approximately 150 psi) |
| `0x1E` | Secondary air pressure | `raw / 10` kPa; `raw * 0.0145037738` psi | Confirmed by ESA; range 0-10342 (approximately 150 psi) |
| `0x1F` | Ammeter | signed 16-bit `raw / 10` amperes | Confirmed by ESA sweep from -1500 to +1500 |
| `0x20` | Air-filter restriction vacuum | Likely `raw / 100` kPa | Address and range 0-1000 confirmed. Scale is strongly inferred but not yet cross-checked against a known physical reading |
| `0x21` | Applied brake pressure | `raw / 10` kPa; `raw * 0.0145037738` psi | Confirmed by ESA and brake-pedal testing; transmitted much more frequently than most gauges |
| `0x22` | Fuel-filter restriction vacuum | Likely `raw / 100` kPa | Address confirmed; ESA maximum observed 6772. Scale is inferred, not yet physically cross-checked |

No CVSG address was identified for DEF tank level. Enabling and exercising the DEF gauge in ESA produced no changing value, new ID, or telltale message on this bus. DEF/aftertreatment information may use another network or display path or this may be the direct result of the vehicle used for testing not being equipped with an OEM exhaust aftertreatment system.

## Invalid and unavailable values

The payload `0xF830`, interpreted as signed 16-bit, is `-2000`. It repeatedly appears for disabled, absent, or unavailable sensors. Treat it as an invalid/not-available sentinel, not as a real measurement.

Do not assume that zero is invalid. Zero is a valid reading for fuel level, pressure, applied brake pressure, boost, and restriction measurements.

Suggested validity logic:

```text
if payload == 0xF830:
    reading = unavailable
else:
    apply the scaling for the gauge ID
```

Other unassigned observations include:

- IDs `0x1C` and `0x23`, commonly carrying `0xF830` in the tested configuration.
- ID `0x24`, commonly carrying constant payload `10200` (`0x27D8`). It is not rear axle temperature and remains unidentified.

Do not assign names to these IDs without another controlled test.

## Typical update rates

Observed approximate update rates are:

- Most gauge-value messages: about 2 Hz per gauge.
- Most telltale messages: about 1 Hz per gauge.
- Applied brake pressure (`0x21`): about 20 Hz.
- Manifold/boost pressure (`0x16`): about 20 Hz.
- `ID 0x00 / command 0x42` heartbeat/status message: approximately 12.5 Hz.

These are observed scheduling rates, not requirements for a decoder. A receiver should accept frames whenever they arrive.

## Integrity field

The final seven bits are deterministic: every identical combination of ID, command, and payload observed across the available captures had the same seven trailing bits.

However:

- They do not match the standard CRC-7 variants tested so far.
- They do not behave as a simple affine/linear parity mapping of the preceding 32 bits.
- Simple sum, XOR, and common bit-order variations tested so far did not solve them.

For passive decoding, the field can be recorded and ignored while the timing and 32 information bits are otherwise valid. For transmitting synthetic frames, the check-field algorithm should be solved or a lookup table should be used only for exact previously observed messages. Do not assume that gauges will accept a frame with an arbitrary or zero trailer.

## Step-by-step decoding from a Saleae transition CSV

1. Export the digital channel as a transition/change-only CSV.
2. Read timestamp and logic-level columns.
3. Locate each rising edge.
4. For each rising edge, locate the next falling edge and following rising edge.
5. Calculate:
   - `high_time = falling_time - rising_time`
   - `period = next_rising_time - rising_time`
6. Reject candidates whose period is outside approximately 70-90 us.
7. Classify the symbol from `high_time`:
   - less than 28 us: `0`
   - 28-52 us: `1`
   - at least 52 us: synchronization
8. On synchronization, begin a new frame.
9. Collect the following 39 binary symbols.
10. Split them into 8-bit ID, 8-bit command, 16-bit payload, and 7-bit integrity field.
11. If the command is `0x41`, apply the ID-specific scaling.
12. If the command is `0x48`, interpret payload zero/one as telltale off/on.
13. Mark payload `0xF830` unavailable.
14. Save the most recent valid reading and timestamp for each gauge.

## Decoder pseudocode

```text
state = WAIT_FOR_SYNC
bits = []

for each valid 80-us pulse period:
    symbol = classify(high_time)

    if symbol == SYNC:
        state = COLLECT_FRAME
        bits = []
        continue

    if state != COLLECT_FRAME:
        continue

    append symbol to bits

    if length(bits) == 39:
        id      = bits_to_uint(bits[0:8])
        command = bits_to_uint(bits[8:16])
        payload = bits_to_uint(bits[16:32])
        trailer = bits_to_uint(bits[32:39])

        process(id, command, payload, trailer)
        state = WAIT_FOR_SYNC
```

If another synchronization symbol appears before 39 bits have been collected, discard the partial frame and restart at the new synchronization symbol.

## Conversion examples

### Coolant temperature

```text
ID = 0x02
command = 0x41
payload = 750

C = 750 / 10 = 75.0 C
F = (75.0 * 9 / 5) + 32 = 167.0 F
```

### Oil pressure

```text
ID = 0x06
command = 0x41
payload = 2923

kPa = 2923 / 10 = 292.3 kPa
psi = 292.3 * 0.1450377377 = 42.4 psi
```

### Voltage

```text
ID = 0x09
command = 0x41
payload = 1418

volts = 1418 / 100 = 14.18 V
```

### Ammeter

```text
ID = 0x1F
command = 0x41
payload bytes = 0xFA 0x24

unsigned payload = 64036
signed 16-bit payload = 64036 - 65536 = -1500
amperes = -1500 / 10 = -150.0 A
```

## Implementation cautions

- Decode high-pulse width, not UART start/stop bits.
- Do not mistake the approximately 64 us synchronization pulse for a binary `1`.
- Maintain MSB-first bit order.
- Preserve signed conversion for the ammeter and invalid sentinel handling.
- Expect partial frames at the beginning and end of any capture.
- Resynchronize whenever a new synchronization symbol is observed.
- Use timestamps or stale-data timeouts; an old valid value should not be presented forever if its message disappears.
- Keep raw payload and trailer values in logs. They are useful for future protocol refinement.
- Passive observation is low risk; active transmission onto a vehicle network can cause incorrect gauge indications. Test synthetic transmission on a bench setup before connecting it to a vehicle.

## Current confidence summary

Confirmed with controlled PACCAR ESA tests:

- Pulse-width symbol encoding and 40-symbol framing.
- ID, command, and payload field positions.
- Commands `0x41` and `0x48`.
- All named addresses in the gauge table except DEF, which produced no CVSG response.
- Temperature, fuel-level, voltage, pressure, and ammeter scaling shown as confirmed.
- Primary air is `0x1D`; secondary air is `0x1E`.
- `0x20` is air-filter restriction and `0x22` is fuel-filter restriction.

Still unresolved or requiring physical cross-checking:

- The seven-bit integrity-field algorithm.
- Exact engineering-unit scaling for air-filter and fuel-filter restriction.
- Whether manifold pressure is represented as absolute pressure or gauge/boost pressure.
- DEF information path/address.
- Functions of IDs `0x1C`, `0x23`, and `0x24`.

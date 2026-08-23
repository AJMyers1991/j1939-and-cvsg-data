# Generic SAE J1939 Broadcast Reference

## Purpose and scope

This document is a vehicle-independent reference for interpreting classic SAE J1939 traffic on heavy-duty vehicles. It covers:

- CAN identifier and canonical PGN extraction.
- PDU1 destination-specific and PDU2 broadcast addressing.
- Source-address discovery and address claiming.
- Broadcast, request-only, diagnostic, identification, acknowledgment, and transport-protocol PGNs.
- Every standards-defined PGN and signal currently represented by this repository's J1939 documents and DBC files.
- Safe passive-capture and narrowly scoped read-only request procedures.
- Repository-specific DBC conventions.

A standardized PGN is **not guaranteed to be present on every vehicle**. Equipment, controller generation, OEM configuration, network segmentation, and gateway routing determine what is actually broadcast. Source addresses are preferred assignments rather than permanent proof of controller identity; always verify Address Claimed traffic and the observed payload.

This is not a substitute for the licensed SAE J1939 Digital Annex (`J1939DA`). The Digital Annex remains authoritative for the complete PGN/SPN catalog, revisions, ranges, enumerations, repetition rates, and reserved values.

Vehicle-specific observations for the truck used to develop this repository are maintained separately in [`2015-Kenworth-T660-Glider.md`](2015-Kenworth-T660-Glider.md).

---

## J1939 network fundamentals

Classic J1939 normally uses:

- Extended 29-bit CAN identifiers.
- Classic CAN payloads of up to 8 bytes.
- 250 kbit/s on many legacy heavy-duty vehicles.
- 500 kbit/s on newer networks and some secondary networks.
- Multi-packet transport when a parameter group exceeds 8 bytes.

Bitrate must be established for the actual network. A vehicle may expose several physically separate CAN channels through one diagnostic connector.

### 29-bit CAN identifier

```text
Priority(3) | Reserved/EDP(1) | DP(1) | PF(8) | PS(8) | SA(8)
```

| Field | Meaning |
|---|---|
| Priority | Arbitration priority; lower numeric values win arbitration |
| Reserved/EDP | Reserved or Extended Data Page, depending on J1939 generation |
| DP | Data Page |
| PF | PDU Format |
| PS | Destination address for PDU1; group extension for PDU2 |
| SA | Source Address |

### Canonical PGN extraction

```python
# can_id is the 29-bit CAN identifier.
pf = (can_id >> 16) & 0xFF
ps = (can_id >> 8) & 0xFF
raw_pgn = (can_id >> 8) & 0x3FFFF

if pf < 0xF0:
    # PDU1: PS is a destination address, not part of the PGN.
    pgn = raw_pgn & 0x3FF00
    destination_address = ps
else:
    # PDU2: PS is the group extension and is part of the PGN.
    pgn = raw_pgn
    destination_address = 0xFF

source_address = can_id & 0xFF
priority = (can_id >> 26) & 0x07
```

Never fold a PDU1 destination address into the canonical PGN. For example:

| CAN ID | Canonical PGN | Destination | Source |
|---|---:|---:|---:|
| `0x18EAFFF9` | `0xEA00` Request | `0xFF` global | `0xF9` tool |
| `0x18EA31F9` | `0xEA00` Request | `0x31` | `0xF9` tool |
| `0x18DA00F9` | `0xDA00` diagnostic message | `0x00` | `0xF9` tool |
| `0x0CF00400` | `0xF004` EEC1 | global/PDU2 | `0x00` |

Destination-expanded values such as `0xEA31` can be useful for display, but protocol handlers must compare the canonical PGN `0xEA00` and keep destination `0x31` separate.

---

## Source addresses

J1939 controllers claim addresses by transmitting PGN `0xEE00` with their 64-bit NAME. Preferred addresses provide useful clues, but live Address Claimed traffic is the authoritative network observation.

Common preferred assignments include:

| SA | Conventional assignment |
|---:|---|
| `0x00` | Engine #1 |
| `0x03` | Transmission #1 |
| `0x0B` | Brakes — System Controller |
| `0x0F` | Retarder — Engine |
| `0x17` | Instrument Cluster #1 |
| `0x31` | Cab Controller — Primary |
| `0xF9` | Off-board Diagnostic/Service Tool #1 |
| `0xFE` | Null Address; not a usable claimed address |
| `0xFF` | Global address; all/any node |

Do not hard-code a controller role from SA alone when a controller can be self-configurable or when a gateway remaps traffic. Capture PGN `0xEE00`, decode NAME, and retain both SA and NAME in inventories.

---

## Broadcast, request-only, and destination-specific traffic

### Broadcast PGNs

Most cyclic application data uses PDU2 PGNs (`PF >= 0xF0`). The sender chooses a repetition rate appropriate to the parameter group. All nodes may receive the frame, but only equipped controllers populate supported SPNs.

### Request-only PGNs

Identification, configuration, and some historical/diagnostic PGNs are normally returned only after PGN `0xEA00` Request. A standard definition does not guarantee that every ECU supports a request or that a gateway forwards it.

### Destination-specific PGNs

PDU1 PGNs (`PF < 0xF0`) carry a destination in PS. Requests, acknowledgments, transport connection management, and many diagnostic protocols use destination-specific frames.

---

## Core network and transport PGNs

| PGN (hex) | PGN (dec) | Label | Purpose | Typical behavior |
|---:|---:|---|---|---|
| `0xC700` | 50944 | ETP.DT | Extended Transport Protocol — Data Transfer | Numbered data packets for application messages larger than classic TP supports |
| `0xC800` | 51200 | ETP.CM | Extended Transport Protocol — Connection Management | Extended-transport session management |
| `0xE800` | 59392 | ACKM | Acknowledgment | ACK, NACK, access denied, or cannot respond to a requested PGN |
| `0xEA00` | 59904 | RQST | Request | Requests one PGN; payload is the requested PGN in three-byte little-endian order |
| `0xEB00` | 60160 | TP.DT | Transport Protocol — Data Transfer | Carries numbered seven-byte chunks of a multi-packet payload |
| `0xEC00` | 60416 | TP.CM | Transport Protocol — Connection Management | BAM, RTS, CTS, End-of-Message ACK, or Abort |
| `0xEE00` | 60928 | AC | Address Claimed | Advertises a controller's 64-bit NAME for an SA |
| `0xEF00` | 61184 | PropA | Proprietary A | Destination-specific proprietary payload; meaning is OEM-defined |
| `0xFED8` | 65240 | CA | Commanded Address | Commands a NAME to use an address; multi-packet and not a passive/read-only operation |
| `0xFF00`–`0xFFFF` | 65280–65535 | PropB | Proprietary B | Broadcast proprietary PGNs; group extension identifies the OEM-defined PGN |

### Request payload

The Request PGN payload is always the requested PGN in least-significant-byte-first order:

```text
Request VIN PGN 0x00FEEC: EC FE 00
Request Component Identification 0x00FEEB: EB FE 00
Request Software Identification 0x00FEDA: DA FE 00
Request Address Claimed 0x00EE00: 00 EE 00
```

A diagnostic requester must claim a valid available source address before sending. The claimed SA and the SA in all Request and Transport Protocol control messages must match.

### Transport Protocol controls

| TP.CM control byte | Meaning |
|---:|---|
| `0x10` | Request To Send (RTS) |
| `0x11` | Clear To Send (CTS) |
| `0x13` | End-of-Message Acknowledgment |
| `0x20` | Broadcast Announce Message (BAM) |
| `0xFF` | Connection Abort |

- **BAM:** global, sender paced, and does not use CTS.
- **RTS/CTS:** destination-specific. The receiver must send correctly addressed CTS frames before TP.DT packets continue.
- Associate each TP session with source, destination, transferred PGN, packet count, and total byte count. Do not concatenate unrelated TP.DT traffic.

---

## Standard diagnostic PGNs

| PGN | Decimal | Label | Meaning | Read-only status |
|---:|---:|---|---|---|
| `0xFECA` | 65226 | DM1 | Active Diagnostic Trouble Codes | Normally broadcast; passive to receive |
| `0xFECB` | 65227 | DM2 | Previously Active DTCs | Read-only request |
| `0xFECC` | 65228 | DM3 | Clear/reset previously active DTCs | **Destructive; never send during read-only work** |
| `0xFECD` | 65229 | DM4 | Freeze Frame Parameters | Read-only request |
| `0xFECE` | 65230 | DM5 | Diagnostic Readiness 1 | Read-only |
| `0xFECF` | 65231 | DM6 | Pending DTCs | Read-only |
| `0xFED0` | 65232 | DM8 | Non-continuously monitored test results | Read-only response |
| `0xFED1` | 65233 | DM9 | Oxygen sensor test results | Read-only |
| `0xFED2` | 65234 | DM10 | Supported non-continuous test identifiers | Read-only |
| `0xFED3` | 65235 | DM11 | Clear/reset active DTCs | **Destructive; never send during read-only work** |
| `0xFED4` | 65236 | DM12 | Emissions-related active DTCs | Read-only |

`0xFECC` is DM3. VIN is `0xFEEC`; the difference is material. The VIN request bytes are `EC FE 00`, not `CC FE 00`.

---

## Generic application PGN catalog used by this repository

The following PGNs are standards-defined. They are vehicle-independent definitions, but broadcasting, source address, update rate, and populated SPNs remain implementation-dependent.

| PGN | Decimal | Label | Standard purpose | Length/behavior |
|---:|---:|---|---|---|
| `0xF000` | 61440 | ERC1 | Electronic Retarder Controller 1 | 8-byte cyclic retarder command/status |
| `0xF001` | 61441 | EBC1 | Electronic Brake Controller 1 | 8-byte cyclic brake/ABS status |
| `0xF002` | 61442 | ETC1 | Electronic Transmission Controller 1 | 8-byte cyclic transmission control/status |
| `0xF003` | 61443 | EEC2 | Electronic Engine Controller 2 | Engine load and related engine control data |
| `0xF004` | 61444 | EEC1 | Electronic Engine Controller 1 | Engine torque mode, torque, and speed |
| `0xFEAE` | 65198 | AIR1 | Air Supply Pressure | Pneumatic supply and service-brake circuit pressures |
| `0xFEBD` | 65213 | FD1 | Fan Drive 1 | Estimated fan speed, drive state, fan speed, hydraulic pressure |
| `0xFEBF` | 65215 | EBC2 | Wheel Speed Information | Front axle speed and relative wheel speeds |
| `0xFEC1` | 65217 | VDHR | High Resolution Vehicle Distance | High-resolution total and trip distance |
| `0xFECA` | 65226 | DM1 | Active Diagnostic Trouble Codes | Variable length; often cyclic and on change |
| `0xFEDA` | 65242 | SOFT | Software Identification | Variable-length ASCII; normally on request |
| `0xFEDF` | 65247 | EEC3 | Electronic Engine Controller 3 | Supplemental engine control data |
| `0xFEE0` | 65248 | VD | Vehicle Distance | Standard-resolution total and trip distance |
| `0xFEE1` | 65249 | RC | Retarder Configuration | Variable/multi-packet configuration data |
| `0xFEE2` | 65250 | TCFG | Transmission Configuration | Variable/multi-packet configuration data |
| `0xFEE3` | 65251 | EC1 | Engine Configuration 1 | Variable/multi-packet engine configuration |
| `0xFEE4` | 65252 | SHUTDN | Shutdown | Engine protection and shutdown status |
| `0xFEE5` | 65253 | HOURS | Engine Hours/Revolutions | Total engine hours and revolutions |
| `0xFEE6` | 65254 | TD | Time/Date | Calendar and clock information |
| `0xFEE7` | 65255 | VH | Vehicle Hours | Total vehicle hours |
| `0xFEE8` | 65256 | VDS | Vehicle Direction/Speed | Direction and navigation-related speed data |
| `0xFEE9` | 65257 | LFC | Fuel Consumption (Liquid) | Trip and total liquid-fuel consumption |
| `0xFEEA` | 65258 | VW | Vehicle Weight | Axle and gross vehicle weight data |
| `0xFEEB` | 65259 | CI | Component Identification | Make, model, serial number, and unit number; variable ASCII |
| `0xFEEC` | 65260 | VI | Vehicle Identification | SPN 237 VIN; variable ASCII |
| `0xFEED` | 65261 | CCSS | Cruise Control/Vehicle Speed Setup | Cruise and speed-limit configuration/status |
| `0xFEEE` | 65262 | ET1 | Engine Temperature 1 | Coolant, fuel, oil, and related temperatures |
| `0xFEEF` | 65263 | EFL/P1 | Engine Fluid Level/Pressure 1 | Fuel, oil, coolant pressure/level data |
| `0xFEF0` | 65264 | PTO | Power Takeoff Information | PTO state, speed, and set-point information |
| `0xFEF1` | 65265 | CCVS1 | Cruise Control/Vehicle Speed 1 | Wheel-based speed, brake/clutch/cruise status |
| `0xFEF2` | 65266 | LFE1 | Fuel Economy (Liquid) | Fuel rate and fuel-economy data |
| `0xFEF3` | 65267 | VP | Vehicle Position | Latitude and longitude |
| `0xFEF4` | 65268 | TIRE | Tire Condition | Tire pressure/temperature/status |
| `0xFEF5` | 65269 | AMB | Ambient Conditions | Barometric pressure and ambient temperatures |
| `0xFEF6` | 65270 | IC1 | Intake/Exhaust Conditions 1 | Intake, manifold, filter, and exhaust parameters |
| `0xFEF7` | 65271 | VEP1 | Vehicle Electrical Power 1 | Currents and electrical potentials |
| `0xFEF8` | 65272 | TRF1 | Transmission Fluids 1 | Transmission pressure, level, and temperature |
| `0xFEF9` | 65273 | AI | Axle Information | Axle weight/location information |
| `0xFEFA` | 65274 | B | Brakes | Brake application/primary/secondary pressure and parking-brake status |
| `0xFEFB` | 65275 | RF | Retarder Fluids | Retarder fluid level/pressure/temperature |
| `0xFEFC` | 65276 | DD | Dash Display | Washer level, fuel levels, filter differential pressures, cargo temperature |

---

## Common generic signal decodes

All byte positions below are **zero-based payload indices**. Multi-byte SPNs shown here are least-significant-byte first. Always confirm the current SAE definition and the actual controller payload before production use.

| Signal | PGN | SPN | Payload byte(s) | Standard scale/offset |
|---|---:|---:|---|---|
| Driver demand engine torque | `0xF004` | 512 | `[1]` | `raw − 125 %` |
| Actual engine torque | `0xF004` | 513 | `[2]` | `raw − 125 %` |
| Engine speed | `0xF004` | 190 | `[3:5]` LE16 | `raw × 0.125 rpm` |
| Engine percent load at current speed | `0xF003` | 92 | `[2]` | `raw × 1 %` |
| Wheel-based vehicle speed | `0xFEF1` | 84 | `[1:3]` LE16 | `raw ÷ 256 km/h` |
| Engine fuel rate | `0xFEF2` | 183 | `[0:2]` LE16 | `raw × 0.05 L/h` |
| Coolant temperature | `0xFEEE` | 110 | `[0]` | `raw − 40 °C` |
| Fuel temperature 1 | `0xFEEE` | 174 | `[1]` | `raw − 40 °C` |
| Engine total hours | `0xFEE5` | 247 | `[0:4]` LE32 | `raw × 0.05 h` |
| High-resolution total vehicle distance | `0xFEC1` | 917 | `[0:4]` LE32 | `raw × 5 m` |
| High-resolution trip distance | `0xFEC1` | 918 | `[4:8]` LE32 | `raw × 5 m` |
| Intake manifold pressure 1 | `0xFEF6` | 102 | `[1]` | `raw × 2 kPa absolute` |
| Intake manifold temperature 1 | `0xFEF6` | 105 | `[2]` | `raw − 40 °C` |
| Engine oil pressure | `0xFEEF` | 100 | `[3]` | `raw × 4 kPa` |
| Battery potential / power input 1 | `0xFEF7` | 168 | `[4:6]` LE16 | `raw × 0.05 V` |
| Keyswitch battery potential | `0xFEF7` | 158 | `[6:8]` LE16 | `raw × 0.05 V` |
| Service brake circuit 1 air pressure | `0xFEAE` | 1087 | `[2]` | `raw × 8 kPa` |
| Service brake circuit 2 air pressure | `0xFEAE` | 1088 | `[3]` | `raw × 8 kPa` |
| Brake primary pressure | `0xFEFA` | 117 | `[1]` | `raw × 4 kPa` |
| Brake secondary pressure | `0xFEFA` | 118 | `[2]` | `raw × 4 kPa` |
| Fuel level 1 | `0xFEFC` | 96 | `[1]` | `raw × 0.4 %` |
| Fuel level 2 | `0xFEFC` | 38 | `[6]` | `raw × 0.4 %` |
| Ambient air temperature | `0xFEF5` | 171 | `[3:5]` LE16 | `raw × 0.03125 − 273 °C` |
| Estimated percent fan speed | `0xFEBD` | 975 | `[0]` | `raw × 0.4 %` |
| Front axle speed | `0xFEBF` | 904 | `[0:2]` LE16 | `raw ÷ 256 km/h` |
| Relative wheel speeds | `0xFEBF` | 905–910 | `[2]`–`[7]` | `raw × 1/16 km/h − 7.8125 km/h` |

### Imperial conversion factors used by this repository

| Quantity | Repository conversion |
|---|---|
| Vehicle speed | `raw ÷ 256 × 0.621371 mph` |
| Fuel rate | `raw × 0.05 × 0.264172 gal/h` |
| Temperature, 8-bit `−40 °C` form | `raw × 1.8 − 40 °F` |
| Temperature, 16-bit `0.03125 K` form | `raw × 0.05625 − 459.4 °F` |
| Pressure at 1 kPa/bit | `raw × 0.145038 psi` |
| Pressure at 2 kPa/bit | `raw × 0.290076 psi` |
| Pressure at 4 kPa/bit | `raw × 0.580152 psi` |
| Pressure at 8 kPa/bit | `raw × 1.160304 psi` |
| High-resolution distance | `raw × 5 ÷ 1609.344 miles` |

For high-resolution distance, retain at least the repository's literal:

```text
0.003106855961 miles/bit
```

Rounding it to `0.003107` can create tens of miles of error when the raw counter is several hundred million.

---

## Identification PGNs

| PGN | Request payload | Standard content |
|---:|---|---|
| `0xFDC5` ECU Identification Information | `C5 FD 00` | ECU part, serial, location, type, manufacturer, and hardware identifiers where supported by the applicable revision |
| `0xFEDA` Software Identification | `DA FE 00` | Asterisk-delimited ASCII software identifiers |
| `0xFEEB` Component Identification | `EB FE 00` | SPN 586 Make, SPN 587 Model, SPN 588 Serial Number, SPN 233 Unit Number |
| `0xFEEC` Vehicle Identification | `EC FE 00` | SPN 237 VIN, normally ASCII and up to 17 VIN characters |

A valid road-vehicle VIN contains exactly 17 characters from:

```text
A-H, J-N, P, R-Z, 0-9
```

The letters `I`, `O`, and `Q` are excluded. Remove only documented delimiters, padding, or terminators. Do not mislabel an engine serial number, ECM Product ID, component unit number, or cached application record as a chassis VIN.

---

## Passive and active read safety

### Passive capture

A passive recorder:

- Connects and reads without calling `RP1210_SendMessage`.
- Does not claim to be passive if it sends requests, acknowledgments, flow control, or diagnostic services.
- May configure local adapter filters and receive formatting, provided those commands do not generate vehicle-bus messages.

### Active read-only request

An active identification read still transmits. Use these controls:

1. Confirm the correct physical channel, protocol, and bitrate passively.
2. Confirm the intended tester SA is unused.
3. Claim that SA with a valid J1939 NAME.
4. Send exactly one allowlisted request.
5. Wait through a complete response/timeout window.
6. Send only the minimum CTS/EOM control required for a matching directed TP response.
7. Disconnect cleanly.

Never send programming, calibration, security-access, reset, actuator-control, DTC-clear, DM3, or DM11 operations during read-only work.

---

## Data-quality rules

- A byte value of all ones commonly represents not available, error, or reserved states, but the exact range depends on the SPN length and definition.
- Do not decode `0xFF` as a valid 255-unit measurement unless the SPN explicitly permits it.
- Correlation is not identification. Validate dynamic signals through controlled state changes.
- Compare engine-running and engine-off states to distinguish live measurements from configuration constants.
- Validate source address and payload independently; the same PGN may be emitted by several controllers.
- Do not concatenate unrelated frames merely because they share a PGN.
- Treat proprietary A/B payloads as unknown until reproducibly decoded or documented by the OEM.

---

## Repository-specific DBC conventions

These are requirements of this repository's `j1939logger`/`canparse` pipeline, not universal on-wire J1939 rules:

1. Use `@1` signal notation because the current parser expects it. In standard DBC syntax, `@1` denotes Intel/little-endian signal encoding; it does not describe CAN wire serialization.
2. Use decimal scale literals, never fraction expressions.
3. Use actual observed CAN identifiers with real source addresses; the logger does not treat SA `0xFE` as a wildcard.
4. Bake imperial conversion into scale and offset.
5. Keep the odometer factor at `0.003106855961` miles/bit.
6. Keep PDU1 destination separate from the canonical PGN in application code.

Other DBC tools may use different start-bit and byte-order conventions. CAN itself does not have a universal `@0`/`@1` annotation; that syntax belongs to DBC interpretation.

---

## Repository file map

| File | Purpose |
|---|---|
| [`../dbc-files/j1939-generic.dbc`](../dbc-files/j1939-generic.dbc) | Generic engine/network signal subset used by this repository |
| [`2015-Kenworth-T660-Glider.md`](2015-Kenworth-T660-Glider.md) | Vehicle-specific connection, observed PGNs, deviations, and test results |
| [`../dbc-files/Paccar-Kenworth-Peterbilt/j1939-paccar.dbc`](../dbc-files/Paccar-Kenworth-Peterbilt/j1939-paccar.dbc) | PACCAR/Kenworth-specific DBC |
| [`../Paccar-CVSG/CVSG.md`](../Paccar-CVSG/CVSG.md) | Kenworth CVSG auxiliary gauge network |

---

## Reference authority

Use this document as a practical repository guide. For design, compliance, or production diagnostics, verify against the applicable editions of:

- SAE J1939 Digital Annex — official PGN/SPN assignments.
- SAE J1939-21 — classic data-link layer and Transport Protocol.
- SAE J1939-81 — network management and address claiming.
- SAE J1939-73 — diagnostics.
- SAE J1939-71 — vehicle application-layer definitions where applicable.

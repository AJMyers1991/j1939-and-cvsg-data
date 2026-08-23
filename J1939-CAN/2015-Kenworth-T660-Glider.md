# 2015 Kenworth T660 Glider — J1939 and Connection Record

## Scope

This document contains only information specific to the vehicle named in the title and the hardware/procedures used to connect to it. Generic J1939 definitions, canonical PGN parsing, standard request mechanics, and the broader PGN catalog are maintained in [`J1939-broadcast.md`](J1939-broadcast.md).

The observations here must not be assumed to apply unchanged to another Kenworth, Peterbilt, PACCAR chassis, Caterpillar engine, or J1939 vehicle.

---

## Vehicle identification

| Item | Vehicle-specific value |
|---|---|
| Vehicle | 2015 Kenworth T660 Glider |
| Engine | Caterpillar C15 |
| Aftertreatment | None; no DPF/DEF/SCR system |
| Chassis VIN | `1NKAGGGG90J123963` — known from external vehicle records, not recovered through the tested networks |
| J1939 channel | 250 kbit/s through the 9-pin Deutsch diagnostic connector |
| Legacy data link | J1708/J1587 active through the same diagnostic connection |
| Fuel system | Two 130-US-gallon saddle tanks with an equalizing crossover line |
| Units in this repository | Imperial: psi, °F, mph, miles, gal/h |

The 9-pin Deutsch diagnostic connector is under the dash near the steering column.

---

## Connection hardware and driver

Captures and controlled read-only tests use:

| Item | Value |
|---|---|
| Adapter | Noregon DLA+ 2.0 |
| RP1210 DeviceID | `100` |
| RP1210 implementation | `DLAUSB32` |
| DLL | `C:\Windows\System32\DLAUSB32.dll` |
| J1939 protocol string | `J1939` |
| J1708 protocol string | `J1708` |
| J1939 bitrate | 250 kbit/s |

### Passive J1939 connection

A truly passive capture on this truck does not need to claim a diagnostic source address or call `RP1210_SendMessage`:

```text
1. ClientConnect(0, 100, "J1939", 0, 0, 0)
2. SendCommand(3, cid, [], 0)       → all receive filters PASS
3. ReadMessage() loop
4. ClientDisconnect(cid)
```

### Active read-only identification connection

Only after the operator explicitly authorizes one exact request:

```text
1. ClientConnect(0, 100, "J1939", 0, 0, 0)
2. Confirm the normal 250 kbit/s bus and intended target are active
3. Confirm SA 0xF9 is not already in use
4. SendCommand(19, cid, address_claim, 10) → claim SA 0xF9
5. SendCommand(16, cid, [1], 1)            → transmit echo ON
6. SendCommand(3, cid, [], 0)              → all receive filters PASS
7. Send one allowlisted request
8. Receive the response or wait through the full timeout
9. ClientDisconnect(cid)
```

No programming, calibration, DTC clearing, reset, control, security-access, session scan, DID scan, or proprietary address scan has been authorized or performed.

### Noregon J1939 `ReadMessage` layout

| Offset | Size | Meaning |
|---:|---:|---|
| 0–3 | 4 | Big-endian timestamp; configured weight is 1000 ticks/s |
| 4 | 1 | Echo flag |
| 5–7 | 3 | PGN, least-significant byte first |
| 8 | 1 | Priority and RP1210 flags |
| 9 | 1 | Source Address |
| 10 | 1 | Destination Address / PS |
| 11+ | variable | Application payload |

Classic CAN frames carry at most eight data bytes. RP1210 records with 18-, 19-, 32-, or other larger application payloads are adapter-reassembled TP/ETP messages, not single CAN frames.

For PDU1 PGNs, the destination must remain separate:

```python
raw_pgn = (message[7] << 16) | (message[6] << 8) | message[5]
pf = (raw_pgn >> 8) & 0xFF
pgn = raw_pgn & 0x3FF00 if pf < 0xF0 else raw_pgn
source_address = message[9]
destination_address = message[10]
```

The earlier copied document's `pgn = pgn | da` example was incorrect for protocol dispatch because it converted destination-specific traffic into a noncanonical PGN.

---

## Physical-network inventory

### Confirmed networks

| Protocol/channel | Result |
|---|---|
| J1939 at 250 kbit/s | Active; normal engine, cab, brake, retarder, and other controller traffic |
| J1708/J1587 | Active; MIDs `0x80`, `0x88`, and intermittent requester `0xB4` observed |
| Raw CAN at 500 kbit/s | No traffic during the authorized passive inventory |
| ISO 15765 at 500 kbit/s | No traffic during the authorized passive inventory |

The 500 kbit/s check was receive-only. The known 250 kbit/s J1939 network was healthy both before and after it.

### Observed J1939 source addresses

| SA | Standard preferred role / observed interpretation | Vehicle status |
|---:|---|---|
| `0x00` | Engine #1 | Cat C15 engine ECU; confirmed primary engine-data source |
| `0x31` | Cab Controller — Primary | Kenworth/PACCAR CECU; confirmed body-data source and requester |
| `0x0B` | Brakes — System Controller | Brake-system traffic observed |
| `0x0F` | Retarder — Engine | Retarder/engine-brake traffic observed |
| `0x87` | Self-configurable/reserved-range participant | Request-only participant observed; not confirmed as a cyclic broadcaster |

A source address is not sufficient by itself to identify an ECU. Preserve Address Claimed NAME data when a capture includes it.

The Cat engine at SA `0x00` was observed claiming NAME `00000000010BFF0C`.

---

## Live-confirmed J1939 signals on this vehicle

All byte positions are **zero-based payload indices**. “Standard layout” means the observed signal occupies the standards-defined SPN location; the fact that this truck actually broadcasts it from the listed SA is the vehicle-specific observation.

| Signal | PGN / label | SA | Payload byte(s) | SPN | Decode used here | Vehicle-specific evidence |
|---|---|---:|---|---:|---|---|
| Driver demand torque | `0xF004` EEC1 | `0x00` | `[1]` | 512 | `raw − 125 %` | Changes with accelerator demand |
| Actual engine torque | `0xF004` EEC1 | `0x00` | `[2]` | 513 | `raw − 125 %` | Tracks engine load |
| Engine speed | `0xF004` EEC1 | `0x00` | `[3:5]` LE16 | 190 | `raw × 0.125 rpm` | Verified from idle through elevated RPM |
| Engine percent load | `0xF003` EEC2 | `0x00` | `[2]` | 92 | `raw %` | Changes with RPM/load |
| Engine fuel rate | `0xFEF2` LFE1 | `0x00` | `[0:2]` LE16 | 183 | `raw × 0.0132086 gal/h` | Increases with engine load; earlier documentation incorrectly described it as one byte |
| Coolant temperature | `0xFEEE` ET1 | `0x00` | `[0]` | 110 | `raw × 1.8 − 40 °F` | Matched engine temperature behavior |
| Fuel temperature | `0xFEEE` ET1 | `0x00` | `[1]` | 174 | `raw × 1.8 − 40 °F` | Stable and plausible relative to coolant/ambient |
| Engine hours | `0xFEE5` HOURS | `0x00` | `[0:4]` LE32 | 247 | `raw × 0.05 h` | Approximately 35,512.9 h during 2026-08-23 testing |
| High-resolution total distance | `0xFEC1` VDHR | `0x00` | `[0:4]` LE32 | 917 | `raw × 0.003106855961 mi` | 1,151,296 mi ground truth on 2026-08-23; earlier docs mislabeled this as SPN 245 |
| Engine oil pressure | `0xFEEF` EFL/P1 | `0x00` | `[3]` | 100 | `raw × 0.580152 psi` | Approximately 35.4 psi idle, 59.8 psi at 964 RPM, and 66.7 psi at 1,258 RPM |
| Battery potential / power input 1 | `0xFEF7` VEP1 | `0x00`, `0x31` | `[4:6]` LE16 | 168 | `raw × 0.05 V` | About 14.2 V running and 12.5 V engine-off; earlier docs mislabeled it as SPN 158 |
| Service brake circuit 1 air pressure | `0xFEAE` AIR1 | `0x31` | `[2]` | 1087 | `raw × 1.160304 psi` | Matched primary/rear dash air gauge around 110–126 psi |
| Service brake circuit 2 air pressure | `0xFEAE` AIR1 | `0x31` | `[3]` | 1088 | `raw × 1.160304 psi` | Matched secondary/front dash air gauge around 109–125 psi |
| Fuel level 1 — left tank | `0xFEFC` DD | `0x31` | `[1]` | 96 | `raw × 0.4 %` | 130-gallon left saddle tank |
| Fuel level 2 — right tank | `0xFEFC` DD | `0x31` | `[6]` | 38 | `raw × 0.4 %` | 130-gallon right saddle tank |
| Ambient air temperature | `0xFEF5` AMB | `0x31` | `[3:5]` LE16 | 171 | `raw × 0.05625 − 459.4 °F` | 63°F decoded versus 64°F dash indication |

### Vehicle-specific implementation notes

- The Cat C15 oil-pressure signal is at `payload[3]`, the fourth byte. This is the standard SPN 100 location. The earlier “Cat uses byte 4 instead of standard byte 3” statement was an indexing error caused by mixing one-based and zero-based byte numbers.
- The engine and CECU both emit VEP1. For the dash-like voltage on this vehicle, use SPN 168 at `payload[4:6]`, not SPN 158.
- The engine and CECU have both emitted `0xFEC1`; the SA `0x31` mirror has been approximately 0.2–0.25 mile above SA `0x00`. Preserve SA when comparing counters.
- Fuel tank percentages can change while parked because the equalizing crossover transfers fuel between tanks. Do not infer fuel use or refueling without comparing distance and time.

---

## Observed PGNs requiring revalidation or retained as context

These PGNs were seen on this truck but are not currently treated as confirmed custom gauges.

| PGN | SA | Correct standard identity | Vehicle observation/status |
|---:|---:|---|---|
| `0xFEF1` | `0x00`, `0x31` | CCVS1 — Cruise Control/Vehicle Speed 1 | Standard wheel-speed field observed at zero while parked; moving-speed validation remains outstanding |
| `0xF000` | `0x0F` | ERC1 — Electronic Retarder Controller 1 | Retarder/engine-brake traffic observed; individual switch/state fields not physically cross-checked |
| `0xF001` | `0x0B`, `0x31` | EBC1 — Electronic Brake Controller 1 | Brake-related status observed; pedal-switch interpretation not yet cross-checked |
| `0xFEBF` | `0x0B` | EBC2 — Wheel Speed Information | Standstill values observed; standard front/relative wheel-speed layout should be used for future moving validation |
| `0xFEBD` | `0x00` | FD1 — Fan Drive 1 | Previously described as an unknown 96% value; old byte/scale interpretation is withdrawn pending raw-payload re-audit |
| `0xFEDF` | `0x00` | EEC3 — Electronic Engine Controller 3 | Standard supplemental engine data; individual fields not independently validated |
| `0xFEE1` | `0x0F` | RC — Retarder Configuration | Static multi-packet configuration observed; not a VEP message |
| `0xFEE3` | `0x00` | EC1 — Engine Configuration 1 | Static multi-packet engine configuration observed; not a VEP message |
| `0xFEE4` | `0x00` | SHUTDN — Shutdown | A constant byte was previously mislabeled as oil pressure; that interpretation is withdrawn |
| `0xFEF0` | `0x00` | PTO — Power Takeoff Information | A constant byte was previously mislabeled as charging voltage; that interpretation is withdrawn |
| `0xFEF6` | `0x00` | IC1 — Intake/Exhaust Conditions 1 | Previous “boost at payload[2] × 1 kPa” interpretation is withdrawn: payload `[2]` is standard manifold temperature; SPN 102 pressure is payload `[1]` at 2 kPa/bit and requires live revalidation on this engine |
| `0xFEF8` | `0x31` | TRF1 — Transmission Fluids 1 | A static byte was previously mislabeled as BCM reference voltage; no voltage decode is retained |
| `0xFEFA` | `0x31` | B — Brakes | Standard brake-pressure/status PGN; previous 16-bit “high-resolution air” description is withdrawn pending raw revalidation |
| `0xFECA` | `0x31` and other diagnostic sources as applicable | DM1 — Active Diagnostic Trouble Codes | Variable-length diagnostic messages; previous “trip data burst” interpretation was incorrect |

### Corrected DM1 interpretation

`0xFECA` is not trip odometer or trip fuel data. It is DM1. A variable-length 18-byte RP1210 payload is consistent with a multi-packet/reassembled active-DTC message. Decode it as lamp status plus DTC entries under J1939-73, preserving source address.

---

## Signals absent from this J1939 diagnostic channel

The following dash measurements were repeatedly unavailable in exposed J1939 SPNs and are carried on the Kenworth CVSG gauge network or are not equipped:

| Signal | Vehicle-specific route/status |
|---|---|
| Ammeter/current gauge | Sensor/CECU to CVSG smart-gauge network; not available on the exposed J1939 diagnostic channel |
| Front drive-axle temperature | CVSG-only on this truck |
| Rear drive-axle temperature | CVSG-only on this truck |
| Suspension pressure | CVSG-only on this truck |
| Air-filter restriction | CVSG smart-gauge data observed; not available as a populated J1939 value |
| Fuel-filter restriction | Sensor/value not populated on this truck |

See [`../Paccar-CVSG/CVSG.md`](../Paccar-CVSG/CVSG.md) for the separate CVSG connection and signal record.

---

## Identification behavior unique to this vehicle

### Known result

The known chassis VIN is:

```text
1NKAGGGG90J123963
```

It was **not** recovered through any bounded standard network-identification route tested on this vehicle.

### J1939 and UDS results

| Test | Exact request/route | Vehicle result |
|---|---|---|
| Global standard VIN | Request `0xFEEC`, data `EC FE 00`, claimed tester SA `0xF9` | No VIN response |
| Directed VIN to Cat engine | Request `0xFEEC` to SA `0x00` | Engine returned J1939 NACK payload `01 FF FF FF FF EC FE 00` |
| Directed VIN to CECU | Request `0xFEEC` to SA `0x31` | No response, ACK, or NACK |
| Directed VIN to SA `0x0B` | Request `0xFEEC` | No response |
| CECU Component Identification | Request `0xFEEB` to SA `0x31`, data `EB FE 00` | No response |
| Engine UDS VIN | PGN `0xDA00`, SA `0xF9` to `0x00`, `03 22 F1 90 00 00 00 00` | Exact transmit echo; no positive response, NRC, or ISO-TP response |
| CECU UDS VIN | PGN `0xDA00`, SA `0xF9` to `0x31`, same payload | Exact transmit echo; no positive response, NRC, or ISO-TP response |

The CECU at SA `0x31` independently broadcasts global requests for VIN PGN `0xFEEC`; no exposed J1939 module answers those requests. That is strong vehicle-specific evidence that the standard VIN PGN is unavailable on this network, not merely that the off-board request was malformed.

### Parallel J1708/J1587 results

The legacy J1708 network is active through the same DLA+ connection. Observed participants:

| MID | Observation |
|---:|---|
| `0x80` | Engine participant; normal traffic and supported PID responses observed |
| `0x88` | Active but not conclusively identified |
| `0xB4` | Intermittent requester; naturally requested engine PID 247 |

Standard identification tests:

```text
AC 80 ED 80   # PID 237 VIN, engine MID 0x80 — no response
AC 00 ED      # PID 237 VIN, global — no response
AC 80 ED B4   # PID 237 VIN, MID 0xB4 — no response
AC 00 F3      # PID 243 Component Identification, global — no response
```

Positive control:

```text
Request:  B4 00 F7
Response: 80 F7 04 E1 DE 0A 00
```

The positive control proves the physical J1708 path and standard PID 0 request/response mechanism work. Do not repeat the failed VIN/Component Identification requests without new evidence.

### Remaining identification path

The next evidence-based route is a passive same-adapter capture while JPRO, PACCAR ESA, or Caterpillar ET performs ordinary identification. A displayed Cat ECM Product ID must not automatically be reported as the chassis VIN.

---

## Vehicle-specific validation procedures

### Engine-off comparison

Use this truck's engine-running versus key-on/engine-off states to distinguish live values from constants:

| Candidate | Running | Engine off | Result |
|---|---:|---:|---|
| VEP1 SPN 168 battery input | About 14.2 V | About 12.5 V | Live electrical measurement |
| EFL/P1 SPN 100 oil pressure | About 35–67 psi | 0 psi | Live oil pressure |
| Former `0xFEF0` voltage interpretation | 14.0 V | 14.0 V | Withdrawn; static PTO payload, not voltage |
| Former `0xFEE4` pressure interpretation | 36.5 psi | 36.5 psi | Withdrawn; Shutdown payload, not oil pressure |

### RPM sweep

For Cat C15 engine parameters:

1. Establish low-idle baseline.
2. Raise engine speed to a stable intermediate value.
3. Repeat at a higher stable value if safe.
4. Verify engine speed, torque/load, fuel rate, and oil pressure move in the expected direction.
5. Reject constants and coincidental correlations.

### Dash cross-reference

This project accepts approximately 5–10% agreement with analog truck gauges. Preserve the raw value, exact timestamp, source address, and dash observation so scaling can be revisited.

### Fuel-tank validation

Because the two 130-gallon tanks equalize through a crossover line:

- Compare both tank percentages together.
- Check odometer and engine hours before inferring consumption.
- Treat opposite-direction parked changes as equalization unless refueling or a leak is independently known.

---

## Vehicle DBC follow-up items

The documentation audit identified legacy DBC definitions that should be corrected in a separate DBC change rather than silently treated as verified:

- LFE1 fuel rate must be 16-bit at payload `[0:2]`, not 8-bit.
- IC1 SPN 102 manifold pressure is payload `[1]` at 2 kPa/bit; the existing boost scale requires correction/revalidation.
- VDHR high-resolution total distance is SPN 917, not SPN 245.
- VEP1 payload `[4:6]` is SPN 168, not SPN 158.
- The PACCAR DBC comment describing oil pressure as a Cat-specific byte exception should be removed; `payload[3]` is the standard SPN 100 position.

This document records the corrected interpretation but does not modify the DBC files.

---

## Current confidence summary

### Confirmed on this vehicle

- J1939 at 250 kbit/s and J1708/J1587 are both active.
- Cat C15 SA `0x00`, CECU SA `0x31`, brake SA `0x0B`, and retarder SA `0x0F` broadcast; unknown SA `0x87` has been observed only as a requester.
- RPM, torque, load, fuel rate, coolant/fuel temperature, engine hours, high-resolution distance, oil pressure, voltage, service-brake air pressures, dual fuel levels, and ambient temperature have live supporting evidence.
- Ammeter, axle temperatures, suspension pressure, and air-filter restriction are unavailable from the exposed J1939 diagnostic channel and use the CVSG path on this truck.
- Standard J1939 VIN, J1939 Component Identification, J1587 VIN, J1587 Component Identification, and default-session UDS VIN reads do not return identification data.

### Still unresolved

- Identity/function of SA `0x87` and J1708 MID `0x88`.
- Moving validation of CCVS1 vehicle speed.
- Moving wheel-speed validation and individual brake/retarder status fields.
- Correct live Cat C15 manifold-pressure/boost decode after withdrawal of the old payload `[2]` interpretation.
- Whether OEM software obtains a live chassis VIN, a cached VIN, or an engine Product ID.

---

*Last documentation audit: 2026-08-23.*

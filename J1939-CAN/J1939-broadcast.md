# J1939 Diagnostic Bus — 2015 Kenworth T660 (Cat C15)

## System identification

The J1939 CAN bus is a SAE-standard heavy-duty vehicle network operating at 250 kbps. On this 2015 Kenworth T660 with Caterpillar C15 engine, the bus is accessed through the standard 9-pin Deutsch diagnostic connector located under the dash near the steering column.

The observed bus carries data from four source addresses:

| SA | ECU | Type |
|----|-----|------|
| 0x00 | Engine ECU (Cat C15) | Broadcasts RPM, torque, temperatures, hours, odometer, boost, oil pressure, voltage |
| 0x31 | Body Control Module (BCM / CECU) | Broadcasts fuel levels, air pressures, ambient temperature, voltage, trip data |
| 0x0B | Brake System Controller | Broadcasts wheel speeds, brake switch status |
| 0x0F | Retarder / Engine Brake | Broadcasts Jake brake status, static configuration |

Messages use **@1 Motorola (big-endian) bit numbering** on the wire but most signal data is **little-endian within bytes**. The DBC files in this repository use the Motorola convention per the `canparse` crate used by the j1939logger tool.

## Purpose and scope

This document describes every J1939 PGN observed on this vehicle — which signals are confirmed against dash gauges, which are standard SAE broadcasts, and which are unconfirmed or OEM-specific.

The capture methodology is:

- **Passive read-only** — no messages are transmitted to the bus except J1939 PGN requests (0x18EAFFF9) for diagnostic interrogation.
- **Live verification** — every confirmed signal was cross-checked against the dash gauge or known ground truth while the engine was running.
- **Engine-off test** — a signal that does not change between engine-on and engine-off is a static configuration value, not a live gauge.

Signals are presented in imperial units (psi, °F, mph, miles, gallons/hour). Conversion from J1939 metric raw values is baked into every scaling equation.

## Connection hardware

Capture was performed with a Noregon DLA+ 2.0 RP1210-compliant adapter (DeviceID=100) using `C:\Windows\System32\DLAUSB32.dll`. The adapter communicates over USB and provides read-only J1939 access with the following connection sequence:

```text
1. ClientConnect(0, 100, "J1939", 0, 0, 0)   → ClientID
2. SendCommand(19, cid, address_claim, 10)     → claim SA 0xF9
3. SendCommand(16, cid, [1], 1)                → echo ON
4. SendCommand(3, cid, [], 0)                  → all filters PASS
5. ReadMessage() loop                          → non-blocking read
6. ClientDisconnect(cid)
```

Binary message format from `ReadMessage`:

| Offset | Size | Description |
|--------|------|-------------|
| 0–3 | 4 | Timestamp (big-endian u32, milliseconds) |
| 4 | 1 | Echo flag |
| 5 | 1 | PGN byte 0 (LSB) |
| 6 | 1 | PGN byte 1 |
| 7 | 1 | PGN byte 2 (MSB) |
| 8 | 1 | Priority + flags |
| 9 | 1 | Source Address |
| 10 | 1 | Destination / PS byte |
| 11+ | var | Payload data |

PGN construction:

```text
pgn = (data[7] << 16) | (data[6] << 8) | data[5]
da  = data[10]
if pgn < 0xF000:
    pgn = pgn | da   # PDU1: PS is destination-specific
sa  = data[9]
```

## CAN ID construction

J1939 29-bit CAN ID:

```text
Priority(3) | EDP(1) | DP(1) | PF(8) | PS(8) | SA(8)
```

- PF < 240 (PDU1): PGN = `(DP << 16) | (PF << 8)`, PS = destination address
- PF ≥ 240 (PDU2): PGN = `(DP << 16) | (PF << 8) | PS`, PS = group extension

**Examples from this vehicle:**

| CAN ID | Priority | PGN | SA | Signal |
|--------|----------|-----|----|--------|
| 0x0CF00400 | 3 | 0xF004 (EEC1) | 0x00 | Engine speed, torque |
| 0x18FEF100 | 6 | 0xFEF1 (CCVS) | 0x00 | Vehicle speed |
| 0x18FEEE00 | 6 | 0xFEEE (ET1) | 0x00 | Coolant/fuel temperature |
| 0x18FEAE31 | 6 | 0xFEAE (Air Press) | 0x31 | Primary/secondary air |
| 0x18FEFC31 | 6 | 0xFEFC (Fuel) | 0x31 | Fuel tank levels |

---

## Confirmed Signals — Engine ECU (SA 0x00)

*All signals in this section are SAE J1939-71 standard. They work on **any** J1939-compliant diesel regardless of make or model — same PGN, same byte positions, same scaling.*

### Engine Speed (RPM)

| | |
|---|---|
| **PGN** | 0xF004 (61444) — EEC1 |
| **SA** | 0x00 (Engine ECU) |
| **Byte(s)** | 3–4, little-endian 16-bit |
| **SPN** | 190 |
| **Scale** | `raw × 0.125 rpm` |
| **DBC** | `24|16@1+ (0.125,0)` |
| **Notes** | Idle ~600–680 rpm. Universal — works on every J1939 diesel. |

### Driver Demand Torque

| | |
|---|---|
| **PGN** | 0xF004 (61444) — EEC1 |
| **SA** | 0x00 (Engine ECU) |
| **Byte(s)** | 1 |
| **SPN** | 512 |
| **Scale** | `(raw − 125) %` |
| **DBC** | `8|8@1+ (1,-125)` |
| **Notes** | Idle = 0%. Tracks accelerator pedal. Shared PGN with RPM and Actual Torque. |

### Actual Engine Torque

| | |
|---|---|
| **PGN** | 0xF004 (61444) — EEC1 |
| **SA** | 0x00 (Engine ECU) |
| **Byte(s)** | 2 |
| **SPN** | 513 |
| **Scale** | `(raw − 125) %` |
| **DBC** | `16|8@1+ (1,-125)` |
| **Notes** | Idle ~20–30% at low idle. Rises with load. Shared PGN with RPM and Demand Torque. |

### Engine Load

| | |
|---|---|
| **PGN** | 0xF003 (61443) — EEC2 |
| **SA** | 0x00 (Engine ECU) |
| **Byte(s)** | 2 |
| **SPN** | 92 |
| **Scale** | `raw × 1 %` |
| **DBC** | `16|8@1+ (1,0)` |
| **Notes** | Percent of available torque at current RPM. Idle ~20%. |

### Vehicle Speed

| | |
|---|---|
| **PGN** | 0xFEF1 (65265) — CCVS |
| **SA** | 0x00 (Engine ECU) |
| **Byte(s)** | 1–2, little-endian 16-bit |
| **SPN** | 84 |
| **Scale** | `raw ÷ 256 × 0.621371 mph` |
| **DBC** | `8|16@1+ (0.002427,0)` |
| **Notes** | Raw value = km/h × 256. Start bit is **8** — the template default of 24 reads a different field. BCM mirrors on SA 0x31. |

### Fuel Rate

| | |
|---|---|
| **PGN** | 0xFEF2 (65266) — LFE |
| **SA** | 0x00 (Engine ECU) |
| **Byte(s)** | 0 |
| **SPN** | 183 |
| **Scale** | `raw × 0.05 × 0.264172 gal/h` |
| **DBC** | `0|8@1+ (0.013208571,0)` |
| **Notes** | Raw units are L/h at 0.05 L/h/bit. Idle ~0.65 gal/h. BCM mirrors on SA 0x31. |

### Coolant Temperature

| | |
|---|---|
| **PGN** | 0xFEEE (65262) — ET1 |
| **SA** | 0x00 (Engine ECU) |
| **Byte(s)** | 0 |
| **SPN** | 110 |
| **Scale** | `(raw − 40) × 1.8 °F` |
| **DBC** | `0|8@1+ (1.8,-40)` |
| **Notes** | J1939 raw = °C with −40°C offset. Shared PGN with Fuel Temp. |

### Fuel Temperature

| | |
|---|---|
| **PGN** | 0xFEEE (65262) — ET1 |
| **SA** | 0x00 (Engine ECU) |
| **Byte(s)** | 1 |
| **SPN** | 174 |
| **Scale** | `(raw − 40) × 1.8 °F` |
| **DBC** | `8|8@1+ (1.8,-40)` |
| **Notes** | Same scaling as coolant. Runs 10–30°F above ambient. Shared PGN with Coolant Temp. |

### Engine Hours

| | |
|---|---|
| **PGN** | 0xFEE5 (65253) — EHR |
| **SA** | 0x00 (Engine ECU) |
| **Byte(s)** | 0–3, little-endian 32-bit |
| **SPN** | 247 |
| **Scale** | `raw × 0.05 hours` |
| **DBC** | `0|32@1+ (0.05,0)` |
| **Notes** | Ground truth: 35,512.9 h (live capture). Matches dash hourmeter. |

### Odometer

| | |
|---|---|
| **PGN** | 0xFEC1 (65217) — HRVD |
| **SA** | 0x00 (Engine ECU) |
| **Byte(s)** | 0–3, little-endian 32-bit |
| **SPN** | 245 |
| **Scale** | `(raw × 5) ÷ 1609.344 miles` |
| **DBC** | `0|32@1+ (0.003106855961,0)` |
| **Notes** | Raw units are meters at 5 m/bit. **Scale precision is critical** — rounding to 6 decimal places (0.003107) introduces a 53-mile error at 1.15M miles. Use full-precision `5/1609.344 = 0.003106855961...`. Ground truth: 1,151,296 mi (verified 2026-08-23). BCM mirrors on SA 0x31 at ~0.2 mi offset. |

### Boost Pressure

| | |
|---|---|
| **PGN** | 0xFEF6 (65270) — IC1 |
| **SA** | 0x00 (Engine ECU) |
| **Byte(s)** | 2 |
| **SPN** | 102 |
| **Scale** | `raw × 0.145038 psi (absolute)` |
| **DBC** | `8|8@1+ (0.145038,0)` |
| **Notes** | Raw is kPa absolute at 1 kPa/bit. Gauge pressure = raw − 101 kPa (≈14.7 psi). Idle ~80 kPa abs (vacuum at gauge). Rises under load. |

### Battery / System Voltage

| | |
|---|---|
| **PGN** | 0xFEF7 (65271) — Electrical System Voltage |
| **SA** | 0x00 (Engine ECU) |
| **Byte(s)** | 4–5, little-endian 16-bit |
| **SPN** | 158 |
| **Scale** | `raw × 0.05 V` |
| **DBC** | `32|16@1+ (0.05,0)` |
| **Notes** | Engine ECU broadcasts voltage at SA 0x00 — this is standard per SAE and works on most platforms. BCM mirrors at SA 0x31 with nearly identical values. Verified via engine-off test: drops from ~14.2 V running to ~12.5 V off — confirms this is a live measurement, not a static config. |

---

## Confirmed Signals — BCM (SA 0x31)

*These signals are broadcast by the PACCAR/Kenworth Body Control Module. The PGNs and SPN scalings are SAE-standard, but the **source address 0x31 is PACCAR-specific**. Other OEMs may broadcast the same PGNs from different SAs (0x23, 0x17, or 0x0B).*

### Oil Pressure ⚠️ Cat C15 Specific

| | |
|---|---|
| **PGN** | 0xFEEF (65263) — EFL/P2 |
| **SA** | 0x00 (Engine ECU) |
| **Byte(s)** | 3 |
| **SPN** | 100 |
| **Scale** | `raw × 4 × 0.145038 psi` |
| **DBC** | `24|8@1+ (0.580152,0)` |
| **Notes** | **⚠️ Cat C15 exception:** Standard SPN 100 is byte 3 (0-indexed). On this Cat C15, byte 3 reads 0xFF (not populated). Cat places oil pressure at **byte 4** — the `kenworth_custom.dbc` / `j1939-paccar.dbc` uses byte 4 with the same scaling. Byte 4 is proprietary Cat behavior and may not work on other engine makes. Verified: 35.4 psi at idle (0x3D), 59.8 psi at 964 RPM (0x67), 66.7 psi at 1258 RPM (0x73). |

**⚠️ Correction history:** Oil pressure was initially misidentified at 0xFEE4 byte 2 (stuck at 0x3F = 36.5 psi regardless of RPM). The engine-off/engine-running test exposed it as a static config value, not a live signal.

### Primary Air Pressure

| | |
|---|---|
| **PGN** | 0xFEAE (65198) — Air Pressure |
| **SA** | 0x31 (BCM) |
| **Byte(s)** | 2 |
| **SPN** | 47 |
| **Scale** | `raw × 8 × 0.145038 psi` |
| **DBC** | `16|8@1+ (1.160304,0)` |
| **Notes** | Raw is kPa at 8 kPa/bit. Primary = rear air tank. ~126 psi with compressor running (0x6D). Matched dash air gauge primary needle. |

### Secondary Air Pressure

| | |
|---|---|
| **PGN** | 0xFEAE (65198) — Air Pressure |
| **SA** | 0x31 (BCM) |
| **Byte(s)** | 3 |
| **SPN** | 48 |
| **Scale** | `raw × 8 × 0.145038 psi` |
| **DBC** | `24|8@1+ (1.160304,0)` |
| **Notes** | Same PGN and scaling as primary. Secondary = front air tank. Shared PGN with Primary Air. Matched dash air gauge secondary needle. |

### Fuel Tank 1 (Left)

| | |
|---|---|
| **PGN** | 0xFEFC (65276) — Fuel Tanks |
| **SA** | 0x31 (BCM) |
| **Byte(s)** | 1 |
| **SPN** | 96 |
| **Scale** | `raw × 0.4 %` |
| **DBC** | `8|8@1+ (0.4,0)` |
| **Notes** | 0–100%. 130 US gallon saddle tank. Gallons = percent × 1.3. Tanks share equalizing crossover — levels balance over time. |

### Fuel Tank 2 (Right)

| | |
|---|---|
| **PGN** | 0xFEFC (65276) — Fuel Tanks |
| **SA** | 0x31 (BCM) |
| **Byte(s)** | 6 |
| **SPN** | 38 |
| **Scale** | `raw × 0.4 %` |
| **DBC** | `48|8@1+ (0.4,0)` |
| **Notes** | Same scaling and tank size as Tank 1. Equalizing crossover line — tank 2 level change without driving is fuel equalization between tanks, not consumption. |

### Ambient Air Temperature

| | |
|---|---|
| **PGN** | 0xFEF5 (65269) — Ambient Conditions |
| **SA** | 0x31 (BCM) |
| **Byte(s)** | 3–4, little-endian 16-bit |
| **SPN** | 171 |
| **Scale** | `(raw × 0.03125 − 273) × 9/5 + 32 °F` |
| **DBC** | `24|16@1+ (0.05625,-459.4)` |
| **Notes** | Standard SPN 171 at 0.03125°C/bit, −273°C offset. Verified 2026-08-23: live decode 63°F matching dash 64°F (within 1°F gauge tolerance). Shares PGN 0xFEF5 — bytes 0–2 are FF (not populated — barometric pressure absent on this truck). |

### BCM Reference Voltage

| | |
|---|---|
| **PGN** | 0xFEF8 (65272) — Battery Voltage |
| **SA** | 0x31 (BCM) |
| **Byte(s)** | 5 |
| **Scale** | `raw × 0.05 V` |
| **Notes** | 12.55 V steady — BCM internal reference. NOT the dash voltmeter (which is 0xFEF7). Does not change with engine state. Lower resolution than dash gauge (single byte at 0.05 V/bit). |

---

## Request-Only PGNs

*Some PGNs are not broadcast spontaneously and must be explicitly requested by sending a J1939 PGN request to CAN ID 0x18EAFFF9 (PGN 0x00EA00, global destination 0xFF) with a 3-byte little-endian target PGN as the data payload.*

### VIN — Not Available on This Vehicle

| | |
|---|---|
| **Request PGN** | 0x00FEEC (65260) |
| **Request Data** | `EC FE 00` |
| **SPN** | 237 |
| **Response** | None / NACK |
| **Notes** | The standard J1939 VIN request was attempted with multiple source addresses (0xF9, 0x20) and both global and targeted addressing. The engine ECU either NACKs (PGN not supported) or remains silent. Multi-frame Transport Protocol (TP.CM on 0xEC00, TP.DT on 0xEB00) was monitored — no response. The Cat C15 on this Kenworth likely requires Cat ET proprietary protocol for VIN retrieval. |

### Barometric Pressure — Not Populated

| | |
|---|---|
| **Request PGN** | 0x00FEF5 (65269) |
| **Request Data** | `F5 FE 00` |
| **SPN** | 108 |
| **Response** | None |
| **Notes** | SPN 108 (0.5 kPa/bit) at byte 0 of PGN 0xFEF5 is 0xFF on both engine ECU and BCM. Sensor not installed on this vehicle. |

---

## Unconfirmed PGNs

*Seen on the J1939 bus but not yet verified against a physical gauge, known value, or dash readout.*

### EEC3 — Desired Engine Speed & Friction Torque

| | |
|---|---|
| **PGN** | 0xFEDF (65247) |
| **SA** | 0x00 (Engine ECU) |
| **Byte(s)** | 2–3 (Desired Speed, LE16), 1 (NomFrictionTorque) |
| **Scale** | `raw × 0.125 rpm` / `(raw − 125) %` |
| **Notes** | Supplemental engine controller data. Not independently verified. |

### FEBD — Unknown 96%

| | |
|---|---|
| **PGN** | 0xFEBD (65213) |
| **SA** | 0x00 (Engine ECU) |
| **Byte(s)** | 2 |
| **Scale** | `raw × 0.4 %` |
| **Notes** | Sits at 0xF0–0xF1 (96.0–96.4%) in all captures. Purpose unknown — this truck has no aftertreatment, so this may be a vestigial broadcast or a configuration constant. |

### BCM Data 1 — Slow-Changing Value + Constant

| | |
|---|---|
| **PGN** | 0xFEF5 (65269) |
| **SA** | 0x31 (BCM) |
| **Byte(s)** | 3 (slow value), 5 (constant 0x26 = 38) |
| **Notes** | Byte 3 drifts between captures (29→10→18.5 at 0.5 scale tentative). Byte 5 is always 0x26. Not amperage. Purpose unknown. Shares PGN with Ambient Temperature (bytes 4–5). |

### BCM Counter — Short Duration

| | |
|---|---|
| **PGN** | 0xFEF7 (65271) |
| **SA** | 0x31 (BCM) |
| **Byte(s)** | 5–6, little-endian 16-bit |
| **Notes** | 279 raw units in one log, 283–290 in another. Possibly trip hours, idle timer, or PTO counter. Not engine hours. |

### BCM Hi-Res Air — Duplicate Air Pressure

| | |
|---|---|
| **PGN** | 0xFEFA (65274) |
| **SA** | 0x31 (BCM) |
| **Byte(s)** | 2–3, little-endian 16-bit |
| **Scale** | `raw × 4 × 0.145038 psi` |
| **Notes** | Same data as 0xFEAE but at 4 kPa/bit (double resolution). Redundant broadcast — use 0xFEAE unless higher precision is needed. |

### Trip Data — 18-Byte Burst

| | |
|---|---|
| **PGN** | 0xFECA (65226) |
| **SA** | 0x31 (BCM) |
| **Byte(s)** | 1–18 (structured container) |
| **Notes** | Contains 4 repeating entries with `03 7E` prefix. Likely encodes trip fuel, trip distance, trip hours. Ground truth trip odometer at time of testing: 6,019.9 mi — believed to be encoded in this frame but the scale has not been confirmed. |

### EBC1 — Brake Pedal Switch

| | |
|---|---|
| **PGN** | 0xF001 (61441) |
| **SA** | 0x0B (Brake ECU), also mirrored by 0x31 (BCM) |
| **Byte(s)** | 0, bits 0–1 |
| **Scale** | Enum (0 = off, 1 = applied) |
| **Notes** | Not verified against brake lights or pedal feel. |

### EBC2 — Engine Brake Status

| | |
|---|---|
| **PGN** | 0xF000 (61440) |
| **SA** | 0x0F (Retarder / Engine Brake) |
| **Byte(s)** | 0, bits 0–3 |
| **Scale** | Enum |
| **Notes** | Jake brake / engine compression brake status. Not verified. |

### Wheel Speed Sensors

| | |
|---|---|
| **PGN** | 0xFEBF (65215) |
| **SA** | 0x0B (Brake ECU) |
| **Byte(s)** | 2–5 (4 channels, 1 byte each) |
| **Scale** | `(raw − 125)` — raw offset, not scaled to mph |
| **Notes** | All channels read 0x7D (125 = zero offset) at standstill. Axle 1 L/R and Axle 2 L/R. Scaling factor needed to convert to mph. |

### VEP1 / VEP3 — Static Configuration Messages

| | |
|---|---|
| **PGN** | 0xFEE1 (65249) / 0xFEE3 (65251) |
| **SA** | 0x0F / 0x00 |
| **Notes** | 19-byte and 32-byte static configuration containers. Constant across all captures. No dynamic data — safe to ignore for monitoring. |

### 0xFEE4 Byte 2 — Stuck Sensor (Not Oil Pressure)

| | |
|---|---|
| **PGN** | 0xFEE4 (65252) — EFL/P1 |
| **SA** | 0x00 (Engine ECU) |
| **Byte(s)** | 2 |
| **Scale** | `raw × 4 × 0.145038 psi` |
| **Notes** | **⚠️ Previously misidentified as Oil Pressure.** Stuck at 0x3F (36.5 psi) regardless of engine RPM — does not change at idle, high idle, or under load. Real oil pressure is at 0xFEEF byte 4. Likely a configuration constant or unpopulated sensor. |

---

## Generic vs PACCAR-Specific Signals

### Truly Universal — Works on Any J1939 Diesel

These engine ECU (SA 0x00) signals are defined by SAE J1939-71 and broadcast identically on every J1939-compliant diesel engine regardless of make:

| Signal | PGN | SPN | Scale |
|--------|-----|-----|-------|
| Engine Speed | 0xF004 | 190 | 0.125 rpm/bit |
| Driver Demand Torque | 0xF004 | 512 | 1%/bit, −125 offset |
| Actual Engine Torque | 0xF004 | 513 | 1%/bit, −125 offset |
| Engine Load | 0xF003 | 92 | 1%/bit |
| Vehicle Speed | 0xFEF1 | 84 | 1/256 km/h/bit |
| Fuel Rate | 0xFEF2 | 183 | 0.05 L/h/bit |
| Coolant Temperature | 0xFEEE | 110 | 1°C/bit, −40°C offset |
| Fuel Temperature | 0xFEEE | 174 | 1°C/bit, −40°C offset |
| Engine Hours | 0xFEE5 | 247 | 0.05 h/bit |
| Odometer | 0xFEC1 | 245 | 5 m/bit |
| Boost Pressure | 0xFEF6 | 102 | 1 kPa/bit |
| Battery Voltage | 0xFEF7 | 158 | 0.05 V/bit |

The `j1939-generic.dbc` file in this repository contains only these universal signals and will work on any J1939 diesel truck.

### PACCAR/Kenworth-Specific (SA 0x31 BCM)

These signals use SAE-standard PGNs and SPN scalings but the specific **source address 0x31** is PACCAR's BCM implementation. Other OEMs may broadcast the same data from different SAs:

| Signal | PGN | SA 0x31 | Other Possible SA |
|--------|-----|---------|-------------------|
| Primary Air Pressure | 0xFEAE | Kenworth BCM | 0x0B Brake Controller |
| Secondary Air Pressure | 0xFEAE | Kenworth BCM | 0x0B Brake Controller |
| Fuel Level 1 | 0xFEFC | Kenworth BCM | 0x23 Cab Controller, 0x17 Instrument Cluster |
| Fuel Level 2 | 0xFEFC | Kenworth BCM | 0x23 Cab Controller, 0x17 Instrument Cluster |
| Ambient Temperature | 0xFEF5 | Kenworth BCM | 0x17 Instrument Cluster, 0x23 Cab Controller |
| Dash Voltage | 0xFEF7 | Kenworth BCM | 0x00 Engine ECU (same PGN, different SA) |

### Cat C15 Specific

- **Oil pressure** at 0xFEEF byte 4 instead of standard byte 3. The standard SPN 100 byte 3 reads 0xFF on this engine.

The `j1939-paccar.dbc` file in this repository contains everything — universal signals, PACCAR SA 0x31 signals, and the Cat-specific oil pressure byte position.

---

## Off-Bus Signals

*These signals run directly from the sensor to the BCM, then over the CVSG private gauge bus to the Kenworth Smart Gauges. **They do not appear on the J1939 diagnostic bus** and cannot be read through the 9-pin connector.*

| Signal | Path |
|--------|------|
| **Ammeter (Amps)** | Alternator current sensor → BCM → CVSG CAN → Dash |
| **Front Drive Axle Temperature** | Sensor → BCM → CVSG CAN → Dash |
| **Rear Drive Axle Temperature** | Sensor → BCM → CVSG CAN → Dash |
| **Suspension Pressure** | Sensor → BCM → CVSG CAN → Dash |
| **Air Filter Restriction** | Sensor → BCM → CVSG CAN → Dash (CVSG ID 0x20) |
| **Fuel Filter Restriction** | Not populated on this truck (no sensor) |

See `CVSG/CVSG.md` for the CVSG gauge bus protocol, addressing, and scaling for these signals.

---

## Validation Methodology

### Engine-Off Test

The most reliable way to distinguish a live signal from a static configuration value:

1. Connect and read the bus with the engine running.
2. Note the candidate signal's value.
3. Turn off the engine, leave the ignition on.
4. Re-read the bus.
5. If the value **changed** with engine state → live signal.
6. If the value **stayed the same** → static config or unpopulated sensor.

**Signals caught by this test:**

| PGN | Byte | Engine On | Engine Off | Verdict |
|-----|------|-----------|------------|---------|
| 0xFEF0 | 5 | 14.0 V | 14.0 V | ✗ STATIC — not dash voltmeter |
| 0xFEE4 | 1 | 36.5 psi | 36.5 psi | ✗ STATIC — not oil pressure |
| 0xFEF7 | 4–5 (LE16) | 14.2 V | 12.5 V | ✓ LIVE — dash voltmeter confirmed |
| 0xFEEF | 3 | 35–67 psi | 0 psi | ✓ LIVE — oil pressure confirmed |

### Scale Precision

Scales that combine multiple conversion factors need sufficient decimal precision, especially for large raw values:

- **Odometer**: `5 / 1609.344 = 0.003106855961...` — rounding to 0.003107 introduces a 53-mile error at 370 million raw units (1.15M miles). Always use full precision.
- **DBC**: Never use fraction notation (`1/8`, `1/256`) — many parsers silently treat these as `1`. Always use decimal values (`0.125`, `0.00390625`).

### DBC Bit Numbering

The `canparse` crate used by the j1939logger tool expects **@1 (Motorola)** bit numbering. Using @0 (Intel) produces correct values in Vector tools but garbled values in the logger. Always use `@1+` or `@1-` for signal definitions.

### DBC CAN ID Source Addresses

The j1939logger matches messages by masking priority bits (`id & 0x3FFFFFF`) but matches **SA exactly**. Template-style CAN IDs with SA=0xFE (wildcard placeholder) will never match real messages. Always use the actual SA from the bus: `0x00` for engine ECU, `0x31` for BCM.

---

## Repository File Map

| File | Description |
|------|-------------|
| `dbc-files/j1939-generic.dbc` | Universal J1939 signals — engine ECU SA 0x00 only. Works on any diesel truck. |
| `dbc-files/Paccar-Kenworth-Peterbilt/j1939-paccar.dbc` | Full PACCAR DBC — all universal + BCM SA 0x31 + Cat C15 oil pressure. |
| `J1939-CAN/J1939-broadcast.md` | This document. |
| `CVSG/CVSG.md` | CVSG auxiliary gauge bus protocol and signal map. |

---

## Current Confidence Summary

Confirmed with live dash-gauge verification and engine-off tests:

- All 12 universal engine ECU signals — RPM, torque, load, speed, fuel rate, temperatures, hours, odometer, boost, voltage.
- All 5 BCM body signals — primary/secondary air, fuel tanks 1/2, ambient temperature.
- Oil pressure at 0xFEEF byte 4 (Cat C15 specific).
- Two static config values identified and excluded: 0xFEF0 (charging system config) and 0xFEE4 byte 2.
- Odometer scale precision confirmed at 5/1609.344 with 12+ decimal places.
- DBC bit numbering convention (@1 Motorola) and decimal-scale requirement confirmed for j1939logger compatibility.

Still unresolved or requiring physical cross-checking:

- Trip data PGN 0xFECA — structure identified but individual field scaling not confirmed.
- BCM Data 1 (0xFEF5 bytes 3 and 5) — purpose unknown.
- BCM Counter (0xFEF7 bytes 5–6) — likely trip timer, unconfirmed.
- VIN retrieval — standard J1939 request NACKed; likely requires Cat ET.
- Barometric pressure — sensor not installed on this vehicle.
- FEBD 96% signal — purpose unknown.
- Wheel speed sensor scaling to mph.

---

*Vehicle: 2015 Kenworth T660, Caterpillar C15 (no aftertreatment), J1939 250k baud, 9-pin diagnostic.*  
*Fuel tanks: 130 US gallons each, equalizing crossover line.*  
*Capture hardware: Noregon DLA+ 2.0 RP1210 adapter (DeviceID=100).*  
*Last verified: 2026-08-23 — live captures with engine running.*
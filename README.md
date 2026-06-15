# Vive Room Tracker

A Python toolkit for capturing HTC Vive tracker positions in a room-relative
coordinate frame, with session recording and camera-readable timestamps for
multimodal experimental setups.

Designed for research use cases that need stable, repeatable position data
across sessions — wrist tracking for behavioral capture, gesture analysis,
training data collection for robotics research.

The system has three main capabilities:

- **Coordinate calibration** — convert raw SteamVR coordinates into a
  room-relative frame anchored at chosen physical reference points
- **Session recording** — log timestamped tracker positions to CSV files
  during experiments
- **Timestamp display + event logging** — show a camera-readable clock and
  let researchers add timestamped annotations during recording

This is research code, not a polished product. Expect to read the source.

---

## Prerequisites

**Hardware:**
- 2× HTC Vive Lighthouse base stations (V2 recommended)
- 1+ Vive trackers (3.0 or compatible) with USB dongles
- A computer that meets SteamVR's minimum requirements

A head-mounted display is **not** required — the system runs with SteamVR's
null-headset driver enabled.

**Software:**
- Windows 11
- Steam + SteamVR installed
- Python 3.10 or newer
- Python packages:
  ```
  openvr
  numpy
  matplotlib
  ```

Install dependencies:

```cmd
pip install openvr numpy matplotlib
```

---

## First-Time Setup

These steps are done once per physical installation. After that, only the
daily usage commands are needed.

### 1. Enable null headset and fake controllers in SteamVR

SteamVR refuses to start fully without an HMD and controllers, even for
tracker-only use. Edit your `steamvr.vrsettings` file (typically at
`C:\Users\<you>\AppData\Local\openvr\steamvr.vrsettings`) and add a
`driver_null` block plus a few flags inside the `steamvr` block:

```json
{
   "driver_null" : {
      "enable" : true,
      "numberOfFakeControllers" : 2
   },
   "steamvr" : {
      "requireHmd" : false,
      "forcedDriver" : "null",
      "activateMultipleDrivers" : true
   }
}
```

Fully quit SteamVR (system tray → Exit) and restart it.

### 2. Complete SteamVR Room Setup once

In SteamVR, run Room Setup → Standing Only. Click through the prompts; the
fake controllers will satisfy SteamVR's setup checks. After this, SteamVR
stops nagging you on subsequent launches.

### 3. Pair your trackers

For each tracker, plug in its dongle, then in SteamVR go to Devices → Pair
Controller → "I want to pair a different type of controller" → Vive Tracker.
Power on one tracker at a time during pairing.

### 4. Identify which tracker is left vs. right

Run:

```cmd
python main.py identify
```

The wizard powers on one tracker at a time and records its serial. At the
end it prints a copy-paste block:

```python
SERIAL_TO_LABEL = {
    "LHR-XXXXXXXX": "Left Wrist ",
    "LHR-YYYYYYYY": "Right Wrist",
}
```

Paste this into `tracker_output.py`, replacing the existing (empty)
`SERIAL_TO_LABEL` dict near the top of the file. This mapping is stable
across sessions because serial numbers are burned into the tracker hardware.

### 5. Place physical reference markers

Decide where your room's coordinate system will be anchored. For example:
- Origin (0, 0, 0) at the left corner of your test area
- +X pointing rightward along the back wall
- +Y pointing up (always — gravity-aligned)
- +Z pointing into the room

Place tape, floor decals, or other durable markers at the reference points
you'll use during calibration. Measure each marker's room coordinates with
a tape measure — you'll type these into the calibration wizard.

For SVD calibration you can use 3+ points anywhere in the volume. More
points (6–10) give better accuracy and let you spot bad measurements.

### 6. Run the initial calibration

Two methods are available. Default is Gram-Schmidt (faster, 3 points):

```cmd
python main.py calibrate
```

For SVD multi-point (recommended for fixed lab setups with multiple
reference points):

```cmd
python main.py calibrate --method svd
```

After calibration completes, the system is ready to use.

---

## Daily Usage

### Live terminal output

```cmd
python main.py print
```

Shows tracker positions in real time, updating in place. Press Ctrl+C to
stop. The system offers an optional drift check at startup (place a tracker
on the reference point and press Enter, or press Ctrl+C to skip).

Adjust the frequency with `-f`:

```cmd
python main.py print -f 30
```

Skip the startup drift check:

```cmd
python main.py print --skip-relocalize
```

### Live 3D visualization

```cmd
python main.py view
```

Opens a matplotlib window with tracker positions, motion trails, and a room
wireframe. Close the window or press Ctrl+C to stop.

### Record a session

Add `--log` plus a session name to either `print` or `view`:

```cmd
python main.py print --log reaching_trial_01
python main.py view --log gesture_demo
```

The session is written to a CSV file in the `sessions/` folder. You can
combine `--log` with other flags:

```cmd
python main.py print --log reaching_trial_01 -f 30 --skip-relocalize
```

### Check calibration drift without changes

```cmd
python main.py verify
```

Samples the reference point and reports drift in centimeters. Does not
modify the calibration. Use this when you want to diagnose drift without
committing to an automatic correction.

### Automatically correct small drift

```cmd
python main.py relocalize
```

Samples the reference point and:
- Drift under 2 cm → does nothing
- Drift 2–10 cm → automatically corrects translation
- Drift over 10 cm → refuses to auto-correct, asks for full recalibration

### Recalibrate from scratch

```cmd
python main.py calibrate                    # Gram-Schmidt (default, 3 points)
python main.py calibrate --method svd       # SVD multi-point
```

Overwrites the existing calibration. Run this if `relocalize` reports drift
too large to auto-correct, or if you physically moved a base station.

---

## Camera Synchronization with Timestamp Display

For multimodal setups where a video camera records the participant alongside
the tracker stream, the timestamp display gives the camera a readable clock
plus an interface for annotating events during recording.

### Running the timestamp display

```cmd
python timestamp_display.py
```

This is a **standalone script** — it doesn't share state with `main.py` and
runs in its own terminal/window.

A black window opens with:
- A large wall-clock at millisecond precision (top, biggest)
- A stopwatch display with Start/Stop/Reset/Lap buttons (middle)
- A recent-laps panel showing the last 5 laps
- Session controls (text field + Start/End Session button)
- A notes text field for typed annotations

### Workflow during an experiment

The two scripts run in two separate terminals:

```cmd
:: Terminal 1: timestamp display
python timestamp_display.py
```

```cmd
:: Terminal 2: tracker recording
python main.py print --log trial_01
```

In the timestamp window:

1. Type the session name (e.g. `trial_01`) into the Session field
   *Pressing Enter just commits the text — it does NOT start the session*
2. Click "Start Session" to begin recording events
3. The camera now has a readable clock to record
4. During the experiment, type notes into the Notes field and press Enter
   to log them with a timestamp
5. Use the stopwatch as needed (independent of the session — see below)
6. Click "End Session" or close the window to finalize

### Coordinating session names

The two scripts don't communicate with each other. You're responsible for
typing the same session name into both:

- `python main.py print --log trial_01` (Terminal 2)
- "Session: trial_01" in the timestamp window (Terminal 1)

If you mistype one, the files won't share a name prefix, which makes
post-processing harder. Decide the session name before starting both.

### Stopwatch

The stopwatch is independent of the session — you can use it any time. It's
useful for measuring trial durations, reaction intervals, or any
time-bounded phase of the experiment.

Four operations:

- **Start** — begin (or resume) ticking. Stopwatch turns green.
- **Stop** — pause ticking. Stopwatch turns amber, time stays frozen.
- **Reset** — return to 00:00:00.000 and clear lap history.
- **Lap** — record the current value as a numbered lap. Only works while
  the stopwatch is running.

When a session is active, all stopwatch actions are logged to the events
CSV. When no session is active, the stopwatch still works but nothing is
written to disk.

### Post-processing the camera/tracker sync

After the experiment you'll have:

- A video file from the camera (with the on-screen clock visible)
- Tracker CSV (`sessions/2026-06-08_143052_trial_01.csv`)
- Events CSV (`sessions/2026-06-08_143052_trial_01_events.csv`)

To align video time with tracker time:

1. Find a video frame where the on-screen timestamp is clearly readable
2. Read the displayed time (e.g. `14:30:52.847`)
3. That moment in the video corresponds to a specific nanosecond timestamp
   in both CSV files
4. Use this anchor to align the video timeline with the tracker timeline

---

## Files Produced

### Persistent files (kept between sessions)

| File | Format | Purpose |
|---|---|---|
| `room_calibration.json` | JSON | The active calibration. Loaded at startup of all modes except `identify` and `calibrate`. |
| `room_calibration.audit.json` | JSON | Audit log showing every sampled point, tracker serials, and per-point residuals (SVD only). |

### Session recordings (only when `--log` or the timestamp display is used)

| File | Format | Purpose |
|---|---|---|
| `sessions/YYYY-MM-DD_HHMMSS_<name>.csv` | CSV | Timestamped tracker positions in room coordinates. Long format: one row per tracker per frame. |
| `sessions/YYYY-MM-DD_HHMMSS_<name>.meta.json` | JSON | Sidecar metadata: calibration used, tracker labels, frequency, duration. |
| `sessions/YYYY-MM-DD_HHMMSS_<name>_events.csv` | CSV | Event log: session start/end, notes, stopwatch laps. |

**Format choice rationale:** JSON for small structured data (configs,
metadata); CSV for long uniform streams (time-series positions, event
log). This split matches what downstream analysis tools (pandas, R,
MATLAB) expect.

**File encoding:** All CSVs are UTF-8 with a byte-order mark, so Excel and
other tools auto-detect the encoding correctly.

### Sample tracker CSV

```csv
timestamp_ns,tracker_label,x,y,z,valid
1716843213847291000,Left Wrist ,1.234000,0.987000,2.105000,1
1716843213847291000,Right Wrist,1.882000,0.965000,2.099000,1
1716843213864123000,Left Wrist ,1.235000,0.987000,2.106000,1
```

Positions are in meters. The `valid` flag is 1 if the tracker had a valid
pose at that frame, 0 otherwise. Invalid frames write NaN coordinates
rather than being skipped, so timing remains continuous.

### Sample events CSV

```csv
timestamp_ns,event_type,text
1716843213847291000,session_start,Session 'trial_01' started
1716843225100000000,note,participant ready
1716843230123456000,note,(stopwatch started at 00:00:00.000)
1716843252868000000,lap,Lap 1 - stopwatch 00:00:22.745
1716843253373050000,lap,Lap 2 - stopwatch 00:00:23.250
1716843267000000000,note,trial 1 complete
1716843298111222000,session_end,Session ended
```

Four event types:
- `session_start` — written when the session begins
- `session_end` — written when the session ends
- `note` — typed by the researcher during the experiment (also used for
  stopwatch start/stop/reset events for context)
- `lap` — stopwatch lap (only when stopwatch is running)

---

## Calibration Methods

Both methods produce the same `RoomCalibration` object format, so all other
modes work identically regardless of which one was used.

### Gram-Schmidt (default)

**How it works:** Sample three points A, B, C and declare their room
coordinates. The code computes +X from A→B, makes +Z perpendicular to +X
using B→C, and derives +Y from the cross product.

**Pros:**
- Fast (3 samples, ~30 seconds total)
- Simple math, easy to inspect
- Works well when reference points can be placed cleanly

**Cons:**
- Cannot detect or correct bad measurements — every point is load-bearing
- Privileges X over Z (X taken directly; Z forced perpendicular)
- Limited to exactly 3 points

**Typical accuracy:** 1–3 cm in normal conditions.

**When to use:** First calibration, simple fixed setups, when speed matters.

### SVD multi-point (Kabsch algorithm)

**How it works:** Sample N points (N ≥ 3) at arbitrary positions and declare
their room coordinates. The algorithm finds the best-fit rotation and
translation that maps SteamVR coordinates to room coordinates across all
points simultaneously.

**Pros:**
- Handles 3 to many points
- Reports per-point residuals so bad measurements can be spotted
- More robust to noise (more points → better averaging)
- All axes treated equally (no privileged direction)
- Points can be placed anywhere — no requirement on distance or alignment

**Cons:**
- Slower (each point needs a sample + typed coordinates)
- More user effort during calibration

**Typical accuracy:** 0.5–2 cm with 6+ well-placed points.

**When to use:** Fixed lab setups with multiple QR-coded or marked
locations, when you need redundancy against bad measurements, when 1m
fixed spacing isn't practical.

### Choosing between them

Run both on the same physical setup and compare the audit logs. If
Gram-Schmidt produces accuracy that matches SVD, the simpler method is
fine. If SVD's residuals reveal a measurement issue, that's evidence to
either re-sample points or stay with SVD's robustness.

---

## Drift Handling

SteamVR's internal coordinate frame can shift slightly between sessions due
to re-localization events. The system handles this with tiered drift
detection:

| Drift magnitude | What happens |
|---|---|
| Under 2 cm | Calibration considered accurate; no action taken |
| 2–10 cm | Auto-correct translation, then re-verify |
| Over 10 cm | Refuse to auto-correct; prompt for full recalibration |

Drift is measured by re-sampling the reference point and comparing the
computed room coordinates to the expected ones.

The check runs automatically at the start of `print` and `view` modes. You
can also trigger it manually with `python main.py relocalize`.

When base stations are bumped or moved, drift typically exceeds 10 cm
because the axes have rotated (not just translated). In that case full
recalibration is required.

---

## Project Structure

```
.
├── main.py                       ← CLI entry point and mode dispatch
├── room_calibration.py           ← Calibration math (Gram-Schmidt, SVD,
│                                    verify, relocalize)
├── tracker_output.py             ← Tracker discovery, room coordinate
│                                    conversion, terminal and 3D output,
│                                    identify wizard
├── session_logger.py             ← CSV session recording for tracker data
├── timestamp_display.py          ← Standalone window: clock, stopwatch,
│                                    session controls, event logging
├── room_calibration.json         ← Active calibration (generated)
├── room_calibration.audit.json   ← Calibration audit log (generated)
└── sessions/                     ← Recorded sessions (generated)
    ├── 2026-06-08_143052_trial_01.csv
    ├── 2026-06-08_143052_trial_01.meta.json
    └── 2026-06-08_143052_trial_01_events.csv
```

The code is split so each file has one responsibility:

- `room_calibration.py` — math, knows nothing about output formats
- `tracker_output.py` — tracker discovery and display, knows nothing about
  calibration math
- `session_logger.py` — file I/O for tracker streams
- `timestamp_display.py` — completely standalone UI for camera sync
- `main.py` — the only file that wires the others together

This makes individual pieces easy to modify or replace.

---

## Intentional Scope Limits

A few things this project deliberately does **not** do:

- **No real-time drift monitoring during sessions.** Drift checks happen
  only at startup or on manual trigger. Continuous monitoring would
  require keeping one tracker permanently stationary on a reference point.

- **No multi-location calibration storage.** One calibration file per
  installation. If you regularly move between rooms, you'd need to
  recalibrate each time.

- **No multimodal sensor fusion.** This project produces tracker position
  data and timestamps. Combining it with gaze, video, or speech is the
  responsibility of downstream analysis pipelines that consume the
  recorded CSV files.

- **No tracker orientation output.** Only position is reported.

- **No automatic feature extraction.** Wrist speed, hand involvement
  ratio, and similar derived quantities are computed downstream from the
  recorded data. This project produces the raw positions.

- **No automatic coordination between the tracker and timestamp scripts.**
  Session names are typed in both windows manually. This is intentional
  for simplicity; can be added if it becomes annoying in practice.

- **No verify-at-arbitrary-point yet.** Currently `verify` uses the
  reference point stored in the calibration file. A planned feature is
  to allow verification at any calibrated point (relevant for SVD
  calibrations with multiple QR-coded reference locations).

If you need any of these, they would be additions on top of the current
code, not modifications to it.

---

## Known Caveats

- **SteamVR origin drift is empirically variable.** Different SteamVR
  versions and different hardware configurations produce different drift
  magnitudes across power cycles. Test your specific setup with the
  verify mode to understand its behavior.

- **The system uses one tracker at a time during calibration.** Whichever
  tracker is at the calibration point gets sampled. The labels
  (Left/Right) only matter during session recording, not during
  calibration.

- **Trackers must be facing base stations during sampling.** The
  photodiodes are directional. If a tracker is upside-down or its sensor
  surface is blocked, sampling will fail with "no valid tracker found".

- **Recalibration is required after physical changes to the setup.**
  Moving a base station, removing one, or running SteamVR Room Setup
  again invalidates the calibration.

- **CSV files can grow large.** A 1-hour tracker session at 60 Hz with 2
  trackers produces ~25 MB. Sessions are not auto-rotated or compressed.

- **The timestamp display window has been tested with simulated input.**
  Interactive widgets (buttons, text fields) couldn't be tested without
  a live display in the development environment. A 5-minute manual test
  is recommended before relying on it for an experiment.

- **Stopwatch glyph compatibility.** Earlier versions used the ⏱ emoji
  in the stopwatch display, which renders as a square box on systems
  without emoji fonts. This is now replaced with the plain text "SW"
  label, which works everywhere.

---

## Recommended `.gitignore`

If you push this to a repository, exclude generated files:

```
room_calibration.json
room_calibration.audit.json
sessions/
__pycache__/
*.pyc
```

Source files belong in the repo. Calibration data is tied to a specific
physical setup and isn't meaningful on other machines. Session recordings
are data, not code.

---

## Troubleshooting

**"No valid tracker found during sampling"**
The tracker is connected to SteamVR but doesn't have a valid pose. Check
the SteamVR status window — the tracker icon should be solid green. If
it's gray, move the tracker into the base station's view. If it's missing
entirely, the tracker isn't paired or its dongle isn't connected.

**"OpenVR initialization failed: HmdNotFound"**
The null headset driver isn't enabled. See first-time setup step 1.

**SteamVR keeps prompting "Complete Room Setup"**
Run Room Setup once (with fake controllers enabled) and finish all steps.
After that the prompts stop. See first-time setup step 2.

**Calibration produces high residuals (>5 cm)**
Common causes: tape marks weren't measured accurately; tracker drifted
during the sampling window; reflective surfaces in the room are confusing
the base stations; one calibration point was occluded during sampling.
Re-run calibration with the tracker held more carefully, or use the SVD
method with more points for redundancy.

**Distances along an axis appear wrong (e.g. 20 cm shorter than tape)**
Likely cause: declared room coordinates during calibration don't match
the actual physical distance between tape marks. Check
`room_calibration.audit.json` — compare `sampled_distance_AB_m` against
the declared room distance. If they disagree by the observed error
magnitude, recalibrate with accurate declared coordinates.

**Trackers report positions but they look wrong**
Probably means the calibration is stale. Run `python main.py verify` to
check drift. If verify reports large drift, run `python main.py relocalize`
or recalibrate from scratch.

**CSV file shows `�` or `?` characters in some cells**
Encoding mismatch. The current code writes UTF-8 with a byte-order mark
to avoid this. If you have older files affected by this, either re-record
the session or open the file in a tool that handles UTF-8 (Notepad++,
VS Code, pandas).

**Square boxes (tofu) in the timestamp display window**
Your monospace font is missing certain glyphs. The current code avoids
problematic emoji; if you still see boxes, the source character can be
replaced with plain ASCII. Tell us which display element has the issue.

**Timestamp display window opens but is cropped or controls overlap**
Resize the window manually — matplotlib's layout adapts to the new size.
Default dimensions are 14×11 inches; adjust the `width_in` and `height_in`
arguments in `TimestampDisplay()` if you need a different default.

---

## License

Copyright © `06/15/2026` `Kevin Liu and fellow HAT Lab members`

Permission is hereby granted, free of charge, to any person
obtaining a copy of this software and associated documentation
files (the “Software”), to deal in the Software without
restriction, including without limitation the rights to use,
copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following
conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
OTHER DEALINGS IN THE SOFTWARE.

# ─────────────────────────────────────────────────────────────────
# room_calibration.py   (Phase 2 — adds automatic re-localization)
#
# PURPOSE:
#   Define a custom room coordinate system, verify it stays accurate
#   across SteamVR sessions, and automatically correct small drift
#   without requiring a full recalibration.
#
# WHAT'S NEW IN PHASE 2:
#   - relocalize_translation(): fix origin-only drift from one sample
#   - smart_relocalize(): tiered drift handling
#       < 2 cm   → leave as is
#       2–10 cm  → auto-correct translation
#       > 10 cm  → require full recalibration
#   - sample_one_tracker(): standalone helper used by both calibrate
#                            and relocalize flows
#
# AXES:
#   +X = rightward along the station wall
#   +Y = upward (gravity-aligned, always reliable)
#   +Z = into the room (depth)
#
# USAGE:
#   from room_calibration import RoomCalibration, RoomCalibrator
#   from room_calibration import smart_relocalize, sample_one_tracker
# ─────────────────────────────────────────────────────────────────

import numpy as np      # math library for arrays and matrix operations
import json             # for saving/loading calibration as a text file
import os               # for checking if calibration file exists on disk
import time             # for sleep and timing during sampling
import openvr           # Python bindings for SteamVR's OpenVR SDK

# Name of the file where calibration data is saved between sessions
CALIB_FILE = "room_calibration.json"

# Drift thresholds for smart_relocalize().  In meters.
# Below DRIFT_OK_M       → no action needed
# Between OK and AUTO    → auto-correct translation
# Above DRIFT_AUTO_M     → require full recalibration
DRIFT_OK_M    = 0.02    # under 2 cm  →  good
DRIFT_AUTO_M  = 0.10    # 2–10 cm     →  auto-correct
                        # above 10 cm →  manual recalibrate required

# Threshold used internally by smart_relocalize() after auto-correct
# to confirm the correction succeeded.
DRIFT_POST_FIX_OK_M = 0.01  # 1 cm — re-verify should be at or below this


# ─── Helper: sample one tracker for N seconds ─────────────────────
# Standalone function used by RoomCalibrator (initial calibration)
# AND by smart_relocalize (drift correction).  Both need the same
# sampling behavior — averaging tracker position over a short window.

def sample_one_tracker(vr, duration=2.0, label=""):
    """
    Collect tracker positions over `duration` seconds and return
    their mean.  Averaging removes momentary noise from the reading.

    Returns:
      numpy array (3,) of SteamVR-space mean position in meters.

    Raises:
      RuntimeError if no valid tracker was found during the window.
    """
    samples = []                        # list of position arrays
    end     = time.time() + duration    # absolute end timestamp
    print(f"  Sampling '{label}' for {duration}s ...", end="", flush=True)

    while time.time() < end:
        # Ask SteamVR for poses of all possible device slots
        poses = vr.getDeviceToAbsoluteTrackingPose(
            openvr.TrackingUniverseStanding,
            0,
            openvr.k_unMaxTrackedDeviceCount
        )

        # Scan every device slot to find a tracker
        for i in range(openvr.k_unMaxTrackedDeviceCount):
            dc = vr.getTrackedDeviceClass(i)

            # Only care about generic trackers
            if dc != openvr.TrackedDeviceClass_GenericTracker:
                # Fallback: some firmware reports trackers with
                # different class IDs.  Check model name.
                try:
                    model = vr.getStringTrackedDeviceProperty(
                        i, openvr.Prop_ModelNumber_String)
                except:
                    model = ""
                if "tracker" not in model.lower():
                    continue

            pose = poses[i]
            if not pose.bPoseIsValid:
                continue    # skip if SteamVR lost this device

            # mDeviceToAbsoluteTracking is a 3x4 matrix.
            # The last column (index 3) is the position vector.
            m = pose.mDeviceToAbsoluteTracking
            samples.append(np.array([m[0][3], m[1][3], m[2][3]]))
            break           # found one tracker, stop scanning

        time.sleep(1 / 60)  # poll at ~60Hz to not busy-wait

    if not samples:
        # No tracker was visible at all during the sample window
        raise RuntimeError("No valid tracker found during sampling")

    mean = np.mean(samples, axis=0)
    print(f" done  (n={len(samples)})  "
          f"raw: x={mean[0]:+.4f} y={mean[1]:+.4f} z={mean[2]:+.4f}")
    return mean


# ─── RoomCalibration ─────────────────────────────────────────────
# Stores the computed room coordinate system, converts positions,
# verifies drift, and supports translation-only re-localization.

class RoomCalibration:

    def __init__(self, origin, x_axis, y_axis, z_axis,
                 reference_sv=None, reference_room=None):
        # origin: the SteamVR position of your room's (0,0,0) corner
        self.origin = np.array(origin)

        # 3x3 rotation matrix.  Columns are the room axes expressed
        # in SteamVR's coordinate space.  Orthonormal by construction.
        self.R = np.column_stack([x_axis, y_axis, z_axis])

        # Inverse rotation.  Orthonormal → inverse equals transpose.
        self.R_inv = self.R.T

        # Reference point used by verify() and relocalize_translation().
        # Stores BOTH the SteamVR coords (what we measured at calibration)
        # AND the room coords those should correspond to.  After drift
        # correction, reference_sv is updated; reference_room stays fixed.
        self.reference_sv   = (np.array(reference_sv)
                               if reference_sv   is not None else None)
        self.reference_room = (np.array(reference_room)
                               if reference_room is not None else None)

    def to_room(self, steamvr_pos):
        # Convert SteamVR position → room coordinates in meters.
        # Step 1: subtract origin → vector from room corner to tracker
        # Step 2: rotate by R_inv → express that vector in room axes
        return self.R_inv @ (np.array(steamvr_pos) - self.origin)

    def has_reference(self):
        # Tells callers whether this calibration has a stored
        # reference point that verify() and relocalize can use.
        return (self.reference_sv   is not None
                and self.reference_room is not None)

    def verify(self, current_sv_pos):
        """
        Compare a freshly sampled SteamVR position against the stored
        reference point.

        Returns a dict with:
          status   : "ok" | "warn" | "fail" | "no_reference"
          drift_m  : drift magnitude in meters (None if no_reference)
          expected : room coords the user marked as the reference
          measured : room coords computed from current_sv_pos
          delta    : per-axis difference (measured - expected)
        """
        if not self.has_reference():
            return {
                "status":   "no_reference",
                "drift_m":  None,
                "expected": None,
                "measured": None,
                "delta":    None,
            }

        # Convert the freshly sampled SteamVR position into room
        # coordinates using the CURRENT calibration.  If SteamVR's
        # internal origin has shifted since calibration, this differs
        # from the known room coords of the reference point.
        measured = self.to_room(current_sv_pos)
        expected = self.reference_room

        delta   = measured - expected
        drift_m = float(np.linalg.norm(delta))

        # Classify drift against the thresholds.  Note these are
        # only used for human messaging — smart_relocalize() uses
        # its own thresholds (DRIFT_OK_M, DRIFT_AUTO_M) below.
        if drift_m <= DRIFT_OK_M:
            status = "ok"
        elif drift_m <= DRIFT_AUTO_M:
            status = "warn"
        else:
            status = "fail"

        return {
            "status":   status,
            "drift_m":  drift_m,
            "expected": expected,
            "measured": measured,
            "delta":    delta,
        }

    def relocalize_translation(self, fresh_sv_pos_at_origin):
        """
        Translation-only drift correction.

        Given a freshly sampled SteamVR position taken AT the room
        origin (Point A), update calibration so that this sampled
        position becomes the new origin.  Axes are left unchanged.

        This corrects pure origin drift — the most common type after
        SteamVR restart, computer wake, or brief tracker disconnect.
        It does NOT correct rotation drift (base station bumped, axes
        rotated).  For that, a full recalibration is needed.

        Modifies self in place.  Caller should save() afterwards.
        """
        # The fresh sample IS now the SteamVR position of room (0,0,0).
        # Replace the stored origin with it.
        self.origin = np.array(fresh_sv_pos_at_origin)

        # Update the reference for future verifies so that a fresh
        # sample on Point A will read close to (0,0,0).
        self.reference_sv = np.array(fresh_sv_pos_at_origin)
        # reference_room remains (0,0,0) — Point A's room coords
        # don't change just because SteamVR's frame did.

    def save(self, path=CALIB_FILE):
        # Pack calibration into a plain dictionary of Python lists
        # (JSON cannot store numpy arrays directly).
        data = {
            "origin":  self.origin.tolist(),
            "x_axis":  self.R[:, 0].tolist(),
            "y_axis":  self.R[:, 1].tolist(),
            "z_axis":  self.R[:, 2].tolist(),
        }

        # Only persist reference fields when they are present.
        if self.has_reference():
            data["reference_sv"]   = self.reference_sv.tolist()
            data["reference_room"] = self.reference_room.tolist()

        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"✓ Room calibration saved to {path}")

    @classmethod
    def load(cls, path=CALIB_FILE):
        # Return None silently if file does not exist yet.
        if not os.path.exists(path):
            return None
        with open(path) as f:
            d = json.load(f)
        print(f"✓ Room calibration loaded from {path}")

        # Backward-compatible: old files won't have reference_* keys.
        # dict.get() returns None when the key is missing.
        return cls(
            origin=d["origin"],
            x_axis=d["x_axis"],
            y_axis=d["y_axis"],
            z_axis=d["z_axis"],
            reference_sv  =d.get("reference_sv"),
            reference_room=d.get("reference_room"),
        )


# ─── Smart Re-localization ────────────────────────────────────────
# Tiered automatic drift handling.  Used at startup of print/view
# modes and as the dedicated 'relocalize' mode.

def smart_relocalize(vr, calibration, sample_duration=2.0,
                     interactive=True):
    """
    Tiered drift handling for a known room.  Behavior depends on
    observed drift magnitude:

      < DRIFT_OK_M       → no action needed, calibration is fine
      < DRIFT_AUTO_M     → auto-correct translation, re-verify
      ≥ DRIFT_AUTO_M     → drift too large, require manual recalibrate

    Args:
      vr               : OpenVR system handle (already initialized)
      calibration      : RoomCalibration object loaded from disk
      sample_duration  : how long to sample the tracker, in seconds
      interactive      : if True, prompts user before sampling
                         if False, samples immediately (for scripted use)

    Returns:
      RoomCalibration  : updated calibration (possibly modified in place)
      None             : if drift is too large and recalibration is needed
    """
    # ── Step 0: sanity check ──────────────────────────────────────
    if not calibration.has_reference():
        print("✗ Calibration has no reference point — cannot relocalize.")
        print("  Run: python main.py calibrate")
        return None

    # ── Step 1: prompt user to place tracker on Point A ──────────
    if interactive:
        print("\n" + "─" * 55)
        print("  RELOCALIZATION")
        print("─" * 55)
        print("  Place a tracker on Point A (the room origin tape mark)")
        print("  Hold it flat and still for ~2 seconds.")
        input("  Press Enter when ready...")

    # ── Step 2: sample the current SteamVR position ──────────────
    try:
        fresh_sv = sample_one_tracker(
            vr,
            duration=sample_duration,
            label="Point A — relocalization check"
        )
    except RuntimeError as e:
        print(f"✗ Sampling failed: {e}")
        print("  Make sure a tracker is powered on and visible.")
        return None

    # ── Step 3: compute drift with the current calibration ───────
    result   = calibration.verify(fresh_sv)
    drift_m  = result["drift_m"]
    drift_cm = drift_m * 100
    delta    = result["delta"]

    print(f"\n  Current drift: {drift_cm:.2f} cm  "
          f"(delta: {delta[0]:+.4f}, {delta[1]:+.4f}, {delta[2]:+.4f}) m")

    # ── Step 4: decide what to do ────────────────────────────────

    # Case A: drift is small, no action needed
    if drift_m <= DRIFT_OK_M:
        print(f"  ✓ Within tolerance ({DRIFT_OK_M*100:.0f} cm) — "
              f"no action needed\n")
        return calibration

    # Case B: drift is moderate, auto-correct
    elif drift_m <= DRIFT_AUTO_M:
        print(f"  ⚠  Moderate drift detected.  Applying automatic "
              f"translation correction...")

        # Apply the translation fix
        calibration.relocalize_translation(fresh_sv)
        calibration.save()

        # Re-verify with a SECOND fresh sample to confirm the fix
        # worked.  We don't reuse fresh_sv because that would
        # trivially show zero drift — we want an independent reading.
        if interactive:
            print(f"\n  Verifying correction... "
                  f"(keep the tracker on Point A)")

        try:
            confirm_sv = sample_one_tracker(
                vr,
                duration=sample_duration,
                label="Point A — confirmation"
            )
        except RuntimeError as e:
            print(f"  ⚠  Confirmation sample failed: {e}")
            print(f"     Correction was applied but not verified.")
            return calibration

        confirm = calibration.verify(confirm_sv)
        post_drift_cm = confirm["drift_m"] * 100

        if confirm["drift_m"] <= DRIFT_POST_FIX_OK_M:
            print(f"  ✓ Re-localization successful.  "
                  f"New drift: {post_drift_cm:.2f} cm\n")
        else:
            print(f"  ⚠  Re-localization incomplete.  "
                  f"Residual drift: {post_drift_cm:.2f} cm")
            print(f"     This may indicate rotation drift "
                  f"(base station moved).")
            print(f"     Consider full recalibration: "
                  f"python main.py calibrate\n")

        return calibration

    # Case C: drift is too large for safe auto-correction
    else:
        print(f"  ✗ Drift of {drift_cm:.2f} cm is too large for "
              f"automatic correction.")
        print(f"     Likely cause: base station moved, axes rotated, "
              f"or wrong reference point.")
        print(f"     Required: full recalibration.")
        print(f"     Run: python main.py calibrate\n")
        return None


# ─── RoomCalibrator ───────────────────────────────────────────────
# Initial 3-point calibration wizard.  Run once per physical setup,
# or whenever smart_relocalize() rejects the drift as too large.

class RoomCalibrator:

    def __init__(self, vr):
        # Store the OpenVR instance so we can poll tracker poses
        self.vr = vr

    def _sample_tracker(self, duration=2.0, label=""):
        # Thin wrapper around the module-level sample_one_tracker().
        # Kept as an instance method for backward compatibility with
        # any code that calls RoomCalibrator(...)._sample_tracker(...).
        return sample_one_tracker(self.vr, duration=duration, label=label)

    def run(self):
        # ── Print physical instructions ────────────────────────────
        print("\n" + "=" * 55)
        print("ROOM COORDINATE CALIBRATION")
        print("=" * 55)
        print("""
Before starting, place tape on the floor at 3 points:

  Point A — your chosen room origin       → becomes (0, 0, 0)
  Point B — 1m to the RIGHT from A        → defines +X axis
  Point C — 1m INTO the room from A       → defines +Z axis

  TIP: Point A also doubles as the verification reference, so
       leave its tape mark in place permanently.  You will return
       to it later for verify and relocalize operations.

Hold the tracker FLAT (face up) at each point.
Keep it still for 2 seconds while sampling.
""")

        # ── Sample Point A (room origin) ───────────────────────────
        input("Hold tracker at Point A — origin. Press Enter...")
        pos_A = self._sample_tracker(
            duration=2.0, label="A — origin (0,0,0)")

        # ── Sample Point B (+X direction) ──────────────────────────
        input("\nHold tracker at Point B — 1m right. Press Enter...")
        pos_B = self._sample_tracker(
            duration=2.0, label="B — +X axis (1,0,0)")

        # ── Sample Point C (+Z direction) ──────────────────────────
        input("\nHold tracker at Point C — 1m into room. Press Enter...")
        pos_C = self._sample_tracker(
            duration=2.0, label="C — +Z axis (0,0,1)")

        # ── Compute room axes from the three sampled points ────────

        # Raw X direction: vector from A to B in SteamVR space
        x_raw = pos_B - pos_A

        # Raw Z direction: vector from A to C in SteamVR space
        z_raw = pos_C - pos_A

        # Normalize X to a unit vector (length = 1)
        x_axis = x_raw / np.linalg.norm(x_raw)

        # Remove any X component from Z so they are perpendicular.
        # This corrects for imperfect tape placement.
        z_axis = z_raw - np.dot(z_raw, x_axis) * x_axis
        z_axis = z_axis / np.linalg.norm(z_axis)

        # Y = cross product of Z and X → perpendicular to both,
        # pointing up (right-handed coordinate system).
        y_axis = np.cross(z_axis, x_axis)
        y_axis = y_axis / np.linalg.norm(y_axis)

        # Recompute Z from X and Y for exact orthonormality.
        z_axis = np.cross(x_axis, y_axis)
        z_axis = z_axis / np.linalg.norm(z_axis)

        # ── Distance sanity check ──────────────────────────────────
        dist_B = np.linalg.norm(pos_B - pos_A)
        dist_C = np.linalg.norm(pos_C - pos_A)
        print(f"\nMeasured distances (should be ~1.0m):")
        print(f"  A → B : {dist_B:.4f} m")
        print(f"  A → C : {dist_C:.4f} m")

        if abs(dist_B - 1.0) > 0.05 or abs(dist_C - 1.0) > 0.05:
            print("⚠  Warning: distance differs from 1.0m by more than 5cm.")
            print("   Re-measure your tape marks and recalibrate if needed.")
            cont = input("Continue anyway? (y/n): ")
            if cont.lower() != "y":
                print("Calibration cancelled.")
                return None

        # ── Build and save calibration ─────────────────────────────
        # Point A doubles as the verification reference.
        calib = RoomCalibration(
            origin=pos_A,
            x_axis=x_axis,
            y_axis=y_axis,
            z_axis=z_axis,
            reference_sv  =pos_A,
            reference_room=np.array([0, 0, 0]),
        )
        calib.save()

        # ── Verification: convert sampled points back to room space ─
        print("\nVerification (expected values shown in brackets):")
        for label, pos, expected in [
            ("Point A", pos_A, "(0.000, 0.000, 0.000)"),
            ("Point B", pos_B, "(1.000, 0.000, 0.000)"),
            ("Point C", pos_C, "(0.000, 0.000, 1.000)"),
        ]:
            r = calib.to_room(pos)
            print(f"  {label}: "
                  f"({r[0]:+.3f}, {r[1]:+.3f}, {r[2]:+.3f})  "
                  f"expected {expected}")

        print("\n✓ Calibration complete.")
        print("  Reference point: Point A.  Verify with: "
              "python main.py verify")
        print("  Auto-correct drift on next session: "
              "happens automatically in print/view modes.\n")
        return calib

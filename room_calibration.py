# ─────────────────────────────────────────────────────────────────
# room_calibration.py   (Phase 3 — flexible Gram-Schmidt + SVD)
#
# PURPOSE:
#   Define a room coordinate system using one of two methods:
#
#     "gram-schmidt"  — flexible 3-point method
#                       Points can be at any distance/direction
#                       (no longer locked to 1m offsets)
#                       Fast, simple, well-understood
#
#     "svd"           — Kabsch/Procrustes multi-point method
#                       3+ points anywhere in the tracking volume
#                       Best-fit transform across all of them
#                       Reports per-point residuals
#                       Most robust to bad measurements
#
#   Both methods produce the same RoomCalibration object format,
#   so verify, relocalize, print, and view modes don't care which
#   was used.
#
# AUDIT LOG:
#   Each calibration also writes a sidecar JSON file
#   (room_calibration.audit.json) containing per-point data: the
#   tracker serial used, the raw SteamVR position sampled, the
#   declared room coordinates, and method-specific outputs like
#   residuals.  Useful for comparing methods later.
#
# AXES:
#   +X = rightward
#   +Y = upward (gravity-aligned)
#   +Z = into the room (depth)
# ─────────────────────────────────────────────────────────────────

import numpy as np
import json
import os
import time
import datetime
import openvr


CALIB_FILE       = "room_calibration.json"
CALIB_AUDIT_FILE = "room_calibration.audit.json"

DRIFT_OK_M          = 0.02
DRIFT_AUTO_M        = 0.10
DRIFT_POST_FIX_OK_M = 0.01


# ─── Helper: sample one tracker for N seconds ─────────────────────

def sample_one_tracker(vr, duration=2.0, label=""):
    """
    Collect tracker positions over `duration` seconds and return
    the mean position plus the serial of the tracker used.

    Returns:
      (mean_position, tracker_serial)

    Raises:
      RuntimeError if no valid tracker was visible during sampling.
    """
    samples = []
    serial_seen = ""
    end = time.time() + duration
    print(f"  Sampling '{label}' for {duration}s ...", end="", flush=True)

    while time.time() < end:
        poses = vr.getDeviceToAbsoluteTrackingPose(
            openvr.TrackingUniverseStanding, 0,
            openvr.k_unMaxTrackedDeviceCount
        )

        for i in range(openvr.k_unMaxTrackedDeviceCount):
            dc = vr.getTrackedDeviceClass(i)
            if dc != openvr.TrackedDeviceClass_GenericTracker:
                try:
                    model = vr.getStringTrackedDeviceProperty(
                        i, openvr.Prop_ModelNumber_String)
                except:
                    model = ""
                if "tracker" not in model.lower():
                    continue

            pose = poses[i]
            if not pose.bPoseIsValid:
                continue

            if not serial_seen:
                try:
                    serial_seen = vr.getStringTrackedDeviceProperty(
                        i, openvr.Prop_SerialNumber_String)
                except:
                    serial_seen = ""

            m = pose.mDeviceToAbsoluteTracking
            samples.append(np.array([m[0][3], m[1][3], m[2][3]]))
            break

        time.sleep(1 / 60)

    if not samples:
        raise RuntimeError("No valid tracker found during sampling")

    mean = np.mean(samples, axis=0)
    print(f" done  (n={len(samples)})  "
          f"raw: x={mean[0]:+.4f} y={mean[1]:+.4f} z={mean[2]:+.4f}")
    return mean, serial_seen


# ─── Helper: prompt the user for room coordinates ────────────────

def prompt_room_coords(point_label, default=None):
    """Ask the user to type room coordinates (X Y Z in meters)."""
    if default is not None:
        prompt = (f"  Room coords for {point_label} "
                  f"(default {default[0]} {default[1]} {default[2]}) "
                  f"[X Y Z in meters, blank=default]: ")
    else:
        prompt = (f"  Room coords for {point_label} "
                  f"[X Y Z in meters]: ")

    while True:
        raw = input(prompt).strip()
        if not raw and default is not None:
            return np.array(default, dtype=float)
        parts = raw.replace(",", " ").split()
        if len(parts) != 3:
            print("  ✗ Enter exactly 3 numbers (X Y Z)")
            continue
        try:
            return np.array([float(p) for p in parts])
        except ValueError:
            print("  ✗ Invalid number — try again")


# ─── RoomCalibration ─────────────────────────────────────────────

class RoomCalibration:

    def __init__(self, origin, x_axis, y_axis, z_axis,
                 reference_sv=None, reference_room=None):
        self.origin = np.array(origin)
        self.R = np.column_stack([x_axis, y_axis, z_axis])
        self.R_inv = self.R.T

        self.reference_sv = (np.array(reference_sv)
                             if reference_sv is not None else None)
        self.reference_room = (np.array(reference_room)
                               if reference_room is not None else None)

    def to_room(self, steamvr_pos):
        return self.R_inv @ (np.array(steamvr_pos) - self.origin)

    def has_reference(self):
        return (self.reference_sv is not None
                and self.reference_room is not None)

    def verify(self, current_sv_pos):
        if not self.has_reference():
            return {"status": "no_reference", "drift_m": None,
                    "expected": None, "measured": None, "delta": None}

        measured = self.to_room(current_sv_pos)
        expected = self.reference_room
        delta    = measured - expected
        drift_m  = float(np.linalg.norm(delta))

        if drift_m <= DRIFT_OK_M:
            status = "ok"
        elif drift_m <= DRIFT_AUTO_M:
            status = "warn"
        else:
            status = "fail"

        return {"status": status, "drift_m": drift_m,
                "expected": expected, "measured": measured, "delta": delta}

    def relocalize_translation(self, fresh_sv_pos_at_origin):
        # Adjust origin so that the freshly sampled SteamVR position
        # maps to the originally-declared reference_room coordinates.
        # If reference_room is (0,0,0), this matches Phase 2 behavior.
        # If reference_room is something else (SVD case), we shift
        # the origin to keep that mapping correct.
        target_room = self.reference_room
        # We want: R_inv @ (fresh_sv - new_origin) = target_room
        #          → new_origin = fresh_sv - R @ target_room
        self.origin = np.array(fresh_sv_pos_at_origin) - self.R @ target_room
        self.reference_sv = np.array(fresh_sv_pos_at_origin)

    def save(self, path=CALIB_FILE):
        data = {
            "origin": self.origin.tolist(),
            "x_axis": self.R[:, 0].tolist(),
            "y_axis": self.R[:, 1].tolist(),
            "z_axis": self.R[:, 2].tolist(),
        }
        if self.has_reference():
            data["reference_sv"]   = self.reference_sv.tolist()
            data["reference_room"] = self.reference_room.tolist()
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"✓ Room calibration saved to {path}")

    @classmethod
    def load(cls, path=CALIB_FILE):
        if not os.path.exists(path):
            return None
        with open(path) as f:
            d = json.load(f)
        print(f"✓ Room calibration loaded from {path}")
        return cls(
            origin=d["origin"], x_axis=d["x_axis"],
            y_axis=d["y_axis"], z_axis=d["z_axis"],
            reference_sv  =d.get("reference_sv"),
            reference_room=d.get("reference_room"),
        )


# ─── Audit log writer ─────────────────────────────────────────────

def write_audit_log(method, points, extra=None, path=CALIB_AUDIT_FILE):
    """Write a calibration audit file with per-point sample details."""
    record = {
        "method": method,
        "created_iso": datetime.datetime.now().isoformat(),
        "points": points,
    }
    if extra:
        record["extra"] = extra
    with open(path, "w") as f:
        json.dump(record, f, indent=2)
    print(f"✓ Calibration audit written to {path}")


# ─── Kabsch SVD solver ────────────────────────────────────────────

def solve_kabsch(sv_pts, room_pts):
    """
    Find R, t such that room_pts[i] ≈ R @ (sv_pts[i] - t) for all i.

    Returns:
      (R, t, residuals, mean_residual)
    """
    sv   = np.array(sv_pts,   dtype=float)
    room = np.array(room_pts, dtype=float)

    c_sv   = sv.mean(axis=0)
    c_room = room.mean(axis=0)
    sv_c   = sv   - c_sv
    room_c = room - c_room

    H = sv_c.T @ room_c
    U, S, Vt = np.linalg.svd(H)

    R = Vt.T @ U.T
    if np.linalg.det(R) < 0:
        Vt[-1, :] *= -1
        R = Vt.T @ U.T

    t = c_sv - R.T @ c_room

    residuals = []
    for sv_i, room_i in zip(sv, room):
        predicted_room = R @ (sv_i - t)
        residuals.append(float(np.linalg.norm(predicted_room - room_i)))
    mean_residual = float(np.mean(residuals))
    return R, t, residuals, mean_residual


# ─── Smart Re-localization (carried over from Phase 2) ───────────

def smart_relocalize(vr, calibration, sample_duration=2.0, interactive=True):
    """Tiered drift handling for a known room."""
    if not calibration.has_reference():
        print("✗ Calibration has no reference point — cannot relocalize.")
        print("  Run: python main.py calibrate")
        return None

    if interactive:
        print("\n" + "─" * 55)
        print("  RELOCALIZATION")
        print("─" * 55)
        print("  Place a tracker on the reference point and hold still.")
        input("  Press Enter when ready...")

    try:
        fresh_sv, _ = sample_one_tracker(
            vr, duration=sample_duration,
            label="reference — relocalization check")
    except RuntimeError as e:
        print(f"✗ Sampling failed: {e}")
        return None

    result = calibration.verify(fresh_sv)
    drift_m = result["drift_m"]
    drift_cm = drift_m * 100
    delta = result["delta"]

    print(f"\n  Current drift: {drift_cm:.2f} cm  "
          f"(delta: {delta[0]:+.4f}, {delta[1]:+.4f}, {delta[2]:+.4f}) m")

    if drift_m <= DRIFT_OK_M:
        print(f"  ✓ Within tolerance ({DRIFT_OK_M*100:.0f} cm) — "
              f"no action needed\n")
        return calibration

    elif drift_m <= DRIFT_AUTO_M:
        print(f"  ⚠  Moderate drift detected.  Applying automatic "
              f"translation correction...")
        calibration.relocalize_translation(fresh_sv)
        calibration.save()

        if interactive:
            print(f"\n  Verifying correction...")

        try:
            confirm_sv, _ = sample_one_tracker(
                vr, duration=sample_duration,
                label="reference — confirmation")
        except RuntimeError as e:
            print(f"  ⚠  Confirmation sample failed: {e}")
            return calibration

        confirm = calibration.verify(confirm_sv)
        post_drift_cm = confirm["drift_m"] * 100

        if confirm["drift_m"] <= DRIFT_POST_FIX_OK_M:
            print(f"  ✓ Re-localization successful.  "
                  f"New drift: {post_drift_cm:.2f} cm\n")
        else:
            print(f"  ⚠  Re-localization incomplete.  "
                  f"Residual drift: {post_drift_cm:.2f} cm")
            print(f"     Consider full recalibration.\n")
        return calibration

    else:
        print(f"  ✗ Drift of {drift_cm:.2f} cm is too large for "
              f"automatic correction.")
        print(f"     Run: python main.py calibrate\n")
        return None


# ─── RoomCalibrator ───────────────────────────────────────────────

class RoomCalibrator:

    def __init__(self, vr):
        self.vr = vr

    # ── Method 1: Flexible Gram-Schmidt 3-point ──────────────────

    def run_gram_schmidt(self):
        """
        3-point calibration with flexible distances.  Points can be
        placed anywhere; user types in the actual room coordinates
        of each one.
        """
        print("\n" + "=" * 60)
        print("ROOM CALIBRATION — Gram-Schmidt (3 flexible points)")
        print("=" * 60)
        print("""
Place tracker at 3 reference points:

  Point A — your chosen origin
  Point B — somewhere along +X     (rightward direction)
  Point C — somewhere along +Z     (into-room direction)

Notes:
  - Distances are flexible (no longer locked to 1m)
  - Place points wherever is convenient and unobstructed
  - You will type each point's actual room coordinates after sampling
  - Points should be at least 30 cm apart from A for noise tolerance
  - B and C should be in clearly different directions from A
""")

        input("Place tracker at Point A (origin), then press Enter...")
        pos_A, serial_A = sample_one_tracker(
            self.vr, duration=2.0, label="A — origin")
        room_A = prompt_room_coords("A", default=[0, 0, 0])

        input("\nPlace tracker at Point B (along +X), then press Enter...")
        pos_B, serial_B = sample_one_tracker(
            self.vr, duration=2.0, label="B — +X direction")
        room_B = prompt_room_coords("B", default=[1, 0, 0])

        input("\nPlace tracker at Point C (along +Z), then press Enter...")
        pos_C, serial_C = sample_one_tracker(
            self.vr, duration=2.0, label="C — +Z direction")
        room_C = prompt_room_coords("C", default=[0, 0, 1])

        # ── Compute axes ───────────────────────────────────────
        # x_raw / z_raw are vectors in SteamVR space pointing from A
        # to B and from A to C.  These define the SteamVR-space
        # directions of the room's +X and +Z axes.
        x_raw = pos_B - pos_A
        z_raw = pos_C - pos_A

        dist_B = np.linalg.norm(x_raw)
        dist_C = np.linalg.norm(z_raw)
        print(f"\nSampled distances:")
        print(f"  A → B (SteamVR space): {dist_B:.4f} m  "
              f"(declared: {np.linalg.norm(room_B - room_A):.4f} m)")
        print(f"  A → C (SteamVR space): {dist_C:.4f} m  "
              f"(declared: {np.linalg.norm(room_C - room_A):.4f} m)")

        if dist_B < 0.30 or dist_C < 0.30:
            print("⚠  Warning: a point is less than 30 cm from A.")
            print("   Calibration will be noise-sensitive.")
            cont = input("Continue anyway? (y/n): ")
            if cont.lower() != "y":
                return None

        decl_B = np.linalg.norm(room_B - room_A)
        decl_C = np.linalg.norm(room_C - room_A)
        if abs(dist_B - decl_B) > 0.05:
            print(f"⚠  Note: A→B sampled {dist_B:.3f}m vs declared "
                  f"{decl_B:.3f}m ({abs(dist_B - decl_B)*100:.1f} cm diff)")
        if abs(dist_C - decl_C) > 0.05:
            print(f"⚠  Note: A→C sampled {dist_C:.3f}m vs declared "
                  f"{decl_C:.3f}m ({abs(dist_C - decl_C)*100:.1f} cm diff)")

        x_axis = x_raw / dist_B
        z_axis = z_raw - np.dot(z_raw, x_axis) * x_axis
        z_norm = np.linalg.norm(z_axis)
        if z_norm < 1e-6:
            print("✗ Points B and C are nearly parallel — calibration "
                  "would be degenerate.")
            return None
        z_axis = z_axis / z_norm

        y_axis = np.cross(z_axis, x_axis)
        y_axis = y_axis / np.linalg.norm(y_axis)
        z_axis = np.cross(x_axis, y_axis)
        z_axis = z_axis / np.linalg.norm(z_axis)

        cos_angle = np.dot(x_raw / dist_B, z_raw / dist_C)
        angle_deg = np.degrees(np.arccos(np.clip(abs(cos_angle), 0, 1)))
        if angle_deg < 30:
            print(f"⚠  B and C are only {angle_deg:.1f}° apart.")
            print(f"   Calibration is more stable when closer to 90°.")
            cont = input("Continue anyway? (y/n): ")
            if cont.lower() != "y":
                return None

        # ── Build calibration ──────────────────────────────────
        # Important subtlety: if room_A is not (0,0,0), the room frame's
        # origin doesn't coincide with point A.  We need to shift the
        # SteamVR origin so to_room(pos_A) returns room_A.
        # That means: origin_sv = pos_A - R @ room_A
        # where R has columns x_axis, y_axis, z_axis.
        R_columns = np.column_stack([x_axis, y_axis, z_axis])
        origin_sv = pos_A - R_columns @ room_A

        calib = RoomCalibration(
            origin=origin_sv,
            x_axis=x_axis,
            y_axis=y_axis,
            z_axis=z_axis,
            reference_sv  =pos_A,
            reference_room=room_A,
        )
        calib.save()

        # ── Audit log ──────────────────────────────────────────
        audit_points = [
            {"label": "A", "room_coords": room_A.tolist(),
             "sv_coords": pos_A.tolist(), "tracker_serial": serial_A},
            {"label": "B", "room_coords": room_B.tolist(),
             "sv_coords": pos_B.tolist(), "tracker_serial": serial_B},
            {"label": "C", "room_coords": room_C.tolist(),
             "sv_coords": pos_C.tolist(), "tracker_serial": serial_C},
        ]
        write_audit_log(
            method="gram-schmidt",
            points=audit_points,
            extra={
                "sampled_distance_AB_m": float(dist_B),
                "sampled_distance_AC_m": float(dist_C),
                "angle_BC_degrees":      float(angle_deg),
            }
        )

        # ── Verification ───────────────────────────────────────
        print("\nVerification (computed vs declared room coords):")
        for label, sv_pos, declared in [
            ("A", pos_A, room_A),
            ("B", pos_B, room_B),
            ("C", pos_C, room_C),
        ]:
            computed = calib.to_room(sv_pos)
            err = np.linalg.norm(computed - declared)
            print(f"  Point {label}: "
                  f"({computed[0]:+.3f}, {computed[1]:+.3f}, {computed[2]:+.3f})  "
                  f"declared ({declared[0]:+.3f}, {declared[1]:+.3f}, {declared[2]:+.3f})  "
                  f"err {err*100:.2f} cm")

        print(f"\n✓ Calibration complete (Gram-Schmidt).")
        print(f"  Reference: Point A at room {tuple(room_A)}")
        print(f"  Audit details: {CALIB_AUDIT_FILE}\n")
        return calib

    # ── Method 2: SVD multi-point (Kabsch) ───────────────────────

    def run_svd(self, min_points=3):
        """
        Multi-point SVD-based calibration.  3+ points anywhere in
        the tracking volume.  Best-fit transform across all of them.
        """
        print("\n" + "=" * 60)
        print("ROOM CALIBRATION — SVD multi-point (Kabsch)")
        print("=" * 60)
        print(f"""
How this works:
  1. Place tracker at any point in your room
  2. Press Enter to sample its SteamVR position
  3. Type in the real-world room coordinates of that point
  4. Repeat for at least {min_points} points (6-10 recommended)
  5. Type 'done' at the prompt to solve

Good points to use:
  - Floor corners or marked spots
  - Top of furniture at a known height
  - Wall-mounted reference at known position

Tips:
  - Spread points across the volume for best results
  - Include at least one elevated point (Y > 0)
  - Don't put all points in a line — geometrically degenerate
""")

        sv_pts, room_pts, serials, labels = [], [], [], []
        point_idx = 0

        while True:
            point_idx += 1
            label = f"P{point_idx}"
            tag = "required" if point_idx <= min_points else "optional"
            print(f"\n─── Point {point_idx} ({tag}) "
                  + "─" * 30)

            if point_idx > min_points:
                action = input(
                    f"  Press Enter to add point {point_idx}, "
                    f"or type 'done' to solve: "
                ).strip().lower()
                if action in ("done", "d", "q", "stop"):
                    break
            else:
                input(f"  Place tracker at point {point_idx}, "
                      f"then press Enter...")

            try:
                sv_pos, serial = sample_one_tracker(
                    self.vr, duration=2.0, label=label)
            except RuntimeError as e:
                print(f"  ✗ Sampling failed: {e}")
                print(f"    Reposition tracker and retry this point.")
                point_idx -= 1
                continue

            room_pos = prompt_room_coords(label)

            sv_pts.append(sv_pos)
            room_pts.append(room_pos)
            serials.append(serial)
            labels.append(label)

            print(f"  ✓ Recorded {label}: "
                  f"room=({room_pos[0]:+.3f}, "
                  f"{room_pos[1]:+.3f}, "
                  f"{room_pos[2]:+.3f}) m")

            if len(sv_pts) >= min_points:
                try:
                    _, _, _, mean_res = solve_kabsch(sv_pts, room_pts)
                    print(f"  Current fit residual: {mean_res*100:.2f} cm "
                          f"(mean across {len(sv_pts)} points)")
                except Exception:
                    pass

        if len(sv_pts) < min_points:
            print(f"\n✗ Need at least {min_points} points. "
                  f"Only {len(sv_pts)} collected.")
            return None

        # ── Solve ──────────────────────────────────────────────
        print(f"\nSolving Kabsch SVD with {len(sv_pts)} points...")
        try:
            R, t, residuals, mean_res = solve_kabsch(sv_pts, room_pts)
        except Exception as e:
            print(f"✗ SVD solve failed: {e}")
            print(f"  Make sure points are not collinear.")
            return None

        # Convert R into the column form RoomCalibration expects.
        # solve_kabsch: room = R @ (sv - t)
        # RoomCalibration.to_room: R_inv @ (sv - origin)
        # We need R_columns such that R_columns.T == R, i.e. R_columns = R.T
        R_columns = R.T
        x_axis = R_columns[:, 0]
        y_axis = R_columns[:, 1]
        z_axis = R_columns[:, 2]

        # First point becomes the reference for verify/relocalize
        reference_sv   = sv_pts[0]
        reference_room = room_pts[0]

        calib = RoomCalibration(
            origin=t,
            x_axis=x_axis,
            y_axis=y_axis,
            z_axis=z_axis,
            reference_sv  =reference_sv,
            reference_room=reference_room,
        )

        # ── Report ─────────────────────────────────────────────
        print(f"\n{'─'*60}")
        print(f"CALIBRATION RESULTS — Kabsch SVD, {len(sv_pts)} points")
        print(f"{'─'*60}")
        print(f"{'Label':<6} {'Declared (room)':^27} "
              f"{'Computed (room)':^27} {'Error':>8}")
        print(f"{'─'*60}")

        for lbl, sv_i, room_i, res in zip(labels, sv_pts, room_pts, residuals):
            computed = calib.to_room(sv_i)
            flag = " ← HIGH" if res > 0.05 else ""
            print(f"  {lbl:<4} "
                  f"({room_i[0]:+.3f}, {room_i[1]:+.3f}, {room_i[2]:+.3f})  "
                  f"({computed[0]:+.3f}, {computed[1]:+.3f}, {computed[2]:+.3f})  "
                  f"{res*100:>6.2f} cm{flag}")

        max_res = max(residuals)
        print(f"{'─'*60}")
        print(f"  Mean residual: {mean_res*100:.2f} cm")
        print(f"  Max residual : {max_res*100:.2f} cm")

        if mean_res > 0.05:
            print(f"\n  ⚠  Mean error > 5cm.  Consider:")
            print(f"       - Re-sampling high-error points")
            print(f"       - Adding more points")
            print(f"       - Spreading points across more volume")

        calib.save()

        audit_points = []
        for lbl, sv_i, room_i, ser, res in zip(
                labels, sv_pts, room_pts, serials, residuals):
            audit_points.append({
                "label":          lbl,
                "room_coords":    room_i.tolist(),
                "sv_coords":      sv_i.tolist(),
                "tracker_serial": ser,
                "residual_m":     float(res),
            })

        write_audit_log(
            method="svd",
            points=audit_points,
            extra={
                "n_points":        len(sv_pts),
                "mean_residual_m": float(mean_res),
                "max_residual_m":  float(max_res),
            }
        )

        print(f"\n✓ Calibration complete (SVD).")
        print(f"  Reference: {labels[0]} at room {tuple(room_pts[0])}")
        print(f"  Audit details: {CALIB_AUDIT_FILE}\n")
        return calib

    # Default: Gram-Schmidt (backward compatible with Phase 2 .run())
    def run(self):
        return self.run_gram_schmidt()

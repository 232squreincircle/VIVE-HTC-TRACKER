# ─────────────────────────────────────────────────────────────────
# tracker_output.py   (Phase 2 — unchanged behavior, reconstructed)
#
# PURPOSE:
#   - Discover trackers via OpenVR
#   - Label them as Left/Right wrist using SERIAL_TO_LABEL (stable
#     across sessions since serials persist while slot indices do not)
#   - Convert raw SteamVR positions to room coordinates using a
#     supplied RoomCalibration
#   - Provide live output:
#       run_terminal()  → printed positions, refreshes in place
#       run_3d()        → matplotlib 3D visualization
#   - identify_wrists() → one-time helper to discover which serial
#                         belongs to which physical wrist
#
# NOTE FOR THE USER:
#   This file is RECONSTRUCTED to match the behavior described in
#   our conversation history.  If your on-disk version contains
#   additional tweaks you made (e.g. X/Y swap, custom thresholds,
#   colors), copy those changes back into this file rather than
#   replacing your version wholesale.
# ─────────────────────────────────────────────────────────────────

import os
import sys
import time
import numpy as np
import openvr

# Enable ANSI escape codes on Windows cmd.exe.  Without this, the
# cursor-overwrite terminal output appears as garbled escape chars.
os.system("")


# ─── SERIAL_TO_LABEL ──────────────────────────────────────────────
# Maps tracker serial numbers to human-readable labels.  Serials
# persist across SteamVR sessions; slot indices do not.  Run
# `python main.py identify` to populate this for your hardware.
#
# Example after running identify:
#   SERIAL_TO_LABEL = {
#       "LHR-54A2BA1C": "Left Wrist ",
#       "LHR-AAD46D19": "Right Wrist",
#   }

SERIAL_TO_LABEL = {
    "LHR-AAD46D19": "Left Wrist ",
    "LHR-54A2BA1C": "Right Wrist",
}


# Number of rolling-average frames used to smooth tracker output.
# Higher = smoother but more lag.  5 is a good balance at 60 Hz.
SMOOTH_N = 5


# ─── TrackerOutput ────────────────────────────────────────────────

class TrackerOutput:
    """
    Discovers trackers via OpenVR, converts their SteamVR positions
    into room coordinates via the supplied RoomCalibration, and
    provides both terminal and 3D-visualization output loops.
    """

    def __init__(self, vr, calibration):
        self.vr          = vr
        self.calibration = calibration

        self.tracker_indices = []     # list of OpenVR device indices
        self.tracker_labels  = {}     # idx → human label
        self._history        = {}     # idx → list of recent positions

        self._discover()

    def _discover(self):
        """
        Scan all OpenVR device slots and register each tracker.
        Labels come from SERIAL_TO_LABEL when possible, with a
        fallback to "Tracker N" when the serial isn't mapped.
        """
        poses = self.vr.getDeviceToAbsoluteTrackingPose(
            openvr.TrackingUniverseStanding,
            0,
            openvr.k_unMaxTrackedDeviceCount
        )

        count = 0
        for i in range(openvr.k_unMaxTrackedDeviceCount):
            dc = self.vr.getTrackedDeviceClass(i)
            if dc == openvr.TrackedDeviceClass_Invalid:
                continue

            try:
                model  = self.vr.getStringTrackedDeviceProperty(
                    i, openvr.Prop_ModelNumber_String)
                serial = self.vr.getStringTrackedDeviceProperty(
                    i, openvr.Prop_SerialNumber_String)
            except Exception:
                model, serial = "", "unknown"

            is_tracker = (
                dc == openvr.TrackedDeviceClass_GenericTracker
                or "tracker" in model.lower()
            )
            if not is_tracker:
                continue

            count += 1

            # Look up the label by serial number.  Fall back to a
            # generic name if this serial hasn't been identified yet.
            if serial in SERIAL_TO_LABEL:
                label  = SERIAL_TO_LABEL[serial]
                source = "[serial]"
            else:
                label  = f"Tracker {count}"
                source = "[order — run identify to map]"

            self.tracker_indices.append(i)
            self.tracker_labels[i] = label
            self._history[i]       = []
            print(f"  ✓ {label} — idx={i}  serial={serial}  {source}")

        print(f"\n  {count} tracker(s) found\n")
        if count == 0:
            print("✗ No trackers found — check that SteamVR sees them")
            openvr.shutdown()
            sys.exit(1)

    def get_room_positions(self, smooth=True):
        """
        Poll OpenVR once and return room-space positions for all
        registered trackers.

        Returns:
          { label: np.array([x, y, z]) or None }
          None means the tracker had an invalid pose this frame.
        """
        poses = self.vr.getDeviceToAbsoluteTrackingPose(
            openvr.TrackingUniverseStanding,
            0,
            openvr.k_unMaxTrackedDeviceCount
        )

        result = {}

        for idx in self.tracker_indices:
            label = self.tracker_labels[idx]
            pose  = poses[idx]

            if not pose.bPoseIsValid:
                # Tracker temporarily lost — clear smoothing history
                # so we don't blend old positions with new after
                # tracking recovers.
                self._history[idx] = []
                result[label] = None
                continue

            m = pose.mDeviceToAbsoluteTracking
            sv_pos = np.array([m[0][3], m[1][3], m[2][3]])

            # Convert from SteamVR space to room space
            room_pos = self.calibration.to_room(sv_pos)

            # Rolling-average smoothing
            if smooth:
                self._history[idx].append(room_pos)
                if len(self._history[idx]) > SMOOTH_N:
                    self._history[idx].pop(0)
                room_pos = np.mean(self._history[idx], axis=0)

            result[label] = room_pos

        return result

    def run_terminal(self, frequency=60.0, logger=None):
        """
        Live terminal output.  Prints the header once, then
        overwrites the per-tracker lines in place using ANSI
        cursor-up + erase-line escape codes.  Result: the numbers
        update smoothly without the screen scrolling.

        Args:
          frequency : update rate in Hz
          logger    : optional SessionLogger; if provided, every
                      sampled frame is also written to CSV
        """
        interval = 1.0 / frequency

        # Static header — printed once, never overwritten
        print(f"Live tracker positions at {frequency:.0f}Hz  (Ctrl+C to stop)")
        print(f"Origin (0, 0, 0) = left corner of room at floor level")
        print(f"X = rightward  |  Y = up  |  Z = into room")
        print("-" * 60)

        # Number of dynamic lines: one per tracker, plus a blank line
        # after them for spacing.  We move the cursor up this many
        # lines each frame to overwrite the previous output.
        n_lines = len(self.tracker_indices) + 1

        # Print the dynamic block once so subsequent frames have
        # something to overwrite (otherwise the cursor-up codes would
        # go above the header).
        for _ in range(n_lines):
            print()

        try:
            while True:
                start     = time.time()
                positions = self.get_room_positions()

                # If a SessionLogger was provided, persist this frame
                # to disk.  Uses time.time_ns() for nanosecond
                # precision so downstream multimodal alignment can
                # sync against other sensor streams.
                if logger is not None:
                    logger.write_frame(time.time_ns(), positions)

                # Move cursor up n_lines lines, erasing each line
                # as we go so old characters don't linger when the
                # new content is shorter.
                for _ in range(n_lines):
                    sys.stdout.write("\033[A")    # move up
                    sys.stdout.write("\033[2K")   # erase line

                # Re-draw the dynamic block
                for label, pos in positions.items():
                    if pos is not None:
                        sys.stdout.write(
                            f"  {label:12s} | "
                            f"X: {pos[0]:+7.4f} m  "
                            f"Y: {pos[1]:+7.4f} m  "
                            f"Z: {pos[2]:+7.4f} m\n"
                        )
                    else:
                        sys.stdout.write(
                            f"  {label:12s} | "
                            f"⚠  out of tracking range\n"
                        )
                sys.stdout.write("\n")  # blank spacer line
                sys.stdout.flush()

                # Pace the loop to the requested frequency
                elapsed = time.time() - start
                sleep   = interval - elapsed
                if sleep > 0:
                    time.sleep(sleep)

        except KeyboardInterrupt:
            print("\nStopped")

    def run_3d(self, frequency=60.0, logger=None):
        """
        Live 3D matplotlib visualization in room coordinates.
        Draws each tracker as a colored dot with a fading trail.
        Closes cleanly when the window is closed or Ctrl+C is hit.

        Args:
          frequency : update rate in Hz
          logger    : optional SessionLogger; if provided, every
                      sampled frame is also written to CSV
        """
        # Import matplotlib lazily so users who only use run_terminal
        # don't need it installed.
        import matplotlib.pyplot as plt
        import matplotlib.animation as animation
        from mpl_toolkits.mplot3d import Axes3D    # noqa: F401

        COLORS  = ["#00BFFF", "#FF6347", "#32CD32", "#FFD700"]
        TRAIL   = 60
        history = {label: [] for label in self.tracker_labels.values()}

        fig = plt.figure(figsize=(10, 8), facecolor="#1a1a2e")
        fig.suptitle(
            "Tracker Position — Room Coordinates (meters)\n"
            "Origin (0, 0, 0) = Left Corner of Room",
            color="white", fontsize=12, fontweight="bold"
        )
        ax = fig.add_subplot(111, projection="3d")

        # ── Style ──
        ax.set_facecolor("#16213e")
        for pane in [ax.xaxis.pane, ax.yaxis.pane, ax.zaxis.pane]:
            pane.fill = False
            pane.set_edgecolor("#0f3460")
        ax.tick_params(colors="white", labelsize=8)
        ax.set_xlabel("X — right (m)",  color="white", fontsize=9)
        ax.set_ylabel("Z — depth (m)",  color="white", fontsize=9)
        ax.set_zlabel("Y — height (m)", color="white", fontsize=9)

        # Approximate room size.  Adjust to your actual room.
        ROOM_W, ROOM_D, ROOM_H = 5.0, 5.0, 3.0
        ax.set_xlim(0, ROOM_W)
        ax.set_ylim(0, ROOM_D)
        ax.set_zlim(0, ROOM_H)

        # Room wireframe — four vertical edges + floor grid
        for x, z in [(0, 0), (ROOM_W, 0), (ROOM_W, ROOM_D), (0, ROOM_D)]:
            ax.plot([x, x], [z, z], [0, ROOM_H],
                    color="#0f3460", lw=0.8, alpha=0.5)
        for zi in np.arange(0, ROOM_D + 1, 1):
            ax.plot([0, ROOM_W], [zi, zi], [0, 0],
                    color="#0f3460", lw=0.4, alpha=0.3)
        for xi in np.arange(0, ROOM_W + 1, 1):
            ax.plot([xi, xi], [0, ROOM_D], [0, 0],
                    color="#0f3460", lw=0.4, alpha=0.3)

        ax.text(0, 0, 0, "(0,0,0)", color="#FFD700", fontsize=8)

        # Tracker scatter and trail objects, one per tracker
        labels  = list(self.tracker_labels.values())
        sc_list = []
        tr_list = []
        for i, label in enumerate(labels):
            sc = ax.scatter([], [], [], s=250, c=COLORS[i % len(COLORS)],
                            depthshade=False, zorder=6, label=label)
            tr, = ax.plot([], [], [], color=COLORS[i % len(COLORS)],
                          alpha=0.3, lw=1.5)
            sc_list.append(sc)
            tr_list.append(tr)

        # Live info text in top-left of the 3D axes
        info = ax.text2D(0.01, 0.97, "",
                         transform=ax.transAxes,
                         color="white", fontsize=9,
                         verticalalignment="top",
                         fontfamily="monospace")

        ax.legend(loc="upper right", labelcolor="white",
                  facecolor="#16213e", edgecolor="#0f3460", fontsize=9)

        def update(_frame):
            positions  = self.get_room_positions()
            info_lines = []

            # If a SessionLogger was provided, persist this frame
            # to disk before drawing it.  This keeps the visual and
            # logged data in sync (same positions, same timestamp).
            if logger is not None:
                logger.write_frame(time.time_ns(), positions)

            for i, label in enumerate(labels):
                pos = positions.get(label)
                if pos is not None:
                    # Matplotlib 3D takes (X, Y_plot, Z_plot).
                    # We map: room X→plot X, room Z→plot Y, room Y→plot Z
                    # so the plot's "height" axis corresponds to real
                    # room height.
                    sc_list[i]._offsets3d = ([pos[0]], [pos[2]], [pos[1]])

                    history[label].append((pos[0], pos[2], pos[1]))
                    if len(history[label]) > TRAIL:
                        history[label].pop(0)

                    info_lines.append(
                        f"{label}\n"
                        f"  X: {pos[0]:+.4f} m\n"
                        f"  Y: {pos[1]:+.4f} m\n"
                        f"  Z: {pos[2]:+.4f} m"
                    )
                else:
                    sc_list[i]._offsets3d = ([], [], [])
                    info_lines.append(f"{label}\n  ⚠ out of range")

                if len(history[label]) > 1:
                    h = np.array(history[label])
                    tr_list[i].set_data(h[:, 0], h[:, 1])
                    tr_list[i].set_3d_properties(h[:, 2])
                else:
                    tr_list[i].set_data([], [])
                    tr_list[i].set_3d_properties([])

            info.set_text("\n".join(info_lines))
            return []

        ani = animation.FuncAnimation(
            fig, update,
            interval=int(1000 / frequency),
            blit=False,
            cache_frame_data=False
        )

        plt.tight_layout()
        plt.show()


# ─── identify_wrists ──────────────────────────────────────────────
# One-time helper to discover which physical wrist owns which serial.
# Run with one tracker powered on at a time so the wizard can record
# unambiguously which serial corresponds to LEFT vs RIGHT.

def identify_wrists(vr):
    """
    Interactive wizard that walks the user through powering on one
    tracker at a time (LEFT first, then RIGHT) to discover their
    serial numbers.

    Prints copy-paste-ready Python code for SERIAL_TO_LABEL at the end.
    """
    print("\n" + "=" * 55)
    print("WRIST IDENTIFICATION WIZARD")
    print("=" * 55)
    print("""
This wizard discovers which tracker serial number belongs to
your LEFT wrist and which belongs to your RIGHT wrist.

  - Power OFF all trackers before starting.
  - You will be asked to power them on ONE AT A TIME.
  - Hold the system button until the tracker vibrates and the
    LED turns solid green to power it on.
""")

    found = {}     # label → serial

    for side in ("LEFT", "RIGHT"):
        print(f"\n─── Step: {side} wrist ─────────────────────────────")
        print(f"  Power ON the tracker on your {side} wrist only.")
        print(f"  Keep all other trackers powered OFF.")
        input(f"  Press Enter when the {side} tracker is on and tracking...")

        # Allow a couple of seconds for SteamVR to register the device
        print(f"  Waiting for tracker to appear...", end="", flush=True)
        time.sleep(3)
        print(" done.")

        # Scan for the (single) tracker currently visible
        poses = vr.getDeviceToAbsoluteTrackingPose(
            openvr.TrackingUniverseStanding,
            0,
            openvr.k_unMaxTrackedDeviceCount
        )

        candidates = []
        for i in range(openvr.k_unMaxTrackedDeviceCount):
            dc = vr.getTrackedDeviceClass(i)
            if dc == openvr.TrackedDeviceClass_Invalid:
                continue
            try:
                model  = vr.getStringTrackedDeviceProperty(
                    i, openvr.Prop_ModelNumber_String)
                serial = vr.getStringTrackedDeviceProperty(
                    i, openvr.Prop_SerialNumber_String)
            except Exception:
                continue

            is_tracker = (
                dc == openvr.TrackedDeviceClass_GenericTracker
                or "tracker" in model.lower()
            )
            if is_tracker and poses[i].bPoseIsValid:
                candidates.append((i, serial))

        if not candidates:
            print(f"  ✗ No tracker detected for {side}.")
            print(f"    Skipping {side} — you may need to retry.")
            continue

        if len(candidates) > 1:
            print(f"  ⚠  More than one tracker visible:")
            for idx, ser in candidates:
                print(f"     idx={idx} serial={ser}")
            print(f"     Power off all but the {side} tracker and retry.")
            continue

        idx, serial = candidates[0]
        label       = "Left Wrist " if side == "LEFT" else "Right Wrist"
        found[label] = serial
        print(f"  ✓ {side} wrist serial: {serial}  (idx={idx})")

        if side == "LEFT":
            print(f"\n  Now power OFF the LEFT tracker before continuing.")
            input(f"  Press Enter when the LEFT tracker is powered off...")

    # ── Output a copy-paste block ───────────────────────────────────
    print("\n" + "=" * 55)
    print("IDENTIFICATION COMPLETE")
    print("=" * 55)
    if not found:
        print("  ✗ No tracker mappings were captured.  Try again.")
        return

    print("\n  Paste this into tracker_output.py to replace the")
    print("  SERIAL_TO_LABEL dictionary:\n")
    print("SERIAL_TO_LABEL = {")
    for label, serial in found.items():
        print(f'    "{serial}": "{label}",')
    print("}\n")

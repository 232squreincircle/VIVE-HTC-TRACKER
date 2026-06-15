# ─────────────────────────────────────────────────────────────────
# session_logger.py   (NEW — Phase 3 session logging)
#
# PURPOSE:
#   Write timestamped tracker positions to a CSV file during a
#   recording session.  Intended for downstream multimodal analysis
#   where this data needs to be aligned with gaze, video, or speech.
#
# OUTPUT FILES (per session):
#   sessions/2026-06-08_143052_my_session.csv       ← the data
#   sessions/2026-06-08_143052_my_session.meta.json ← metadata
#
# CSV FORMAT:
#   timestamp_ns,tracker_label,x,y,z,valid
#   1716843213847291000,Left Wrist ,1.2340,0.9821,2.1043,1
#   1716843213864123000,Left Wrist ,1.2341,0.9820,2.1044,1
#   ...
#
# DESIGN NOTES:
#   - Each tracker writes its own row each frame (long format).
#     This is easier for downstream tools like pandas to filter.
#   - Validity flag is 1/0 so the CSV stays numeric-friendly.
#   - We flush periodically, not every frame, to avoid disk-I/O
#     bottlenecks at high sample rates.
# ─────────────────────────────────────────────────────────────────

import os
import json
import time
import datetime


# Folder where session files are written.  Auto-created on first use.
SESSIONS_DIR = "sessions"

# How often (in frames) to flush the CSV to disk.  Higher = less
# disk overhead, but more data lost if the program crashes.  At
# 60 Hz, flushing every 60 frames means at most ~1 second of data
# lost on crash.
FLUSH_EVERY_N_FRAMES = 60


class SessionLogger:
    """
    Records timestamped tracker positions to a CSV file plus a
    sidecar JSON file with metadata.

    Usage:
        logger = SessionLogger("my_session_name", metadata={...})
        for each frame:
            logger.write_frame(timestamp_ns, positions_dict)
        logger.close()

    Or use as a context manager:
        with SessionLogger("name", metadata={...}) as logger:
            ...
    """

    def __init__(self, session_name, metadata=None):
        # Make the sessions folder if it doesn't exist
        os.makedirs(SESSIONS_DIR, exist_ok=True)

        # Build timestamped filename so multiple sessions in one day
        # don't collide.  Format chosen to sort lexicographically.
        now      = datetime.datetime.now()
        ts_str   = now.strftime("%Y-%m-%d_%H%M%S")
        # Sanitize the user-provided session name: keep alphanumerics,
        # underscores, and hyphens; replace anything else with _
        safe_name = "".join(
            c if c.isalnum() or c in "_-" else "_"
            for c in session_name
        )
        base = f"{ts_str}_{safe_name}"

        self.csv_path  = os.path.join(SESSIONS_DIR, f"{base}.csv")
        self.meta_path = os.path.join(SESSIONS_DIR, f"{base}.meta.json")

        # Open the CSV file in line-buffered text mode.  Newlines
        # are written explicitly.  We open in 'w' which truncates
        # any existing file with the same name (shouldn't happen
        # due to the timestamp in the filename, but defensive).
        self.csv_file = open(self.csv_path, "w", buffering=1)

        # Write CSV header
        self.csv_file.write("timestamp_ns,tracker_label,x,y,z,valid\n")

        # Track frame count for periodic flushing
        self.frame_count = 0

        # Stash metadata for writing at close.  We record start time
        # now and end time at close so the meta file always reflects
        # the actual session duration.
        self.metadata = dict(metadata or {})
        self.metadata["session_name"]      = session_name
        self.metadata["csv_path"]          = self.csv_path
        self.metadata["start_time_iso"]    = now.isoformat()
        self.metadata["start_time_ns"]     = time.time_ns()
        # end_time will be filled in by close()

        # Track session totals for the meta summary
        self._total_frames    = 0
        self._invalid_frames  = 0

        print(f"✓ Session logging started")
        print(f"    Data : {self.csv_path}")
        print(f"    Meta : {self.meta_path}")

    def write_frame(self, timestamp_ns, positions):
        """
        Write one frame of tracker data to the CSV.

        Args:
          timestamp_ns : int, nanosecond timestamp (from time.time_ns())
          positions    : dict { label: np.array([x,y,z]) or None }
                         None means the tracker had an invalid pose.
        """
        for label, pos in positions.items():
            if pos is not None:
                self.csv_file.write(
                    f"{timestamp_ns},{label},"
                    f"{pos[0]:.6f},{pos[1]:.6f},{pos[2]:.6f},1\n"
                )
            else:
                # Invalid pose — write a row with NaN coords so
                # downstream tools know the timestamp existed but
                # the tracker wasn't tracked at that moment.
                self.csv_file.write(
                    f"{timestamp_ns},{label},nan,nan,nan,0\n"
                )
                self._invalid_frames += 1

            self._total_frames += 1

        # Periodic flush so data is durable without per-frame I/O
        self.frame_count += 1
        if self.frame_count >= FLUSH_EVERY_N_FRAMES:
            self.csv_file.flush()
            self.frame_count = 0

    def close(self):
        """
        Flush and close the CSV file, then write the metadata sidecar.
        Safe to call multiple times.
        """
        if self.csv_file is None:
            return    # already closed

        # Final flush + close on the CSV
        self.csv_file.flush()
        self.csv_file.close()
        self.csv_file = None

        # Fill in end-of-session metadata
        end_time_ns = time.time_ns()
        duration_s  = (end_time_ns - self.metadata["start_time_ns"]) / 1e9
        self.metadata["end_time_iso"]     = datetime.datetime.now().isoformat()
        self.metadata["end_time_ns"]      = end_time_ns
        self.metadata["duration_seconds"] = round(duration_s, 3)
        self.metadata["total_rows"]       = self._total_frames
        self.metadata["invalid_rows"]     = self._invalid_frames

        # Write the metadata JSON
        with open(self.meta_path, "w") as f:
            json.dump(self.metadata, f, indent=2)

        print(f"✓ Session logging stopped")
        print(f"    Duration : {duration_s:.1f} s")
        print(f"    Rows     : {self._total_frames}  "
              f"({self._invalid_frames} with invalid pose)")
        print(f"    Saved to : {self.csv_path}")

    # Context-manager protocol so `with SessionLogger(...) as log:`
    # automatically closes on exit, even if an exception is raised.

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False    # do not suppress exceptions

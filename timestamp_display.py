# ─────────────────────────────────────────────────────────────────
# timestamp_display.py   (Phase 4 — camera-readable timestamp + events)
#
# PURPOSE:
#   A standalone matplotlib window designed to be visible to a video
#   camera during a recording session.  Displays:
#     - Big readable wall-clock time at millisecond precision
#     - Current session name (editable)
#     - Elapsed time since session started
#     - A text-input field for typing notes during the experiment
#
#   While running, the window logs events (session start, notes,
#   session end) to a CSV file in the sessions/ folder.  Each event
#   carries a nanosecond timestamp so it can be aligned with the
#   tracker session CSV in downstream analysis.
#
# USAGE:
#   Open in a terminal (separate from the one running main.py):
#     python timestamp_display.py
#
#   In the window:
#     1. Type a session name into the Session field
#        (pressing Enter just commits the text — it does NOT start
#         the session yet, giving you a chance to verify the name)
#     2. Click "Start Session" → creates an events CSV file
#     3. During the session, type notes into the Notes field and
#        press Enter to log them
#     4. Click "End Session" or close the window to finalize
#
#   For multimodal sync:
#     Position the window so the wall-mounted camera can see the
#     big timestamp text clearly.  In post-processing, read the
#     timestamp from a video frame to align with the tracker CSV.
#
# OUTPUT FILE:
#   sessions/YYYY-MM-DD_HHMMSS_<session_name>_events.csv
#
#   Format:
#     timestamp_ns,event_type,text
#     1716843213847291000,session_start,Session 'trial_01' started
#     1716843230123456000,note,participant looked away briefly
#     1716843240500000000,note,(stopwatch started at 00:00:00.000)
#     1716843267000000000,lap,Lap 1 — stopwatch 00:00:26.500
#     1716843298111222000,session_end,Session ended
#
#   Event types:
#     session_start   — session began
#     session_end     — session ended
#     note            — researcher-typed annotation
#     lap             — stopwatch lap (only when stopwatch is running)
#                       Other stopwatch actions (start/stop/reset)
#                       are logged as notes for context.
#
# DESIGN NOTES:
#   - Internal timestamps use time.time_ns() — full nanosecond
#     precision — to match the format in the tracker session CSV.
#   - On-screen display is rounded to milliseconds (HH:MM:SS.mmm)
#     because that's the precision a 30fps camera can capture.
#   - No coordination with the tracker code — user is responsible
#     for typing the same session name in both places.
# ─────────────────────────────────────────────────────────────────

import os
import time
import datetime
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from matplotlib.widgets import TextBox, Button


# Same folder as tracker sessions, for easy cross-referencing later.
SESSIONS_DIR = "sessions"

# Display update rate.  30 Hz is plenty for camera capture (matches
# typical camera framerate) and keeps CPU usage low.
DISPLAY_HZ = 30


def format_clock_ms(ns):
    """
    Convert a nanosecond timestamp into a human-readable string with
    millisecond precision: HH:MM:SS.mmm

    We drop nanoseconds for display because:
      - A 30fps camera only captures one frame per ~33ms anyway
      - Sub-millisecond text doesn't render meaningfully large
      - The full-precision ns value is still in the events CSV
    """
    # ns → datetime via microseconds (//1000 = us, //1000000 = ms)
    # datetime supports microseconds, so we use that and slice the
    # printed string to keep only the milliseconds portion.
    sec = ns // 1_000_000_000
    ms  = (ns // 1_000_000) % 1000
    dt  = datetime.datetime.fromtimestamp(sec)
    return f"{dt:%H:%M:%S}.{ms:03d}"


def format_elapsed(seconds):
    """Convert elapsed seconds into HH:MM:SS string."""
    if seconds < 0:
        return "00:00:00"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_stopwatch(seconds):
    """
    Convert a non-negative stopwatch duration in seconds into a
    HH:MM:SS.mmm string.  Used for the stopwatch display and for
    lap timestamps written to the events file.

    Includes milliseconds because that's the precision researchers
    want for interval timing (reaction times, trial durations, etc.).
    """
    if seconds < 0:
        seconds = 0.0
    total_ms = int(seconds * 1000)
    h  = total_ms // 3_600_000
    m  = (total_ms // 60_000) % 60
    s  = (total_ms // 1_000)  % 60
    ms = total_ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def sanitize_name(name):
    """
    Strip characters that would break filenames.  Keeps alphanumerics,
    underscores, and hyphens; replaces everything else with underscores.
    """
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in name)


# ─────────────────────────────────────────────────────────────────
# EventLogger — writes session events to a CSV file
# ─────────────────────────────────────────────────────────────────

class EventLogger:
    """
    Appends event rows to a session-specific CSV file.  Each event has
    a nanosecond timestamp, an event_type label, and free-form text.

    The file is opened in append mode so events flush to disk as soon
    as they're written — important because a power failure or crash
    shouldn't lose annotation data.
    """

    def __init__(self, session_name):
        os.makedirs(SESSIONS_DIR, exist_ok=True)

        now = datetime.datetime.now()
        ts_str = now.strftime("%Y-%m-%d_%H%M%S")
        safe_name = sanitize_name(session_name)
        base = f"{ts_str}_{safe_name}"

        self.session_name = session_name
        self.csv_path  = os.path.join(SESSIONS_DIR, f"{base}_events.csv")
        self.start_ns  = time.time_ns()

        # Open in append mode so each write is durable.  Use line
        # buffering so the file system sees each row as it's written
        # (no need to flush manually).
        self.csv_file = open(self.csv_path, "w", buffering=1)
        self.csv_file.write("timestamp_ns,event_type,text\n")

        self._write("session_start",
                    f"Session '{session_name}' started")

        print(f"✓ Event log started")
        print(f"    Path : {self.csv_path}")

    def _write(self, event_type, text):
        # CSV-escape any commas or quotes in the text by wrapping in
        # double quotes and doubling any embedded double quotes.
        # This is the minimal CSV-safe encoding.
        if "," in text or '"' in text or "\n" in text:
            escaped = '"' + text.replace('"', '""') + '"'
        else:
            escaped = text

        self.csv_file.write(
            f"{time.time_ns()},{event_type},{escaped}\n"
        )

    def note(self, text):
        """Log a freeform note from the researcher."""
        if not text:
            return
        self._write("note", text)
        print(f"  + note: {text}")

    def close(self):
        """Write a session_end marker and close the file."""
        if self.csv_file is None:
            return
        self._write("session_end", "Session ended")
        self.csv_file.flush()
        self.csv_file.close()
        self.csv_file = None
        print(f"✓ Event log closed: {self.csv_path}")


# ─────────────────────────────────────────────────────────────────
# Stopwatch — a simple start/stop/reset/lap timer
# ─────────────────────────────────────────────────────────────────

class Stopwatch:
    """
    Independent stopwatch with start/stop/reset/lap operations.

    State model:
      - `accumulated_s`  : frozen seconds from previous runs (before
                           the current pause/stop)
      - `run_start_ns`   : timestamp at which the current run started
                           (None when stopped)

    Elapsed time at any moment:
      - If running:  accumulated_s + (now - run_start_ns)/1e9
      - If stopped:  accumulated_s

    Laps are stored as a list of (lap_number, elapsed_s) tuples.
    The list grows unbounded, but the display shows only the last
    few.  Resetting the stopwatch clears the lap list.
    """

    def __init__(self):
        self.accumulated_s = 0.0
        self.run_start_ns  = None     # None = stopped, ns = running
        self.laps          = []       # list of (lap_number, elapsed_s)

    @property
    def is_running(self):
        return self.run_start_ns is not None

    def elapsed_s(self):
        """Current elapsed time in seconds."""
        if self.is_running:
            now_ns = time.time_ns()
            return self.accumulated_s + (now_ns - self.run_start_ns) / 1e9
        return self.accumulated_s

    def start(self):
        """Start (or resume) ticking.  No-op if already running."""
        if self.is_running:
            return False
        self.run_start_ns = time.time_ns()
        return True

    def stop(self):
        """
        Pause ticking, freezing the current elapsed time.
        No-op if already stopped.
        """
        if not self.is_running:
            return False
        # Bank the time accumulated during this run, then clear the
        # running-start marker so elapsed_s() returns the frozen value.
        now_ns = time.time_ns()
        self.accumulated_s += (now_ns - self.run_start_ns) / 1e9
        self.run_start_ns   = None
        return True

    def reset(self):
        """
        Reset to 00:00:00.000 and clear lap history.  Stops the
        stopwatch if it was running.
        """
        self.accumulated_s = 0.0
        self.run_start_ns  = None
        self.laps          = []

    def lap(self):
        """
        Record the current elapsed time as a lap.  Only allowed when
        the stopwatch is running — capturing a lap on a stopped
        stopwatch is almost always accidental.

        Returns (lap_number, elapsed_s) on success, or None if the
        stopwatch wasn't running.
        """
        if not self.is_running:
            return None
        elapsed = self.elapsed_s()
        lap_number = len(self.laps) + 1
        self.laps.append((lap_number, elapsed))
        return (lap_number, elapsed)


# ─────────────────────────────────────────────────────────────────
# Main display window
# ─────────────────────────────────────────────────────────────────

class TimestampDisplay:
    """
    The visible window: shows clock, session info, elapsed time, and
    accepts notes.  Camera-friendly layout — big text on dark
    background.
    """

    def __init__(self, width_in=14, height_in=11):
        # Build the figure.  Tight black background, no axis frames.
        # Window is taller than before (11 in) to fit the stopwatch
        # section under the clock without shrinking the clock itself.
        self.fig = plt.figure(figsize=(width_in, height_in),
                              facecolor="black")
        self.fig.canvas.manager.set_window_title("Session Timestamp")

        # ── Wall clock (top, biggest text) ─────────────────────
        # The Axes object has no ticks or labels — it's a text canvas.
        self.ax_clock = self.fig.add_axes([0.0, 0.72, 1.0, 0.22])
        self.ax_clock.set_facecolor("black")
        self.ax_clock.set_xticks([]); self.ax_clock.set_yticks([])
        for spine in self.ax_clock.spines.values():
            spine.set_visible(False)

        # Monospace so the digits don't shift horizontally as numbers
        # change — easier for OCR / human reading on video.
        self.clock_text = self.ax_clock.text(
            0.5, 0.5, "00:00:00.000",
            ha="center", va="center",
            fontsize=80, family="monospace",
            color="white", weight="bold",
            transform=self.ax_clock.transAxes
        )

        # ── Stopwatch display (under clock, medium text) ───────
        # Shows current stopwatch value.  Color indicates state:
        #   white   = stopped at 0 (idle)
        #   #00ff66 = running
        #   #ffcc44 = stopped but with elapsed time on the clock
        self.ax_sw = self.fig.add_axes([0.0, 0.55, 1.0, 0.15])
        self.ax_sw.set_facecolor("black")
        self.ax_sw.set_xticks([]); self.ax_sw.set_yticks([])
        for spine in self.ax_sw.spines.values():
            spine.set_visible(False)

        self.stopwatch_text = self.ax_sw.text(
            0.5, 0.5, " 00:00:00.000",
            ha="center", va="center",
            fontsize=55, family="monospace",
            color="#888888", weight="bold",
            transform=self.ax_sw.transAxes
        )

        # ── Stopwatch buttons row (Start / Stop / Reset / Lap) ──
        # Four buttons centered under the stopwatch display.  Width
        # of each is 0.16 fig units, with a small gap between.
        button_y = 0.46
        button_h = 0.06
        # Lay out the four buttons evenly between x=0.10 and x=0.90
        bw = 0.17
        gap = (0.80 - 4 * bw) / 3
        x = 0.10
        ax_sw_start = self.fig.add_axes([x, button_y, bw, button_h])
        x += bw + gap
        ax_sw_stop  = self.fig.add_axes([x, button_y, bw, button_h])
        x += bw + gap
        ax_sw_reset = self.fig.add_axes([x, button_y, bw, button_h])
        x += bw + gap
        ax_sw_lap   = self.fig.add_axes([x, button_y, bw, button_h])

        self.sw_start_btn = Button(ax_sw_start, "Start",
                                   color="#2d5a2d", hovercolor="#3d7a3d")
        self.sw_start_btn.label.set_color("white")
        self.sw_start_btn.label.set_fontsize(14)
        self.sw_start_btn.on_clicked(self._on_sw_start)

        self.sw_stop_btn = Button(ax_sw_stop, "Stop",
                                  color="#5a4a2d", hovercolor="#7a6a3d")
        self.sw_stop_btn.label.set_color("white")
        self.sw_stop_btn.label.set_fontsize(14)
        self.sw_stop_btn.on_clicked(self._on_sw_stop)

        self.sw_reset_btn = Button(ax_sw_reset, "Reset",
                                   color="#5a2d2d", hovercolor="#7a3d3d")
        self.sw_reset_btn.label.set_color("white")
        self.sw_reset_btn.label.set_fontsize(14)
        self.sw_reset_btn.on_clicked(self._on_sw_reset)

        self.sw_lap_btn = Button(ax_sw_lap, "Lap",
                                 color="#2d4a5a", hovercolor="#3d6a7a")
        self.sw_lap_btn.label.set_color("white")
        self.sw_lap_btn.label.set_fontsize(14)
        self.sw_lap_btn.on_clicked(self._on_sw_lap)

        # ── Recent laps display ────────────────────────────────
        # Shows up to 5 most recent laps, newest at top.
        # All laps stay in the events file regardless of what's
        # shown here.
        self.ax_laps = self.fig.add_axes([0.0, 0.27, 1.0, 0.17])
        self.ax_laps.set_facecolor("black")
        self.ax_laps.set_xticks([]); self.ax_laps.set_yticks([])
        for spine in self.ax_laps.spines.values():
            spine.set_visible(False)

        self.laps_text = self.ax_laps.text(
            0.5, 0.5, "(no laps yet)",
            ha="center", va="center",
            fontsize=14, family="monospace",
            color="#888888",
            transform=self.ax_laps.transAxes
        )

        # ── Session info line (status text) ────────────────────
        self.ax_info = self.fig.add_axes([0.0, 0.20, 1.0, 0.06])
        self.ax_info.set_facecolor("black")
        self.ax_info.set_xticks([]); self.ax_info.set_yticks([])
        for spine in self.ax_info.spines.values():
            spine.set_visible(False)

        self.info_text = self.ax_info.text(
            0.5, 0.5,
            "(no session — type a name below, then click 'Start Session')",
            ha="center", va="center",
            fontsize=18, family="monospace",
            color="#aaaaaa",
            transform=self.ax_info.transAxes
        )

        # ── Session controls row ───────────────────────────────
        ax_session_input = self.fig.add_axes([0.13, 0.10, 0.50, 0.06])
        self.session_input = TextBox(
            ax_session_input, "Session: ",
            initial="", color="#222222", hovercolor="#333333",
            textalignment="left"
        )
        self.session_input.label.set_color("white")
        self.session_input.label.set_fontsize(13)
        self.session_input.text_disp.set_color("white")
        self.session_input.text_disp.set_fontsize(13)
        self.session_input.on_submit(self._on_session_submit)

        ax_session_btn = self.fig.add_axes([0.66, 0.10, 0.21, 0.06])
        self.session_btn = Button(
            ax_session_btn, "Start Session",
            color="#2d5a2d", hovercolor="#3d7a3d"
        )
        self.session_btn.label.set_color("white")
        self.session_btn.label.set_fontsize(13)
        self.session_btn.on_clicked(self._on_session_button)

        # ── Notes controls row ─────────────────────────────────
        ax_note_input = self.fig.add_axes([0.13, 0.02, 0.50, 0.06])
        self.note_input = TextBox(
            ax_note_input, "Notes:   ",
            initial="", color="#222222", hovercolor="#333333",
            textalignment="left"
        )
        self.note_input.label.set_color("white")
        self.note_input.label.set_fontsize(13)
        self.note_input.text_disp.set_color("white")
        self.note_input.text_disp.set_fontsize(13)
        self.note_input.on_submit(self._on_note_submit)

        ax_note_btn = self.fig.add_axes([0.66, 0.02, 0.21, 0.06])
        self.note_btn = Button(
            ax_note_btn, "Add Note",
            color="#2d2d5a", hovercolor="#3d3d7a"
        )
        self.note_btn.label.set_color("white")
        self.note_btn.label.set_fontsize(13)
        self.note_btn.on_clicked(self._on_note_button)

        # ── State ──────────────────────────────────────────────
        self.logger = None        # EventLogger or None when no session
        self.session_start_ns = None
        self.stopwatch = Stopwatch()    # independent stop/start timer

        # Set up the animation that refreshes the clock & elapsed
        # text 30 times per second.
        interval_ms = int(1000 / DISPLAY_HZ)
        self.anim = animation.FuncAnimation(
            self.fig, self._tick,
            interval=interval_ms,
            blit=False,           # blit looks nice but textbox/button
                                  # widgets don't play well with blit
            cache_frame_data=False
        )

        # Hook window close so we finalize the events file cleanly
        self.fig.canvas.mpl_connect("close_event", self._on_close)

    # ── Tick: refresh clock + stopwatch + info on screen ─────────

    def _tick(self, _frame):
        now_ns = time.time_ns()
        self.clock_text.set_text(format_clock_ms(now_ns))

        # ── Stopwatch ──
        # Color cues: green while running, amber while paused with
        # nonzero time, gray when idle at 00:00:00.000.
        sw_seconds = self.stopwatch.elapsed_s()
        self.stopwatch_text.set_text(
            f" {format_stopwatch(sw_seconds)}"
        )
        if self.stopwatch.is_running:
            self.stopwatch_text.set_color("#00ff66")
        elif sw_seconds > 0:
            self.stopwatch_text.set_color("#ffcc44")
        else:
            self.stopwatch_text.set_color("#888888")

        # ── Laps panel ──
        # Show the most recent 5 laps, newest at top.
        laps = self.stopwatch.laps
        if not laps:
            self.laps_text.set_text("(no laps yet)")
            self.laps_text.set_color("#888888")
        else:
            recent = laps[-5:][::-1]   # last 5, reversed (newest first)
            lines = [
                f"  Lap {num}:  {format_stopwatch(t)}"
                for num, t in recent
            ]
            self.laps_text.set_text("Recent laps:\n" + "\n".join(lines))
            self.laps_text.set_color("#cccccc")

        # ── Session info line ──
        if self.logger is not None and self.session_start_ns is not None:
            elapsed_s = (now_ns - self.session_start_ns) / 1e9
            self.info_text.set_text(
                f"Session: {self.logger.session_name}    "
                f"Elapsed: {format_elapsed(elapsed_s)}"
            )
            self.info_text.set_color("#00ff66")    # green = recording
        else:
            # Hint differs based on whether the user has typed
            # something into the session field yet.  If they have,
            # nudge them toward clicking the Start button; if not,
            # remind them to type a name first.
            typed = self.session_input.text.strip()
            if typed:
                self.info_text.set_text(
                    f"Ready: '{typed}'  —  click 'Start Session' to begin recording"
                )
                self.info_text.set_color("#ffcc44")    # amber = ready but not started
            else:
                self.info_text.set_text(
                    "(no session — type a name below, then click 'Start Session')"
                )
                self.info_text.set_color("#aaaaaa")

        return [self.clock_text, self.stopwatch_text,
                self.laps_text, self.info_text]

    # ── Session start / end handlers ─────────────────────────────

    def _start_session(self, name):
        name = name.strip()
        if not name:
            print("  Session name cannot be empty")
            return

        if self.logger is not None:
            # User started a new session without ending the old one.
            # Close the previous one first so its events file is
            # finalized cleanly.
            print(f"  (auto-closing previous session "
                  f"'{self.logger.session_name}')")
            self.logger.close()

        self.logger = EventLogger(name)
        self.session_start_ns = self.logger.start_ns
        # Swap button label to "End Session"
        self.session_btn.label.set_text("End Session")
        # Recolor the button to red so it's visually clear that
        # clicking it stops the recording
        self.session_btn.color = "#5a2d2d"
        self.session_btn.hovercolor = "#7a3d3d"
        self.session_btn.ax.set_facecolor(self.session_btn.color)

    def _end_session(self):
        if self.logger is None:
            return
        self.logger.close()
        self.logger = None
        self.session_start_ns = None
        # Restore button to green "Start Session"
        self.session_btn.label.set_text("Start Session")
        self.session_btn.color = "#2d5a2d"
        self.session_btn.hovercolor = "#3d7a3d"
        self.session_btn.ax.set_facecolor(self.session_btn.color)
        # Clear the session name input field for the next session
        self.session_input.set_val("")

    # ── Widget callbacks ────────────────────────────────────────

    def _on_session_submit(self, text):
        """
        User pressed Enter inside the session name field.

        IMPORTANT: pressing Enter here does NOT start the session.
        It just commits the typed text into the field.  The session
        only starts when the user clicks the "Start Session" button.

        This is deliberate: typing a name and pressing Enter is easy
        to do reflexively, and we don't want to start a recording
        before the researcher has had a chance to verify the name.

        If a session is already running and the user types a new name
        in this field and presses Enter, we log it as a re-label note
        (no file rename — the original session continues recording).
        """
        if self.logger is not None:
            new_name = text.strip()
            if new_name and new_name != self.logger.session_name:
                self.logger.note(
                    f"(session re-labeled in UI to '{new_name}'; "
                    f"file name unchanged: {self.logger.session_name})"
                )
        # If no session is running: do nothing.  Wait for the button.

    def _on_session_button(self, _event):
        """Start Session / End Session button clicked."""
        if self.logger is None:
            self._start_session(self.session_input.text)
        else:
            self._end_session()

    def _on_note_submit(self, text):
        """User pressed Enter inside the notes field."""
        self._add_note(text)

    def _on_note_button(self, _event):
        """Add Note button clicked."""
        self._add_note(self.note_input.text)

    def _add_note(self, text):
        text = text.strip()
        if not text:
            return
        if self.logger is None:
            print("   No active session — start one first")
            return
        self.logger.note(text)
        self.note_input.set_val("")    # clear the field

    # ── Stopwatch button handlers ───────────────────────────────
    # Each handler also logs the action to the events file when a
    # session is active.  When no session is running, the stopwatch
    # still works — it just doesn't write anything to disk.

    def _on_sw_start(self, _event):
        """Start (or resume) the stopwatch."""
        started = self.stopwatch.start()
        if not started:
            return    # already running, ignore
        if self.logger is not None:
            self.logger.note(
                f"(stopwatch started at "
                f"{format_stopwatch(self.stopwatch.elapsed_s())})"
            )
        print("stopwatch started")

    def _on_sw_stop(self, _event):
        """Pause the stopwatch — freezes elapsed time."""
        # Capture the current time BEFORE stopping so we log the
        # exact value the user saw at the moment of the click.
        elapsed = self.stopwatch.elapsed_s()
        stopped = self.stopwatch.stop()
        if not stopped:
            return    # already stopped, ignore
        if self.logger is not None:
            self.logger.note(
                f"(stopwatch stopped at {format_stopwatch(elapsed)})"
            )
        print(f"stopwatch stopped at {format_stopwatch(elapsed)}")

    def _on_sw_reset(self, _event):
        """Reset stopwatch to 00:00:00.000 and clear lap history."""
        prior_elapsed = self.stopwatch.elapsed_s()
        had_laps = bool(self.stopwatch.laps)
        self.stopwatch.reset()
        # Only log if something actually changed — avoid spamming
        # the events file with no-op resets.
        if (prior_elapsed > 0 or had_laps) and self.logger is not None:
            self.logger.note(
                f"(stopwatch reset; prior elapsed "
                f"{format_stopwatch(prior_elapsed)})"
            )
        print("stopwatch reset")

    def _on_sw_lap(self, _event):
        """Record a lap with the current stopwatch value."""
        result = self.stopwatch.lap()
        if result is None:
            # Stopwatch wasn't running — refuse silently except for
            # a console message so the researcher can see why nothing
            # happened.
            print("  ✗ cannot lap: stopwatch is not running")
            return
        lap_num, elapsed = result
        text = f"Lap {lap_num} - stopwatch {format_stopwatch(elapsed)}"
        if self.logger is not None:
            # Write as a typed lap event for easier post-processing.
            # Using the EventLogger's private writer so it's tagged
            # with event_type='lap' rather than 'note'.
            self.logger._write("lap", text)
        print(f"{text}")

    def _on_close(self, _event):
        """Window closed — finalize any active session."""
        if self.logger is not None:
            self._end_session()

    # ── Run ─────────────────────────────────────────────────────

    def run(self):
        plt.show()


# ─────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("SESSION TIMESTAMP DISPLAY")
    print("=" * 60)
    print("""
The window shows a large millisecond clock for camera recording.

Usage:
  1. Type a session name in the 'Session' field
     (pressing Enter only commits the text — it does NOT start
      the session, so you can verify the name first)
  2. Click 'Start Session' to begin recording
  3. During the experiment, type notes in the 'Notes' field and
     press Enter to log them with a timestamp
  4. Click 'End Session' or close the window to finalize

Stopwatch (independent of session):
    Start  — begin (or resume) ticking
    Stop   — pause ticking (elapsed time stays frozen on screen)
    Reset  — return to 00:00:00.000 and clear lap history
    Lap    — record current value as a lap (running stopwatch only)

  Stopwatch events are logged to the events CSV when a session is
  active; otherwise the stopwatch works as a standalone timer.

Events are saved to:
  sessions/YYYY-MM-DD_HHMMSS_<name>_events.csv

For multimodal sync with the tracker session CSV, run this in a
separate terminal alongside:
  python main.py print --log <same_session_name>
""")
    TimestampDisplay().run()


if __name__ == "__main__":
    main()

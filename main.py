# ─────────────────────────────────────────────────────────────────
# main.py   (Phase 2 — adds auto-relocalize on startup + manual mode)
#
# PURPOSE:
#   Entry point for the Vive Room Tracker.
#   Handles six modes selected via command line argument:
#
#     identify    →  detect which tracker serial = left or right wrist
#     calibrate   →  run room_calibration.py to define (0,0,0)
#     verify      →  check current calibration against reference (no fix)
#     relocalize  →  NEW: smart drift detection + automatic correction
#     print       →  terminal coordinate output (auto-relocalize first)
#     view        →  live 3D visualization  (auto-relocalize first)
#
# WHAT'S NEW IN PHASE 2:
#   - 'relocalize' mode: tiered drift handling with auto-correction
#   - 'print' and 'view' modes now offer an auto-relocalize check at
#     startup (can be skipped with --skip-relocalize flag)
#   - Verify is unchanged — it's still useful as a "check without
#     touching anything" diagnostic
#
# USAGE:
#   python main.py identify
#   python main.py calibrate
#   python main.py verify
#   python main.py relocalize
#   python main.py print
#   python main.py print --skip-relocalize
#   python main.py view -f 30
# ─────────────────────────────────────────────────────────────────

import argparse     # standard library for parsing command line arguments
import sys          # for sys.exit on unrecoverable errors
import time         # for the 2-second startup delay after OpenVR init
import openvr       # SteamVR Python bindings

# Import our custom modules (must be in the same folder)
from room_calibration import (
    RoomCalibration,
    RoomCalibrator,
    smart_relocalize,
    sample_one_tracker,
)
from tracker_output import TrackerOutput, identify_wrists
from session_logger import SessionLogger


# ── Argument Parser ───────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Vive Room Tracker — outputs tracker positions in room coordinates",
        formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument(
        "mode",
        choices=["identify", "calibrate", "verify",
                 "relocalize", "print", "view"],
        nargs="?",
        default="print",
        help=(
            "identify   : detect which serial = left or right wrist\n"
            "calibrate  : define room origin and axes (full 3-point)\n"
            "verify     : check calibration drift (no changes made)\n"
            "relocalize : check drift AND auto-correct if possible\n"
            "print      : live terminal coordinate output (default)\n"
            "view       : live 3D matplotlib visualization"
        )
    )

    parser.add_argument(
        "-f", "--frequency",
        type=float,
        default=60.0,
        help="Update frequency in Hz (print and view modes). Default: 60"
    )

    # NEW: opt-out flag for the auto-relocalize step.  Useful when:
    #   - you trust the calibration and want fastest startup
    #   - you don't have a tracker handy to place on Point A
    #   - you're scripting and don't want interactive prompts
    parser.add_argument(
        "--skip-relocalize",
        action="store_true",
        help="Skip the auto-relocalize check at startup of print/view"
    )

    # NEW: enable session logging.  Pass a session name like
    #   python main.py print --log reaching_trial_01
    # Logs are written to sessions/YYYY-MM-DD_HHMMSS_<name>.csv
    # plus a companion .meta.json file with calibration info.
    parser.add_argument(
        "--log",
        type=str,
        default=None,
        metavar="SESSION_NAME",
        help="Record tracker stream to sessions/<timestamp>_<name>.csv"
    )

    # NEW: choose calibration method.  Only relevant for 'calibrate' mode.
    #   "gram-schmidt" — flexible 3-point (default; fast)
    #   "svd"          — multi-point Kabsch (more robust; slower)
    parser.add_argument(
        "--method",
        type=str,
        choices=["gram-schmidt", "svd"],
        default="gram-schmidt",
        help="Calibration method (calibrate mode only). Default: gram-schmidt"
    )

    return parser.parse_args()


# ── OpenVR Initializer ────────────────────────────────────────────

def init_vr():
    """
    Initialize the OpenVR runtime and return the VR system object.
    Exits the program if SteamVR is not running.
    """
    try:
        vr = openvr.init(openvr.VRApplication_Background)
        print("✓ OpenVR initialized successfully")
    except openvr.OpenVRError as e:
        print(f"✗ OpenVR initialization failed: {e}")
        print("  Make sure SteamVR is open and running.")
        sys.exit(1)

    # Give SteamVR 2 seconds to register all connected devices.
    print("  Waiting 2s for devices to register...")
    time.sleep(2)

    return vr


# ── Shared: Load calibration or exit ──────────────────────────────

def load_calibration_or_exit():
    """
    Helper used by verify, relocalize, print, and view modes.
    Loads room_calibration.json; if missing, prints a helpful
    message and exits.
    """
    calib = RoomCalibration.load()
    if not calib:
        print("✗ No room calibration file found.")
        print("  Run first: python main.py calibrate")
        openvr.shutdown()
        sys.exit(1)
    return calib


# ── Shared: Build a SessionLogger with calibration metadata ──────

def build_session_logger(session_name, calib, frequency, mode_label):
    """
    Construct a SessionLogger with metadata about the calibration
    and runtime context.  Putting this in the meta JSON file makes
    the recorded CSV self-describing — downstream analysis can know
    which calibration was active when the data was recorded.
    """
    metadata = {
        "mode":              mode_label,        # "print" or "view"
        "frequency_hz":      frequency,
        "trackers":          [],                # filled in by caller after discovery
        "calibration": {
            "has_reference": calib.has_reference(),
            "origin_sv":     calib.origin.tolist(),
        },
    }
    if calib.has_reference():
        metadata["calibration"]["reference_sv"]   = calib.reference_sv.tolist()
        metadata["calibration"]["reference_room"] = calib.reference_room.tolist()

    return SessionLogger(session_name, metadata=metadata)


# ── Shared: Auto-relocalize on startup of print/view ─────────────

def maybe_auto_relocalize(vr, calib, skip):
    """
    Optional drift check at the start of print/view modes.
    Returns the (possibly updated) calibration, or None if drift
    is too large and the user should run full recalibration.

    Behavior:
      - If `skip` is True or calibration has no reference, do nothing
      - Otherwise prompt user to place tracker on Point A, then run
        smart_relocalize() which may auto-correct moderate drift
      - User can press Ctrl+C during the prompt to skip
    """
    if skip:
        print("  Skipping auto-relocalize (--skip-relocalize set)")
        return calib

    if not calib.has_reference():
        print("  Skipping auto-relocalize (calibration has no reference)")
        return calib

    print("\n  Auto-relocalize check available at startup.")
    print("  Place tracker on Point A and press Enter to verify drift,")
    print("  or press Ctrl+C to skip and use the stored calibration as-is.")

    try:
        input("  > ")
    except KeyboardInterrupt:
        print("\n  Skipping relocalize — using stored calibration.")
        return calib

    # interactive=False here because we already prompted above
    # (and we don't want a second "press Enter" prompt inside
    # smart_relocalize itself)
    updated = smart_relocalize(vr, calib,
                                sample_duration=2.0,
                                interactive=False)

    if updated is None:
        # Drift was too large for auto-correct
        return None

    return updated


# ── Mode: identify ────────────────────────────────────────────────

def mode_identify(vr):
    """
    Run the wrist identification wizard.
    Prints lines to paste into SERIAL_TO_LABEL in tracker_output.py.
    """
    identify_wrists(vr)
    openvr.shutdown()
    print("\n✓ Done — paste the printed lines into tracker_output.py")


# ── Mode: calibrate ───────────────────────────────────────────────

def mode_calibrate(vr, method):
    """
    Run the interactive room calibration wizard.
    Overwrites any existing room_calibration.json and audit file.

    Args:
      method : "gram-schmidt" (3 flexible points) or "svd" (N points)
    """
    calibrator = RoomCalibrator(vr)

    if method == "gram-schmidt":
        result = calibrator.run_gram_schmidt()
    elif method == "svd":
        result = calibrator.run_svd()
    else:
        # Should never happen given argparse choices, but defensive.
        print(f"✗ Unknown calibration method: {method}")
        result = None

    openvr.shutdown()

    if result:
        print(f"✓ Calibration saved ({method}). "
              f"Run 'python main.py print' next.")
    else:
        print("✗ Calibration was cancelled or failed.")


# ── Mode: verify ──────────────────────────────────────────────────

def mode_verify(vr):
    """
    Check drift WITHOUT modifying calibration.  Use this as a
    diagnostic when you want to see the current drift without
    auto-correcting it.

    For "check and fix" behavior, use 'relocalize' mode instead.
    """
    calib = load_calibration_or_exit()

    if not calib.has_reference():
        print("✗ This calibration has no reference point stored.")
        print("  It was created before the verify feature was added.")
        print("  Run: python main.py calibrate")
        openvr.shutdown()
        sys.exit(1)

    print("\n" + "=" * 55)
    print("CALIBRATION VERIFICATION  (diagnostic, no changes)")
    print("=" * 55)
    print("""
This will measure the current calibration drift WITHOUT changing
anything.  For automatic correction, use:  python main.py relocalize

  Place a tracker on Point A and hold it still for ~2 seconds.
""")
    input("Place tracker on Point A, then press Enter...")

    try:
        fresh_sv, _ = sample_one_tracker(
            vr, duration=2.0, label="Point A — verification"
        )
    except RuntimeError as e:
        ...

    result = calib.verify(fresh_sv)

    expected = result["expected"]
    measured = result["measured"]
    delta    = result["delta"]
    drift_cm = result["drift_m"] * 100

    print(f"\n{'─' * 55}")
    print(f"  Expected (room) : "
          f"({expected[0]:+.4f}, {expected[1]:+.4f}, {expected[2]:+.4f}) m")
    print(f"  Measured (room) : "
          f"({measured[0]:+.4f}, {measured[1]:+.4f}, {measured[2]:+.4f}) m")
    print(f"  Per-axis delta  : "
          f"({delta[0]:+.4f}, {delta[1]:+.4f}, {delta[2]:+.4f}) m")
    print(f"  Drift magnitude : {drift_cm:.2f} cm")
    print(f"{'─' * 55}")

    status = result["status"]
    if status == "ok":
        print("  ✓ Calibration is still accurate.")
    elif status == "warn":
        print("  ⚠  Moderate drift detected.")
        print("     Run: python main.py relocalize    (auto-correct)")
        print("     Or:  python main.py calibrate     (full recalibrate)")
    elif status == "fail":
        print("  ✗ Significant drift detected.")
        print("     Likely cause: base station moved or axes rotated.")
        print("     Run: python main.py calibrate")
    print()

    openvr.shutdown()


# ── Mode: relocalize ──────────────────────────────────────────────

def mode_relocalize(vr):
    """
    Smart drift detection + automatic correction.

    Tiered behavior:
      < 2 cm    → leave calibration alone
      2–10 cm   → auto-correct translation, verify, save
      ≥ 10 cm   → reject as too large, prompt for full recalibration

    This is the recommended startup operation after SteamVR restart,
    computer wake-from-sleep, or any tracker reconnect.
    """
    calib = load_calibration_or_exit()

    updated = smart_relocalize(vr, calib,
                                sample_duration=2.0,
                                interactive=True)

    if updated is None:
        print("Recalibration required.")
    else:
        print("✓ Relocalize complete.")

    openvr.shutdown()


# ── Mode: print ───────────────────────────────────────────────────

def mode_print(vr, frequency, skip_relocalize, log_session):
    """
    Live terminal coordinate output.
    Offers an auto-relocalize check before starting (skippable).
    If log_session is non-None, also records data to a CSV file.
    """
    calib = load_calibration_or_exit()

    # Auto-relocalize unless skipped
    calib = maybe_auto_relocalize(vr, calib, skip=skip_relocalize)
    if calib is None:
        print("Cannot start — recalibration required.")
        print("Run: python main.py calibrate")
        openvr.shutdown()
        sys.exit(1)

    # Create the output handler — discovers all trackers
    output = TrackerOutput(vr, calib)

    # Optionally start session logging.  We use a `with` block so
    # the file gets closed cleanly even if the user hits Ctrl+C
    # in the middle of the streaming loop.
    logger = None
    if log_session:
        logger = build_session_logger(log_session, calib, frequency, "print")
        # Stash tracker labels in the metadata so the meta.json
        # records which labels are present in the CSV.
        logger.metadata["trackers"] = list(output.tracker_labels.values())

    try:
        if logger is not None:
            with logger as L:
                output.run_terminal(frequency=frequency, logger=L)
        else:
            output.run_terminal(frequency=frequency)
    finally:
        openvr.shutdown()
        print("✓ OpenVR shut down cleanly")


# ── Mode: view ────────────────────────────────────────────────────

def mode_view(vr, frequency, skip_relocalize, log_session):
    """
    Live 3D matplotlib visualization of tracker positions.
    Offers an auto-relocalize check before starting (skippable).
    If log_session is non-None, also records data to a CSV file.
    """
    calib = load_calibration_or_exit()

    calib = maybe_auto_relocalize(vr, calib, skip=skip_relocalize)
    if calib is None:
        print("Cannot start — recalibration required.")
        print("Run: python main.py calibrate")
        openvr.shutdown()
        sys.exit(1)

    output = TrackerOutput(vr, calib)

    logger = None
    if log_session:
        logger = build_session_logger(log_session, calib, frequency, "view")
        logger.metadata["trackers"] = list(output.tracker_labels.values())

    try:
        if logger is not None:
            with logger as L:
                output.run_3d(frequency=frequency, logger=L)
        else:
            output.run_3d(frequency=frequency)
    finally:
        openvr.shutdown()
        print("✓ OpenVR shut down cleanly")


# ── Entry Point ───────────────────────────────────────────────────

def main():
    args = parse_args()

    print(f"\n=== Vive Room Tracker  |  mode: {args.mode} ===\n")

    vr = init_vr()

    # Route to the correct mode function
    if args.mode == "identify":
        mode_identify(vr)

    elif args.mode == "calibrate":
        mode_calibrate(vr, method=args.method)

    elif args.mode == "verify":
        mode_verify(vr)

    elif args.mode == "relocalize":
        mode_relocalize(vr)

    elif args.mode == "print":
        mode_print(vr,
                   frequency=args.frequency,
                   skip_relocalize=args.skip_relocalize,
                   log_session=args.log)

    elif args.mode == "view":
        mode_view(vr,
                  frequency=args.frequency,
                  skip_relocalize=args.skip_relocalize,
                  log_session=args.log)


if __name__ == "__main__":
    main()

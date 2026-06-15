### what steam vr does
1. SteamVR process starts
2. It loads its saved configuration (knows about base stations from previous sessions)
3. It listens for radio signals from any dongles plugged into USB
4. It listens for IR sweeps from base stations
5. As base stations come online, it identifies them by their broadcast data
6. As trackers connect to dongles via radio, it learns they exist
7. Each tracker, once it sees base station sweeps, computes its own position
8. The tracker radios that position back through its dongle
9. SteamVR receives the position and adds the tracker to its device list
10. SteamVR's "world frame" stabilizes based on observed base station positions

Thus, SteamVR must be running with the null headset driver enabled; Both base stations must be operational;  The tracker must be visible to at least one base station, for one cannot reach, the other one will have to work


### actual working proccess
Hardware level:
  Base stations powered on  ─┐
                              ├─→ visible IR sweeps
  Base stations spun up     ─┘

SteamVR level:
  SteamVR running              ┐
  Null driver enabled          │
  Trackers paired to dongles   ├─→ trackers can report positions
  Dongles plugged in           │
  Room Setup completed once  ─┘   (so SteamVR's "ready" check passes)

Code level:
  openvr Python package        ┐
  OpenVR initialized           ├─→ Python can read tracker positions
  Tracker placed at Point A  ─┘

Result:
  python main.py calibrate  →  reads tracker pose at Point A
                                ↓
                              same for B, C
                                ↓
                              computes transform
                                ↓
                              saves room_calibration.json


### new feature
adding timestamp (precision 0.001s)

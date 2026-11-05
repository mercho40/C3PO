# Hardware support tickets: drafts to send

Two faults that no software change fixes. Send these to Unitree support (or the seller), copying Livox for the lidar.
Attach `journalctl` excerpts and photos where noted. Fill in the purchase details in `<…>` before sending.

---

## 1. G1 EDU: Mid-360S lidar, scan module error HMS 0x02100003

**To:** Unitree support (cc: Livox support) · **Subject:** G1 EDU Mid-360S scan module fault, HMS 0x02100003, SN ARMCNAF0030677

Hello,

The Mid-360S lidar in our G1 EDU (robot `<robot SN / order number>`) reports a scan module fault and produces no point cloud.

- **Sensor:** Livox Mid-360S, `dev_type 35` (`kLivoxLidarTypeMid360s`), SN **ARMCNAF0030677**, at `192.168.123.120`.
- **Symptom:** after configuration the sensor reports `work_state 4` (Error) and HMS **`0x02100003`**, at about 65.6 °C internal temperature. The Livox HMS table defines ID `0x0210` at level 3 as "Scan module is abnormal, the system is trying to recover … try restarting the device." It sends no points by any route.
- **What we tried:**
  1. Livox-SDK2 v1.3.1 with a `Mid360s` config section. The sensor is discovered and configured (data type, pattern, attitude, work mode Normal, IMU), then enters the error state.
  2. The control board's own `lidar_driver`. It drops the sensor because its SDK predates type 35, so `rt/utlidar/cloud_livox_mid360` never publishes.
  3. A cold power cycle (battery swap). The fault persists.
  4. Nothing is touching the dome, and the motor makes no sound.
- **First seen:** 2026-11-03, and still present on 2026-11-05.

We plan to read the firmware with Livox Viewer 2 v2.5.9 and update to 35.01.0113. If the fault persists after that, we request a repair or replacement (part G1-MID360L). Please tell us whether you need the sensor sent alone or the whole robot, and any logs you want first.

Thank you,
`<name, ORT Uruguay, contact>`

---

## 2. G1 EDU: BrainCo hands not responding on any serial line

**To:** Unitree support (cc: BrainCo, if the hands were sold through them) · **Subject:** G1 EDU BrainCo dexterous hands do not respond on any RS485 port

Hello,

Neither BrainCo hand on our G1 EDU (robot `<robot SN / order number>`) responds.

- **Hardware:** the hands sit behind the FTDI FT4232H (`0403:6011`), which shows up as `/dev/ttyUSB0`–`3`.
- **Before:** on an earlier session the vendor `brainco_hand_server` (from `xr_teleoperate/vendor/brainco_hand_service`) bound the **right** hand on `/dev/ttyUSB1`: hardware_type 6, sku_type 1 (medium right), firmware **1.0.22.U**, serial **BCXTR2124J2600024**, Modbus RTU slave 0x7f at 460800 baud. The **left** hand never answered on any port ("Failed to get device info").
- **Now** (2026-11-03 and 2026-11-05): neither hand answers on any of the 4 lines, through our driver or the vendor `brainco_hand_server`.
- **Not yet checked:** hand power, and the wrist connectors. Please tell us the safe way to inspect them.

We need to know:

1. How to check hand power and the wrist cable from the robot side.
2. Whether a firmware or service update is needed.
3. The repair process if a hand or the RS485 adapter is faulty.

Thank you,
`<name, ORT Uruguay, contact>`

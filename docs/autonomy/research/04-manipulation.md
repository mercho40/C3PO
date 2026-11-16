# 04 — Manipulation on the Unitree G1 EDU (29 DoF) + two BrainCo Revo2 hands

_Research snapshot: 2026-11-02. Target robot: G1 EDU, 29 DoF, two BrainCo dexterous hands, RealSense D435i head camera, Jetson Orin NX 16 GB on JetPack 5. Arms currently run only the stock arm-action table through the high-level API._

**Confidence key:** **[V]** means I checked it against the primary repo or source code myself. **[S]** means it comes from a secondary source (a mirror, a blog, or a retailer), so check it before relying on it.

---

## 0. TL;DR

1. **Nothing in 2026 lets a G1 do _anything_ zero-shot.** The best open systems pair a language-model planner with a library of skills that are either trained or computed. Stanford's HomeBody (G1, D435i head camera, analytic grasps, LLM planner, Apache-2.0, real-robot code released 2026-10-26) is the closest open reference design ([site](https://tml.stanford.edu/homebody/), [repo](https://github.com/Stanford-TML/homebody)) **[V]**. Learned vision-language-action models (VLAs) fine-tuned per task family reach about 50–90% on trained tasks ([OpenHLM](https://openhlm-project.github.io/), [CloudWalk GR00T G1](https://huggingface.co/cloudwalk-research/GR00T-N1.6-G1-PnPAppleToPlate)).
2. **BrainCo hands are well supported in Unitree's own stack.** Support covers `xr_teleoperate --ee=brainco`, `brainco_hand_service` (serial to DDS), and `unitree_lerobot` with robot type `Unitree_G1_Brainco` ([xr_teleoperate](https://github.com/unitreerobotics/xr_teleoperate), [brainco_hand_service](https://github.com/unitreerobotics/brainco_hand_service), [unitree_lerobot](https://github.com/unitreerobotics/unitree_lerobot)) **[V]**. This is the shortest path to the first learned skill.
3. **The NVIDIA whole-body stack is the strongest long-term path, but it does not fit this robot yet.** That stack is GEAR-SONIC plus GR00T N1.7. Its action space assumes 7-DoF hands, while the Revo2 has 6 actuators ([VLA workflow](https://nvlabs.github.io/GR00T-WholeBodyControl/tutorials/vla_workflow.html)). Onboard deployment also needs the G1's Orin reflashed to JetPack 6 ([install docs](https://nvlabs.github.io/GR00T-WholeBodyControl/getting_started/installation_deploy.html)). Treat it as a phase-3 target.
4. **First task: "pick the cup from the table and place it / hand it over"**, with the robot standing still. Add two wrist cameras. Unitree publishes printable BrainCo-hand wrist-camera brackets ([Device.md](https://github.com/unitreerobotics/xr_teleoperate/blob/main/Device.md)) **[V]**. Collect 50–100 teleop demos per variant, train ACT as a baseline and π0.5 on the H100, and run inference off-board over wired Ethernet first.

---

## 1. Low-level arm control on the G1

### 1.1 Control channels

| Channel                                          | What it does                                                                                                 | Locomotion still running?       |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------ | ------------------------------- |
| High-level arm action client (current c3po path) | Plays preset gestures                                                                                        | Yes                             |
| **`rt/arm_sdk`** (LowCmd\_ msg)                  | Joint-level PD targets for the arms and waist, blended into Unitree's own locomotion controller              | **Yes**, in Regular mode (R1+X) |
| `rt/lowcmd` (debug mode)                         | Full low-level control of all 29 motors. Stock locomotion is off, so you must run your own RL walking policy | No (you replace it)             |

- **The blend weight is a special motor slot.** In `rt/arm_sdk` mode, Unitree's `xr_teleoperate` writes the blend weight into `motor_cmd[kNotUsedJoint0].q`, which is index 29. It ramps that value linearly from 1 to 0 over 101 steps when sending the arms home ([robot_arm.py](https://github.com/unitreerobotics/xr_teleoperate/blob/main/teleop/robot_control/robot_arm.py)) **[V]**. The official C++ examples are `g1_arm5_sdk_dds_example` and `g1_arm7_sdk_dds_example` ([DeepWiki summary of unitree_sdk2](https://deepwiki.com/unitreerobotics/unitree_sdk2/12.3-arm-and-hand-examples)) **[S]**.
- **Gains and velocity clipping.** `xr_teleoperate` uses kp 300 / kd 3 on the strong joints, kp 80 / kd 3 on the shoulders and elbows (its "weak" motors), and kp 40 / kd 1.5 on the wrists. Each target is clipped so the arm moves at most `velocity_limit * dt` per tick (`clip_arm_q_target`) ([robot_arm.py](https://github.com/unitreerobotics/xr_teleoperate/blob/main/teleop/robot_control/robot_arm.py)) **[V]**. The default `velocity_limit=30.0` is permissive, so set it much lower near people.
- **Mode support.** `--motion` mode (arms through `arm_sdk` while the stock controller walks) works **only in Regular mode (R1+X), not Running mode (R2+A)** ([xr_teleoperate README](https://github.com/unitreerobotics/xr_teleoperate)) **[V]**. A community wiki reports that with weight 1.0 in Running mode the G1 stops following velocity commands ([RoboticsKnowledgebase mirror](https://vat.pages.dev/RoboticsKnowledgebase/roboticsknowledgebase.github.io/blob/e25f04f00ad0e76d908d3c07dc21e95907be312e/wiki/common-platforms/unitree-g1.md)) **[S]**.
- **Stock arm control fights `/arm_sdk`.** BrainCo's G1 integration disables Unitree's built-in arm control at startup to avoid conflicts with `/arm_sdk` (2026-06-29 changelog) ([BrainCoTech/unitree-g1-brainco-hand](https://github.com/BrainCoTech/unitree-g1-brainco-hand)) **[V]**. c3po's arm-action path and any `arm_sdk` skill must be mutually exclusive.
- **No documented watchdog.** One adapter warns there is no public `arm_sdk` firmware watchdog: if your process dies at weight 1, the last target may keep being held. It also says you should ramp the weight while holding the measured pose ([inspect-robots-unitree-g1](https://pypi.org/project/inspect-robots-unitree-g1/)) **[S]**.
- **Payload.** The EDU arm carries about 3 kg, depending strongly on pose ([unitree.com/g1](https://www.unitree.com/mobile/g1)). That is plenty for a glass or cup, but not for heavy doors.

### 1.2 Inverse kinematics (IK)

- **Unitree's IK.** `robot_arm_ik.py` builds a reduced Pinocchio model with the legs and waist locked. It solves a CasADi/IPOPT problem with translation, rotation, regularization and smoothness costs for both end-effectors (`L_ee`/`R_ee`) ([robot_arm_ik.py](https://github.com/unitreerobotics/xr_teleoperate/blob/main/teleop/robot_control/robot_arm_ik.py)) **[V]**. **Reuse it.** It is the same IK the teleop data was recorded with, and LeRobot's G1 integration uses it too: install pinocchio from conda-forge for the CasADi bindings ([LeRobot G1 docs](https://huggingface.co/docs/lerobot/unitree_g1)) **[V]**.
- **Alternatives:**
  - [Pink](https://pypi.org/project/pin-pink/) does differential IK on Pinocchio and ships a G1 squat example ([releases](https://science.ecosyste.ms/projects/58533/releases)) **[S]**.
  - [mink](https://github.com/kevinzakka/mink) does differential IK on MuJoCo and is actively maintained (Apache-2.0) **[V]**.
- **MoveIt 2.** I found **no maintained G1 MoveIt 2 config**. You would have to build an SRDF and ros2_control bridge yourself ([MoveIt 2 setup pattern](https://automaticaddison.com/?p=16946)). For collision-aware motion, GPU [cuRobo](https://github.com/NVlabs/curobo) (Apache-2.0, active) is a better fit than MoveIt on a humanoid **[V]**. For a student team, Pinocchio IK plus simple self-collision and table checks with `hpp-fcl` (bundled with Pinocchio) is enough for table-top grasps.

### 1.3 Lower body while the upper body is commanded

| Option                                                                       | Notes                                                                                                                                                                                                                                                                                                | Open source / G1-ready                                                                   |
| ---------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Stock Unitree locomotion + `arm_sdk` (Regular mode)                          | Simplest. The locomotion policy does not know the arm pose, so balance degrades with extended arms or payloads ([RKB mirror](https://vat.pages.dev/RoboticsKnowledgebase/roboticsknowledgebase.github.io/blob/e25f04f00ad0e76d908d3c07dc21e95907be312e/wiki/common-platforms/unitree-g1.md)) **[S]** | Yes                                                                                      |
| GR00T Decoupled WBC (RL legs + IK arms)                                      | Used with GR00T N1.5/N1.6 (released 2025-11-12)                                                                                                                                                                                                                                                      | Yes ([GR00T-WholeBodyControl](https://github.com/NVlabs/GR00T-WholeBodyControl)) **[V]** |
| LeRobot G1 with `GrootLocomotionController` / `HolosomaLocomotionController` | Uses `rt/lowcmd` (full low-level), so stock locomotion is replaced ([unitree_g1.py](https://github.com/huggingface/lerobot/blob/main/src/lerobot/robots/unitree_g1/unitree_g1.py)) **[V]**                                                                                                           | Yes                                                                                      |
| HOMIE (RL legs robust to arm poses, $500 exoskeleton cockpit)                | Deployment code for G1 + Dex3 ([OpenHomie](https://github.com/InternRobotics/OpenHomie), [paper](https://arxiv.org/abs/2502.13013)) **[V]**                                                                                                                                                          | Yes                                                                                      |
| AMO (RL + trajectory optimisation, G1 29-DoF)                                | Code at [OpenTeleVision/AMO](https://github.com/OpenTeleVision/AMO) **[V]**. HomeBody uses AMO as its lower body ([HomeBody](https://tml.stanford.edu/homebody/))                                                                                                                                    | Yes                                                                                      |

Whole-body controllers that also drive the arms are covered in §5.

---

## 2. BrainCo hand integration

- **Hardware.** The Revo2 has 5 fingers and 11 joints, of which **6 are actuated** (thumb, thumb-aux, index, middle, ring, pinky). Positions and speeds are normalised to [0, 1] ([brainco_hand_service](https://github.com/unitreerobotics/brainco_hand_service)) **[V]**, and `brainco_Num_Motors = 6` in [robot_hand_brainco.py](https://github.com/unitreerobotics/xr_teleoperate/blob/main/teleop/robot_control/robot_hand_brainco.py) **[V]**. G1-specific left and right variants with mounts are sold through Unitree channels ([Scan listing](https://www.scan.co.uk/products/brainco-bionic-dexterous-right-revo-2-hand-for-the-g1-with-tactile-and-rgb)) **[S]**. Check whether yours is a "Touch" (tactile) unit.
- **Unitree driver (`brainco_hand_service`).** One USB-serial adapter per hand feeds a serial-to-DDS bridge. Topics are `rt/brainco/{left,right}/{cmd,state}`. Run `sudo ./brainco_hand_server --network eth0`, test with `test_brainco_hand_server [left|right]`, and optionally enable autostart ([repo](https://github.com/unitreerobotics/brainco_hand_service)) **[V]**.
- **Vendor SDK.** `bc_stark_sdk` on PyPI (v2.x) supports Revo2 Basic, Touch and Touch Pressure over Modbus-RTU (RS-485), CAN 2.0, CAN-FD and EtherCAT, with Linux ARM64 wheels. The licence is proprietary ([PyPI](https://pypi.org/project/bc_stark_sdk/)) **[S]**. Examples are in [BrainCoTech/stark-serialport-example](https://github.com/BrainCoTech/stark-serialport-example) **[V: repo exists]**.
- **BrainCo's G1 repo.** [BrainCoTech/unitree-g1-brainco-hand](https://github.com/BrainCoTech/unitree-g1-brainco-hand) (v2.0.0, 2026-06, updated 2026-10-27) provides:
  - a ROS 2 dual-arm and dual-hand workspace (`brainco_ws`), with arm IK adapted from `xr_teleoperate`;
  - `ros2_stark_ws`;
  - hand-eye calibration and simple motion demos, supporting both G1-23 and G1-29;
  - dual RS-485 ports (`/dev/ttyUSB*`). **[V]**
    It is a good source of ready-made "go to pose and grasp" demos. A third-party guide's `brainco/revo2_ros2_driver` repo **could not be verified** ([openelab guide](https://openelab.io/es/blogs/robotics/brainco-dexterous-hand-revo-2-development-guide)) **[S]**.
- **Retargeting and URDF.** `xr_teleoperate/assets/brainco_hand/` ships `brainco_left.urdf`, `brainco_right.urdf` and `brainco.yml`. The YAML is a dex-retargeting DexPilot config that maps human hand keypoints to the 6 target joints (`*_thumb_metacarpal_joint`, `*_thumb_proximal_joint`, `*_{index,middle,ring,pinky}_proximal_joint`), with `low_pass_alpha 0.2` ([brainco.yml](https://github.com/unitreerobotics/xr_teleoperate/blob/main/assets/brainco_hand/brainco.yml)) **[V]**. The retargeting library is [dex-retargeting](https://github.com/dexsuite/dex-retargeting) (MIT).
- **Grasp primitives to script first.** Use the 6 normalised joint targets:
  - **power/cylindrical** (all fingers close, thumb opposed) for cups and bottles;
  - **pinch** (thumb plus index) for small objects;
  - **hook** (four fingers curled, thumb open) for handles and bags.
    Detect grasp success from finger-position error and state current (if exposed by state), or from tactile readings on Touch units.
- **Sim gap.** `unitree_sim_isaaclab` ships Dex1, Dex3 and Inspire task scenes only, with **no BrainCo scenes** ([README](https://github.com/unitreerobotics/unitree_sim_isaaclab)) **[V]**. You would need to add the BrainCo URDF yourself. A community project retargeted a Dex3 GR00T N1.7 policy to Revo2 in Isaac Lab:
  - Retargeting alone scored 6% success.
  - Adding 2,647 Isaac Lab Mimic-generated demos raised it to 50% at ±5 cm spawn noise, and 0% at ±10 cm.
  - Results are simulation-only ([model card](https://huggingface.co/pashuparthis/dex3-brainco-retargeted-policy)) **[S]**.
    The lesson is that you cannot borrow Dex3 policies cheaply: collect BrainCo data.
- **Existing BrainCo datasets** (useful for co-training and format reference):
  - [n1robotics/G1_Brainco_StackCups_Dataset](https://huggingface.co/datasets/n1robotics/G1_Brainco_StackCups_Dataset): `Unitree_G1_Brainco`, 102 episodes **[S]**.
  - Several "G1 BrainCo pick drink / apple / tissues" sets of about 200 episodes ([claru listing](https://claru.ai/datasets/jeffrinsam-g1-brainco-pickdrink-dataset-2x24fps)) **[S]**.
  - Unitree's own G1 datasets carry no explicit licence ([roboticscenter](https://www.roboticscenter.ai/datasets/unitree-g1-locomotion/)) **[S]**.
  - RoboTacDex (G1 + dual tactile BrainCo, VR teleop) ([arXiv 2606.31836](https://arxiv.org/pdf/2606.31836)) **[S]**.

---

## 3. Data collection

| System                                                                                    | Device                                                | BrainCo?                                                 | Whole body?                                                   | Notes                                                                                                            |
| ----------------------------------------------------------------------------------------- | ----------------------------------------------------- | -------------------------------------------------------- | ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| **[xr_teleoperate](https://github.com/unitreerobotics/xr_teleoperate)** v1.6 (2026-07-29) | Apple Vision Pro, PICO 4 Ultra Enterprise, Quest 3/3S | **Yes**, `--ee=brainco` (controller input added in v1.6) | Arms + hands; `--motion` adds joystick walking (Regular mode) | `--record`, 30 Hz, JSON + images, then `unitree_lerobot`. `--sim` works with `unitree_sim_isaaclab` **[V]**      |
| [avp_teleoperate](https://github.com/unitreerobotics/xr_teleoperate)                      | AVP                                                   | Old name, redirects to xr_teleoperate                    |                                                               |                                                                                                                  |
| [GR00T-WBC / SONIC teleop](https://github.com/NVlabs/GR00T-WholeBodyControl)              | PICO + IsaacTeleop 1.4                                | Not documented (7-D hands)                               | **Yes**                                                       | Feeds the GR00T N1.7 VLA workflow **[V]**                                                                        |
| [TWIST2](https://github.com/amazon-far/TWIST2)                                            | PICO + 2 motion trackers + 2-DoF neck add-on          | No                                                       | **Yes**                                                       | MIT, ONNX checkpoint shipped ([paper](https://arxiv.org/abs/2511.02832)) **[V]**                                 |
| [HOMIE](https://github.com/InternRobotics/OpenHomie)                                      | $500 exoskeleton + gloves                             | Dex3 deploy                                              | Yes                                                           | **[V]**                                                                                                          |
| [LeRobot G1 + Homunculus exo](https://huggingface.co/docs/lerobot/unitree_g1)             | 7-DoF exoskeleton / joystick                          | Not documented                                           | Yes (RL loco)                                                 | `lerobot-record` writes LeRobot format directly **[V]**                                                          |
| OpenHLM joint-based mocap teleop                                                          | portable mocap rig                                    | No                                                       | Yes                                                           | Beat VR 3-point and decoupled teleop on whole-body tasks ([project](https://openhlm-project.github.io/)) **[S]** |

**Recommendation:** use `xr_teleoperate --arm=G1_29 --ee=brainco --record` with a Quest 3 (cheapest) or PICO. Record **standing still** (no `--motion`) for phase 1.

### 3.1 Cameras

- **Head only is a real handicap.** At grasp time the D435i sees the hand from above and behind, so finger contact is occluded. Unitree's BOM offers two wrist-camera options ([Device.md](https://github.com/unitreerobotics/xr_teleoperate/blob/main/Device.md)) **[V]**:
  - two RealSense D405 cameras on Dex3/Dex1 brackets;
  - two 160°, 60 FPS monocular wrist cameras, with **printable brackets for Inspire DFX and BrainCo hands** and a USB 3.0 hub.
- **Recommendation:** buy the two monocular wrist cameras and print the BrainCo brackets. Unitree's own data platform (G1-D) uses head + 2 wrist cameras ([G1-D listing](https://globaldronehq.com/products/unitree-g1-d-humanoid-robot-data-acquisition-series)) **[S]**.

### 3.2 Format and conversion

Flow: `xr_teleoperate` JSON, then the `unitree_lerobot` editor (trim), then `sort_and_rename_folders.py`, then `convert_unitree_json_to_lerobot.py --robot_type Unitree_G1_Brainco`. This produces LeRobot dataset v2/v3 that can be pushed to the HF Hub ([unitree_lerobot](https://github.com/unitreerobotics/unitree_lerobot)) **[V]**.

### 3.3 How many demos

- **ACT** learned ALOHA tasks from **50 demos/task** ([ACT paper](https://arxiv.org/abs/2304.13705)).
- **SmolVLA:** "record ~50 episodes"; 25 episodes "was not enough"; 10 episodes × 5 positions worked ([LeRobot SmolVLA doc](https://github.com/huggingface/lerobot/blob/main/docs/source/smolvla.mdx)) **[V]**.
- **GR00T-WBC VLA workflow:** "at least 50–100 demonstrations" ([vla_workflow](https://nvlabs.github.io/GR00T-WholeBodyControl/tutorials/vla_workflow.html)) **[V]**.
- **GR00T fine-tuning examples:** 102 sim episodes gave 50% closed-loop success ([CloudWalk](https://huggingface.co/cloudwalk-research/GR00T-N1.6-G1-PnPAppleToPlate)), and OpenHLM used 120 demos ([OpenHLM](https://arxiv.org/abs/2606.22174)) **[S]**.
- **Ψ0** claims new long-horizon skills from about 80 trajectories ([Psi0](https://github.com/physical-superintelligence-lab/Psi0)) **[S]**.
- **Plan:** 100 demos per task variant, spread over about 5 object positions, 2–3 cups and 2 lighting conditions. Roughly 2–4 operator-hours per task.

---

## 4. Imitation-learning policies

| Policy                                                                       | Size                | Train compute                                                                                                                          | Inference                                                                       | G1 notes                                                                                                                                                                                                                                                                          |
| ---------------------------------------------------------------------------- | ------------------- | -------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **ACT**                                                                      | about 80M           | 1 consumer GPU, hours                                                                                                                  | Light; feasible on Orin NX                                                      | Supported in `unitree_lerobot` **[V]**. Single-task, no language                                                                                                                                                                                                                  |
| **Diffusion Policy** ([paper](https://arxiv.org/abs/2303.04137))             | about 100–250M      | 1 GPU                                                                                                                                  | Multiple denoising steps                                                        | Supported in `unitree_lerobot`                                                                                                                                                                                                                                                    |
| **SmolVLA** ([paper](https://arxiv.org/abs/2506.01844))                      | 450M                | Consumer GPU                                                                                                                           | About 2 GB inference memory ([tutorial](https://wikidocs.net/386013)) **[S]**   | In LeRobot; not listed in `unitree_lerobot`                                                                                                                                                                                                                                       |
| **π0.5** ([openpi](https://github.com/Physical-Intelligence/openpi))         | about 3B            | LoRA >22.5 GB; **full >70 GB (A100/H100)**; inference >8 GB ([openpi README](https://github.com/Physical-Intelligence/openpi)) **[V]** | Off-board GPU                                                                   | LeRobot G1 docs train `pi05` for 3,000 steps at batch 32 and deploy with RTC ([docs](https://huggingface.co/docs/lerobot/unitree_g1)) **[V]**. OpenHLM's whole-body VLA is built on π0.5 ([project](https://openhlm-project.github.io/))                                          |
| **GR00T N1.6 / N1.7** ([Isaac-GR00T](https://github.com/NVIDIA/Isaac-GR00T)) | 3B                  | **≥40 GB GPU (H100/L40 recommended)** **[V]**                                                                                          | ≥16 GB GPU. About 28 Hz on AGX Thor with TensorRT; no Orin figure given **[V]** | Tags `UNITREE_G1` (decoupled WBC) and `UNITREE_G1_SONIC`. G1 sim checkpoints exist ([IsaacLab-Arena loco-manip](https://isaac-sim.github.io/IsaacLab-Arena/main/pages/example_workflows/locomanipulation/index.html)). N1.7 is GA on a Cosmos-Reason2 / Qwen3-VL backbone **[V]** |
| **UnifoLM-VLA-0** (Unitree)                                                  | Qwen2.5-VL-7B based | Large                                                                                                                                  | Off-board                                                                       | G1, 12 task categories, **CC BY-NC-SA** ([HF](https://huggingface.co/unitreerobotics/UnifoLM-VLM-Base)) **[S]**                                                                                                                                                                   |
| **Ψ0**                                                                       |                     |                                                                                                                                        |                                                                                 | G1 + Dex3, 9 tasks open-sourced ([repo](https://github.com/physical-superintelligence-lab/Psi0), [paper](https://arxiv.org/abs/2603.12263)) **[S]**                                                                                                                               |
| **OpenHLM**                                                                  | π0.5 base           |                                                                                                                                        |                                                                                 | Code, data and checkpoints released; 87.5% vs GR00T N1.6 57.5% on a long-horizon task, with less than half the demo time ([project](https://openhlm-project.github.io/), [code](https://github.com/OpenHLM-project/OpenHLM)) **[S]**                                              |

**Can the Orin NX run these?**

- **ACT: yes.** Quantised ACT runs on even an 8 GB Jetson ([arXiv 2608.03938](https://arxiv.org/pdf/2608.03938)) **[S]**.
- **3B VLAs: practically no.**
  - π0.5 runs at about 4.6 Hz (BF16) to 8 Hz (INT8) on the larger **AGX** Orin 64 GB ([community FlashRT data](https://caballo-compute.pages.dev/systems/robotics-edge-accelerators)) **[S]**, and about 1.4 s latency in another Orin study ([Jetson-PI](https://www.alphaxiv.org/abs/2607.12659.md)) **[S]**.
  - The NX has roughly half the AGX's memory bandwidth.
- **JetPack 5 is a second blocker.** Modern LeRobot uses Python 3.12. JetPack 7.2 now supports Orin, with CUDA 13 ([NVIDIA archive](https://developer.nvidia.com/embedded/jetpack/downloads/archive-7.2)) **[S]**, but upgrading means reflashing (see §5).

**Off-board inference** is the standard design:

- LeRobot ships a remote-inference profile (GPU host as server, G1 Jetson as client over Zenoh) ([unitree_g1_sonic.yaml](https://github.com/huggingface/lerobot/blob/main/examples/remote_inference/unitree_g1_sonic.yaml)) **[V]** and async inference ([docs](https://huggingface.co/docs/lerobot/async)).
- GR00T uses a ZMQ PolicyServer on a GPU box ([vla_workflow](https://nvlabs.github.io/GR00T-WholeBodyControl/tutorials/vla_workflow.html)) **[V]**.
- Action chunking plus RTC hides 100–200 ms of latency. Use **wired Ethernet first**, then a dedicated Wi-Fi 6 AP (Unitree recommends Wi-Fi 6 for teleop ([Device.md](https://github.com/unitreerobotics/xr_teleoperate/blob/main/Device.md))).
- An H100 in a data centre reached over the internet adds jitter. Run inference on a **local RTX 4090 / 5090 laptop or desktop** (enough for π0.5 and GR00T inference at ≥8–16 GB) and keep the H100 for training.

---

## 5. Loco-manipulation and whole-body control

| Project                                                                                                                                                                                   | What                                                                                                                                                                                        | Open / G1-ready                                                                                         | Status                                                                                                                                                                                                                                                                                                                                                 |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **GEAR-SONIC** ([repo](https://github.com/NVlabs/GR00T-WholeBodyControl), [HF](https://huggingface.co/nvidia/GEAR-SONIC), [paper](https://arxiv.org/abs/2511.07820))                      | Universal motion-tracking whole-body controller; outputs 64-D latent motion tokens at 50 Hz; C++ + TensorRT deploy; PICO whole-body teleop; end-to-end collect, fine-tune (N1.7) and deploy | Code Apache-2.0, weights under NVIDIA Open Model License; **3 G1 checkpoints**; v1.1 2026-07-23 **[V]** | **The strongest current option.** Caveats: hand action is **7-D per hand** (Dex3-shaped) ([vla_workflow](https://nvlabs.github.io/GR00T-WholeBodyControl/tutorials/vla_workflow.html)), and onboard deploy requires **JetPack 6 on the G1 Orin** ([install](https://nvlabs.github.io/GR00T-WholeBodyControl/getting_started/installation_deploy.html)) |
| GR00T Decoupled WBC                                                                                                                                                                       | RL legs + IK arms                                                                                                                                                                           | Yes (2025-11) **[V]**                                                                                   | Good stepping stone                                                                                                                                                                                                                                                                                                                                    |
| **Holosoma** (Amazon FAR) ([repo](https://github.com/amazon-far/holosoma))                                                                                                                | Locomotion + whole-body tracking training/deploy, IsaacSim / MJWarp / MuJoCo                                                                                                                | Apache-2.0, G1 out of the box, very active (pushed 2026-11-02) **[V]**                                  | Used as LeRobot's G1 locomotion option                                                                                                                                                                                                                                                                                                                 |
| **BeyondMimic** ([repo](https://github.com/HybridRobotics/whole_body_tracking), [paper](https://arxiv.org/abs/2508.08241))                                                                | Motion tracking + guided diffusion                                                                                                                                                          | MIT, active **[V]**                                                                                     | Dynamic motions; less about grasping                                                                                                                                                                                                                                                                                                                   |
| TWIST / TWIST2 ([TWIST](https://github.com/YanjieZe/TWIST), [TWIST2](https://github.com/amazon-far/TWIST2))                                                                               | Whole-body teleop and data collection                                                                                                                                                       | MIT, G1 **[V]**                                                                                         | Needs PICO trackers + neck add-on                                                                                                                                                                                                                                                                                                                      |
| HOMIE / AMO                                                                                                                                                                               | See §1.3                                                                                                                                                                                    | Yes                                                                                                     | AMO is HomeBody's lower body                                                                                                                                                                                                                                                                                                                           |
| OmniH2O / HumanPlus ([human2humanoid](https://github.com/LeCAR-Lab/human2humanoid), [humanplus](https://github.com/MarkFzp/humanplus))                                                    | 2024 whole-body teleop + imitation learning (H1)                                                                                                                                            | Open, stale since 2024–25 **[V]**                                                                       | Historical; superseded on G1                                                                                                                                                                                                                                                                                                                           |
| ExBody2 ([paper](https://arxiv.org/abs/2412.13196))                                                                                                                                       | Expressive whole-body tracking                                                                                                                                                              | No official repo found                                                                                  | —                                                                                                                                                                                                                                                                                                                                                      |
| **VIRAL** ([paper](https://arxiv.org/abs/2511.15200))                                                                                                                                     | Pure sim-to-real RGB loco-manipulation on G1 (54 loops); needs **up to 64 GPUs**                                                                                                            | Project page only                                                                                       | Shows that pure sim-to-real is beyond a student budget                                                                                                                                                                                                                                                                                                 |
| unitree_rl_lab ([repo](https://github.com/unitreerobotics/unitree_rl_lab))                                                                                                                | Isaac Lab G1-29 locomotion train, then sim2sim (MuJoCo), then sim2real                                                                                                                      | Apache-2.0 **[V]**                                                                                      | For your own locomotion policy                                                                                                                                                                                                                                                                                                                         |
| ProtoMotions ([G1 tracker tutorial](https://nvlabs.github.io/ProtoMotions/tutorials/workflows/g1_deployment.html)), InstinctLab ([repo](https://github.com/project-instinct/instinctlab)) | Tracking pipelines                                                                                                                                                                          | Open **[V]**                                                                                            | Alternatives                                                                                                                                                                                                                                                                                                                                           |

**Reflashing warning.** Moving the G1 Orin NX from JetPack 5 to 6 means a reflash that wipes the NVMe ([mirrored flashing notes](https://git.bibbit.duckdns.org/epilectrik/gr00t-WholeBodyControl/raw/branch/main/docs/source/references/jetpack6.md)) **[S]**. That destroys everything on the robot, including any other team's files. Do a full image backup and get explicit sign-off first.

---

## 6. Classical grasp pipeline (an alternative or complement to learned policies)

Pipeline: language → VLM/LLM picks the object → **SAM 3** text-prompted segmentation ([sam3](https://github.com/facebookresearch/sam3)) or [Grounded-SAM-2](https://github.com/IDEA-Research/Grounded-SAM-2) → D435i depth gives an object point cloud → grasp pose → Pinocchio IK with collision check → reach above, approach, close the hand, lift.

**Grasp generators:**

- [GraspGen](https://github.com/NVlabs/GraspGen) (diffusion, 53M grasps) supports **only** Franka, Robotiq-2F-140 and suction ([repo](https://github.com/nvlabs/graspgen)) **[S]**.
- [GraspGen-X](https://github.com/NVlabs/GraspGenX) (CVPR 2026) conditions on gripper geometry, and its demos include multi-finger hands ([paper](https://openaccess.thecvf.com/content/CVPR2026/papers/Han_GraspGen-X_Cross-Embodiment_6-DOF_Diffusion-based_Grasping_CVPR_2026_paper.pdf)) **[S]**. It is the best candidate for a Revo2 with its URDF.
- [Contact-GraspNet](https://github.com/NVlabs/contact_graspnet) is parallel-jaw and stale.
- AnyGrasp needs a per-machine licence ([anygrasp_sdk](https://github.com/graspnet/anygrasp_sdk)) **[V]**.
- Dexterous-specific research: D(R,O) / T(R,O) Grasp ([arXiv 2510.12724](https://arxiv.org/abs/2510.12724v1)) **[S]**.

**The practical shortcut is the HomeBody approach:** "we predict the grasp analytically". Segment, then stereo depth, then a top or side grasp on the object's principal axis, with retry and stance adjustment on failed contact. It runs on G1 + D435i head camera + MID-360 LiDAR + Dex3 hands, with an RTX 4080/4090 workstation, AMO lower body, and an LLM planner (validated with GPT-6 Astra and Claude Opus 5.5). Code is Apache-2.0; the REAL stack was released 2026-10-26 ([site](https://tml.stanford.edu/homebody/), [repo](https://github.com/Stanford-TML/homebody)) **[V]**. For cups and glasses, a **cylindrical power grasp from the side** computed from the segmented point cloud is reliable, and it needs no learned grasp network.

**Feasibility with head camera only:**

- **Fine for upright cylinders** on a table about 0.3–0.6 m in front of the robot.
- **Poor for** pinches, cluttered scenes, and anything the hand occludes during the final approach.
- **Wrist cameras fix final alignment.** You will want them anyway for learned policies.

**Other perception tools:**

- [FoundationPose](https://github.com/NVlabs/FoundationPose) for known-object 6-D pose (useful for door handles).
- [FoundationStereo](https://github.com/NVlabs/FoundationStereo) to clean D435i depth on glass. **Transparent glasses break D435i depth**, so start with an opaque cup.

**Zero-shot VLM manipulation research to watch:**

- ZeroDex ([arXiv 2606.19340](https://arxiv.org/html/2606.19340)): IK-feasible grasp share went from 24% to 37%, which shows how hard reachability is.
- Wh0 on G1 + Inspire ([arXiv 2606.22136](https://arxiv.org/html/2606.22136v2)): 8% to 39% zero-shot success.

---

## 7. Safety for manipulation near people

There is **no official Unitree spec** for arm torque or velocity limits, or an `arm_sdk` watchdog ([search summary of community docs](https://git.bibbit.duckdns.org/epilectrik/unitree-g1/raw/branch/master/context/safety-limits.md)) **[S]**. Build these layers yourself:

1. **Gains and velocity.**
   - Start below Unitree's teleop gains: shoulder/elbow kp ≤ 40–60, kd 1.5–3. One adapter uses kp 60 / kd 1.5 with 2 s blends ([PyPI adapter](https://pypi.org/project/inspect-robots-unitree-g1/)) **[S]**.
   - Clip velocity with `xr_teleoperate`'s `clip_arm_q_target`, at something like **≤1 rad/s near people** instead of the default 30 ([robot_arm.py](https://github.com/unitreerobotics/xr_teleoperate/blob/main/teleop/robot_control/robot_arm.py)) **[V]**.
2. **Torque.** Clip feed-forward `tau`, and monitor `motor_state.tau_est` against model torque. If the gap is above a threshold, treat it as a collision: freeze, then ramp the weight to 0.
3. **Workspace box.** Check every IK target against an allowed Cartesian box in front of the torso, and add a head/face keep-out sphere. Reject the target rather than clamping it.
4. **Watchdog.** If no fresh command arrives within 50 ms, hold the current measured pose, then ramp the weight down. A community stack uses a 50 ms watchdog with hold mode ([Kumar G1 stack](https://kumar-abhina.github.io/projects/7_project)) **[S]**.
5. **E-stop.**
   - The hand-held remote's damping combo, plus `xr_teleoperate`'s "both joysticks = soft stop into damping" ([README](https://github.com/unitreerobotics/xr_teleoperate)) **[V]**.
   - A software stop that zeroes the `arm_sdk` weight and opens the hands.
   - A dedicated safety operator with the remote; SONIC docs require one ([model card](https://nvlabs.github.io/GR00T-WholeBodyControl/model_card.html)) **[V]**.
6. **Hand-over.** "Hand me that" should present the object at a fixed hand-over pose, wait for a pull (finger-position error or tactile), then open. Never reach into a person's space.
7. **Testing order.** Simulation first, then MuJoCo sim2sim ([unitree_mujoco](https://github.com/unitreerobotics/unitree_mujoco)), then a gantry or harness, then free standing. Standards: ISO 25785-1 for dynamically stable robots is still a draft and builds on ISO/TS 15066 contact limits ([i-scoop](https://www.i-scoop.eu/iso-25785-1-explained-and-what-it-means-for-humanoid-robot-safety/)) **[S]**.

---

## 8. Recommended roadmap (6–12 months, student team)

**Architecture target:** the LLM agent (c3po's existing conversational layer) calls **skills** such as `pick(object)`, `place(target)`, `handover()`, `go_to(location)` and `open(door)`. Each skill is either a learned policy (π0.5 / ACT) or the classical grasp pipeline, sitting on one safety-wrapped `arm_sdk` controller and the BrainCo DDS topics. "Do anything" then means "compose skills we have".

### Phase 0 (weeks 0–4): control foundation

1. Build a single **arm-control process** that owns `rt/arm_sdk`:
   - weight ramp in and out from the measured pose;
   - velocity, workspace and watchdog limits (§7);
   - Unitree's Pinocchio/CasADi IK (copy `robot_arm_ik.py`).
     Keep the stock arm-action client and this process mutually exclusive.
2. Install `brainco_hand_service` on PC2. Script **power / pinch / hook / open** primitives.
3. Add a sim twin: `unitree_mujoco` for the arms, plus the BrainCo URDF from `xr_teleoperate/assets`.
4. **Hardware:**
   - two 160° wrist cameras with the BrainCo brackets and a USB 3 hub ([Device.md](https://github.com/unitreerobotics/xr_teleoperate/blob/main/Device.md));
   - a Quest 3 or PICO 4 Ultra;
   - a Wi-Fi 6 router;
   - an RTX 4090-class inference PC.

### Phase 1 (months 1–3): first learned skill, "pick the cup and place it on the plate"

1. Fork **[xr_teleoperate](https://github.com/unitreerobotics/xr_teleoperate)** and **[unitree_lerobot](https://github.com/unitreerobotics/unitree_lerobot)**. Record with `--arm=G1_29 --ee=brainco --record`, robot standing, opaque cup, 100 demos over about 5 positions.
2. Convert with `--robot_type Unitree_G1_Brainco`. Use `eval_g1_dataset.py` (offline) and `replay_robot.py` to check the data.
3. Train **ACT** (baseline, one GPU, hours) and **π0.5** (`lerobot/pi05_base`, H100, about 3k–20k steps).
4. Deploy with `eval_g1.py` / LeRobot RTC rollout off-board, wired first.
5. **Success criterion:** ≥80% over 20 trials at trained positions.

### Phase 2 (months 3–6): language and generalisation

1. Run the classical pipeline in parallel: SAM 3 + D435i, then analytic side grasp, then IK, then close. Use [HomeBody](https://github.com/Stanford-TML/homebody) as the reference. This gives open-vocabulary "bring me the X" for upright graspables.
2. Multi-task π0.5 (or GR00T N1.7 with `NEW_EMBODIMENT`) covering pick, place, handover, and push drawer. Use 100–200 demos per task, co-train with public G1 BrainCo datasets, and condition on language.
3. Add the handover skill and the LLM skill router. Measure end-to-end success on 10 spoken commands.

### Phase 3 (months 6–12): loco-manipulation

1. "Bring me the glass" = navigate (stock locomotion / high-level API) → stop at the table → phase-1/2 pick → walk with the arm held in a tucked carry pose → handover. Stock Regular-mode locomotion with light objects is enough.
2. Whole-body (squat, reach low, open doors). Choose one:
   - **(a) GEAR-SONIC + GR00T N1.7.** Needs a JetPack 6 reflash (backup first, get sign-off) and a 6-D BrainCo hand head in place of the 7-D Dex3 vector. The PICO whole-body teleop then gives you data.
   - **(b) OpenHLM** (π0.5-based, open code, data and checkpoints).
     "Open the door" is the hardest common request (force interaction, base and arm coordination). Make it the last milestone.
3. Isaac Sim host: use IsaacLab-Arena loco-manipulation and Mimic data generation ([Arena](https://isaac-sim.github.io/IsaacLab-Arena/main/pages/example_workflows/locomanipulation/index.html)) to multiply real demos once a BrainCo G1 asset exists. Pure sim-to-real visual policies (VIRAL) need tens of GPUs, so they are out of scope.

### Compute summary

| Need                                      | Hardware                                                                           |
| ----------------------------------------- | ---------------------------------------------------------------------------------- |
| ACT / Diffusion / SmolVLA training        | 1× RTX 4090 (24 GB)                                                                |
| π0.5 full fine-tune, GR00T N1.7 fine-tune | **H100 80 GB** (π0.5 >70 GB full; GR00T ≥40 GB, 4+ GPUs recommended for SONIC VLA) |
| VLA inference                             | Local GPU ≥16 GB (4090/5090), wired to the robot                                   |
| Onboard (Orin NX, JetPack 5)              | DDS bridges, cameras, BrainCo service, safety watchdog, ACT at most                |
| Sim                                       | Isaac Sim 4.5 / 5.x host (RTX 3090/4090 tested for `unitree_sim_isaaclab`)         |

### Repos to fork, in priority order

1. [unitreerobotics/xr_teleoperate](https://github.com/unitreerobotics/xr_teleoperate)
2. [unitreerobotics/unitree_lerobot](https://github.com/unitreerobotics/unitree_lerobot)
3. [unitreerobotics/brainco_hand_service](https://github.com/unitreerobotics/brainco_hand_service)
4. [huggingface/lerobot](https://github.com/huggingface/lerobot)
5. [Stanford-TML/homebody](https://github.com/Stanford-TML/homebody)
6. [BrainCoTech/unitree-g1-brainco-hand](https://github.com/BrainCoTech/unitree-g1-brainco-hand)
7. Later: [NVlabs/GR00T-WholeBodyControl](https://github.com/NVlabs/GR00T-WholeBodyControl) + [NVIDIA/Isaac-GR00T](https://github.com/NVIDIA/Isaac-GR00T), or [OpenHLM](https://github.com/OpenHLM-project/OpenHLM)

---

## 9. Open questions to verify on the robot

- Is the hand a Revo2 Basic or Touch? Is it RS-485 or CAN-FD? Is it on PC2 USB?
- How does the arm-action table behave while `arm_sdk` weight > 0? Test on a gantry.
- Do the BrainCo DDS state messages report current or force for grasp detection? `brainco_hand_service` documents only position and speed.
- What Wi-Fi round-trip latency does the venue (MVP stand) have? Plan for a wired fallback.

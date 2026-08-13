import { defineSkill, t } from "./define";

export const startWalkingWaist = defineSkill({
  name: "start_walking_waist",
  description:
    "Enter FSM 501, 'Walk Motion-3Dof-waist' — the walk program for a 29-DoF G1. " +
    "500 and 501 are two DIFFERENT walk programs selected by how many degrees of freedom " +
    "the waist has, not a generic start and a variant of it. Unitree documents mode_machine " +
    "as 4=23-DoF, 5=29-DoF, 6=27-DoF, and this robot reports 5, so 501 is likely the " +
    "program it actually implements and 500 the other variant's. That matters because " +
    "start_walking (500) has been observed returning success while never leaving StandUp, " +
    "which is exactly how a recognised-but-unimplemented mode id would behave. " +
    "NEVER EXECUTED on this robot: this is a hypothesis to be tested in a supervised " +
    "window with an operator ready to damp, not something to try mid-plan.",
  parameters: t.Object({}),
  preconditions: ["fsm_state_is_preparation", "operator_present"],
  expectedDurationSeconds: 3,
  cancellable: false,
  typicalFailureModes: ["fsm_transition_rejected", "no_motion_controller_loaded"],
  classification: "posture",
  dangerLevel: "high",
  status: "real",
  // Untested on hardware and it engages a walk controller. The agent must not
  // reach for this on its own; an operator runs it deliberately.
  works: { sim: false, real: false },
});

/**
 * Fired on `window` the moment any PARAR button is pressed, before its request
 * leaves the browser.
 *
 * `stop_everything` stops the robot and latches the teleop stream, but a page
 * running its OWN dispatch loop (/vr-control's walk_velocity ticks, when no
 * stream is connected) is not something the bridge can latch — its next tick
 * would simply send a fresh walk. Pages with such a loop listen for this and
 * latch themselves.
 */
export const ESTOP_EVENT = "c3po:estop";

/**
 * The robot camera on /vr-control: the page's ONE MJPEG connection, which the
 * headset shares.
 *
 * Same source as /live-camera: `apps/perception`'s vision container, which
 * is the process that already owns the D435i — and a V4L2 device has exactly
 * one owner, so it is the only thing that CAN show this picture.
 *
 * This page previously used its own WebSocket relay over teleimager's ZeroMQ
 * feed. That path is gone: teleimager is not running on the robot, and the
 * `/dev/video4` it opened no longer exists (colour moved to video5). Two
 * camera transports where one cannot work is worse than one that does.
 *
 * The status poll is not decoration. An <img> pointed at an MJPEG stream
 * keeps showing its last frame forever, with no event — so a camera that
 * died two minutes ago looks identical to a working one. The picture comes
 * from the <img>; whether it is LIVE comes from /status.
 *
 * Moved out of `vr-control.svelte.ts` intact.
 */

import { PUBLIC_API_URL, PUBLIC_ROBOT_CAM_URL } from "$app/env/public";
import {
  camCredentials,
  connectRobotCamera,
  type RobotCamHandle,
  type RobotCamState,
  type RobotCamStatus,
} from "./mjpeg-camera";
import { describeRobotCam } from "./camera-copy";
import { cameraReasonFor, type CameraReason } from "#lib/webxr/camera-layer.js";
import type { XrSession } from "./vr-xr.svelte";

export class VrCamera {
  camState = $state<RobotCamState>("closed");
  camDetail = $state("");
  camFrameUrl = $state<string | null>(null);
  camLive = $state(false);
  camStatus = $state.raw<RobotCamStatus | null>(null);

  /** The live headset session, if any: the stream URL and liveness follow it. */
  #headset: () => XrSession | null;
  #camHandle: RobotCamHandle | null = null;

  constructor(headset: () => XrSession | null) {
    this.#headset = headset;
  }

  /**
   * Whether this console has a camera URL at all. Without one the headset
   * never asks for a picture, and "no frame ever arrived" is the console's
   * configuration, not the robot's vision process.
   */
  get camConfigured(): boolean {
    return PUBLIC_ROBOT_CAM_URL.trim() !== "";
  }

  /** The page's camera state, phrased for somebody wearing the headset. */
  reason(): CameraReason {
    if (!this.camConfigured) return cameraReasonFor("error", undefined, false);
    // The same translation the on-page panel uses, so a token like "cors" or
    // "HTTP 503" reaches the headset as words, not as a code.
    const { overlay } = describeRobotCam({
      state: this.camState,
      detail: this.camDetail || null,
      status: this.camStatus,
      url: this.camFrameUrl,
    });
    return cameraReasonFor(
      this.camState,
      overlay?.kind === "message" ? overlay.title : undefined,
    );
  }

  /** Open the camera connection once. Safe to call repeatedly. */
  ensure(): void {
    if (this.#camHandle) return;
    this.connect();
  }

  /**
   * A fresh stream URL on the existing connection — what "Reconectar" and the
   * camera view's own image-error retry ask for. The vision server CLOSES the
   * stream when it goes stale, so a retry has to change the URL; the handle's
   * `reconnect()` does that and reads /status immediately.
   */
  reconnect(): void {
    if (this.#camHandle) this.#camHandle.reconnect();
    else this.connect();
  }

  connect(): void {
    this.#camHandle?.close();
    this.#camHandle = null;
    const base = PUBLIC_ROBOT_CAM_URL.trim();
    if (!base) {
      this.camState = "error";
      this.camDetail = "PUBLIC_ROBOT_CAM_URL no está configurado.";
      return;
    }
    const creds = camCredentials(base, PUBLIC_API_URL);
    this.#camHandle = connectRobotCamera(
      base,
      {
        onState: (state, detail) => {
          this.camState = state;
          this.camDetail = detail ?? "";
        },
        onStatus: (status) => {
          this.camStatus = status;
          this.camLive = status?.live ?? false;
          // Stale is dimmed in the headset rather than hidden: losing the view
          // mid-motion is worse than an obviously old one.
          this.#headset()?.setCameraLive(this.camLive);
        },
        onStreamUrl: (url) => {
          this.camFrameUrl = url;
          // Same URL to the headset. It changes on every reconnect (the source
          // cache-busts it), and the XR layer must follow — reusing a dead MJPEG
          // URL is a retry that does nothing, so a stall would otherwise freeze
          // the headset view on its last frame for the rest of the session.
          this.#headset()?.setCameraStream(url, creds === "include");
        },
      },
      creds,
    );
  }

  /** Teardown. */
  close(): void {
    this.#camHandle?.close();
    this.#camHandle = null;
  }
}

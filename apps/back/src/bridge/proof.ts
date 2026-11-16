/**
 * The proof back shows the bridge on a privileged call: the shared
 * `SAFETY_CONFIRM_SECRET`, in the header the bridge checks with
 * `safety.confirm_secret_ok`. One place, so /safety/confirm, /safety/ack and
 * /face/* cannot drift apart. Null when unset: the caller answers 503.
 */
export function bridgeProof(): Record<string, string> | null {
  const secret = process.env.SAFETY_CONFIRM_SECRET;
  return secret ? { "x-c3po-confirm-secret": secret } : null;
}

/**
 * The bridge's English, in Spanish — one table for every surface that shows it.
 *
 * The bridge writes its notes, hints and reasons in English, and the console
 * shows them verbatim (in `<span lang="en">`) because they are the precise
 * diagnosis. Operators read Spanish, so the strings they actually meet get a
 * Spanish rendering shown FIRST, with the English kept underneath. Health,
 * the live map and the live camera all used this table from inside health.ts;
 * it lives here so none of them depends on another page's module.
 *
 * Plain module, no runes, so `bun test` can load it.
 */

/**
 * The bridge's English strings an operator actually meets on this page, each
 * with its Spanish rendering. Sources, so a reworded bridge string can be
 * found: `bridge/sdk/perception_link.py` (gate `disabled_reason`),
 * `mcp_server.py` (costmap/camera/snapshot/voice bodies),
 * `sdk/videohub.py` + `sdk/camera_relay.py` (camera `hint`), back's own codes
 * (`bridge_error`, `unavailable`, the probe's `unreadable_body`) and the
 * transport errors Bun puts in `bridge_error`.
 *
 * Matched case-insensitively against the trimmed text: `exact` for short
 * fixed strings, `prefix` for the long hints (their tails carry commands that
 * may be reworded), `includes` for transport errors that arrive wrapped. A
 * miss returns null and the caller shows the English alone — a wrong
 * translation would be worse than none.
 */
const BRIDGE_ES: readonly {
  match: "exact" | "prefix" | "includes";
  en: string;
  es: string;
}[] = [
  // Nav2 gate — why it is closed.
  {
    match: "exact",
    en: "never armed",
    es: "No se armó desde que arrancó el puente.",
  },
  {
    match: "exact",
    en: "arm TTL expired",
    es: "Se cerró sola: venció el tiempo de armado.",
  },
  { match: "exact", en: "stop_everything", es: "La cerró PARAR." },
  { match: "exact", en: "operator disarmed", es: "La cerró un operador." },
  {
    match: "exact",
    en: "link stopped",
    es: "Se detuvo el enlace con la percepción.",
  },
  { match: "exact", en: "disabled", es: "Cerrada, sin motivo informado." },
  // Perception snapshot.
  {
    match: "exact",
    en: "could not build a snapshot",
    es: "El puente no pudo armar la instantánea de la escena.",
  },
  // Voice telemetry.
  {
    match: "exact",
    en: "voice telemetry unavailable",
    es: "La telemetría de voz no está disponible en el puente.",
  },
  // Costmap.
  { match: "exact", en: "no costmap received", es: "No llegó ningún costmap." },
  { match: "exact", en: "no_costmap", es: "No llegó ningún costmap." },
  {
    match: "exact",
    en: "costmap payload is not decodable",
    es: "El costmap llegó, pero no se pudo decodificar.",
  },
  {
    match: "prefix",
    en: "the costmap comes from Nav2's global_costmap",
    es: "El costmap sale del global_costmap de Nav2, así que solo existe mientras hay un stage de nav2 levantado: c3po perception up nav2-fake (sin sensores) o c3po perception up nav2 (toma los dos sensores).",
  },
  // Camera.
  {
    match: "prefix",
    en: "neither camera feed is live",
    es: "Ninguna de las dos cámaras entrega imagen. La del fabricante necesita que videohub_pc4 tenga /dev/video4; la de visión necesita c3po perception up perception (o nav2) con el dispositivo tomado por c3po camera take. No pueden funcionar las dos a la vez.",
  },
  {
    match: "prefix",
    en: "no frame has ever arrived",
    es: "Nunca llegó un cuadro: puede que videohub_pc4 no esté corriendo. Es el servicio del fabricante dueño de /dev/video4; si alguien detuvo master_service para darle la RealSense al detector, esta cámara está apagada a propósito. sudo systemctl start master_service la vuelve a levantar.",
  },
  {
    match: "prefix",
    en: "frames stopped",
    es: "Dejaron de llegar cuadros: lo más probable es que hayan detenido videohub_pc4 para liberar /dev/video4 para el detector. No pueden correr los dos a la vez.",
  },
  {
    match: "exact",
    en: "the head camera is real-hardware only",
    es: "La cámara de la cabeza solo existe en el robot real.",
  },
  {
    match: "prefix",
    en: "this feed reads the G1's videohub RPC",
    es: "Esta cámara lee el RPC videohub del G1; las del simulador son servidores WebRTC teleimager en los puertos 60001–60003 (apps/web/README.md).",
  },
  { match: "exact", en: "no frame yet", es: "Todavía no llegó ningún cuadro." },
  // back's own codes, and the probe's.
  {
    match: "exact",
    en: "bridge_error",
    es: "El puente respondió con un error.",
  },
  {
    match: "exact",
    en: "unavailable",
    es: "No disponible; el puente no dio detalle.",
  },
  {
    match: "exact",
    en: "unreadable_body",
    es: "Respondió 200, pero con un cuerpo que no se pudo leer.",
  },
  // Transport errors back records as `bridge_error` (Bun / undici wording).
  {
    match: "includes",
    en: "ECONNREFUSED",
    es: "El puente rechazó la conexión: no hay nada escuchando en ese puerto (¿túnel SSH o servicio c3po caído?).",
  },
  {
    match: "includes",
    en: "Unable to connect",
    es: "No se pudo conectar con el puente (¿túnel SSH o servicio c3po caído?).",
  },
  {
    match: "exact",
    en: "fetch failed",
    es: "No se pudo conectar con el puente (¿túnel SSH o servicio c3po caído?).",
  },
  { match: "includes", en: "timed out", es: "El puente no contestó a tiempo." },
];

/**
 * The Spanish for a known bridge string, or null when we do not know it (the
 * caller then shows the English alone, verbatim).
 */
export function translateBridgeText(
  text: string | null | undefined,
): string | null {
  const t = (text ?? "").trim().toLowerCase();
  if (!t) return null;
  for (const entry of BRIDGE_ES) {
    const en = entry.en.toLowerCase();
    if (
      entry.match === "exact"
        ? t === en
        : entry.match === "prefix"
          ? t.startsWith(en)
          : t.includes(en)
    ) {
      return entry.es;
    }
  }
  return null;
}

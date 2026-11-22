/**
 * Type-level checks that a hand-written TS type agrees with the bridge's.
 *
 * Hand-written, unlike its neighbours: `bridge-payloads.ts` is generated from
 * the bridge's own TypedDicts (apps/bridge/scripts/export_contracts.py), and
 * these helpers let apps/back and apps/web hold their restated types to it in
 * their `contracts.test.ts`. The check is the compiler's (`check-types`), not a
 * runtime one: the failure names the field.
 *
 *   type _ = Fits<Mine, Bridge>;
 *     Every payload the bridge can send is a valid `Mine`: same field types,
 *     and nothing `Mine` requires is optional on the bridge.
 *
 *   type _ = NoUnknownKeys<UnknownKeys<Mine, Bridge>>;
 *     `Mine` names no field the bridge does not send, at any depth. This is
 *     the one that catches a rename: `Fits` alone passes when the old name was
 *     optional, because a missing optional field is still assignable.
 *     Errors as `Type '"raw.fsm_id"' does not satisfy the constraint 'never'`.
 *
 * Types only. Nothing here exists at runtime.
 */

/** Compiles only when `Bridge` is assignable to `Mine`. */
export type Fits<Mine, Bridge extends Mine> = Bridge;

/** Compiles only when `Keys` is `never`. */
export type NoUnknownKeys<Keys extends never> = Keys;

/** Dotted paths of the keys `Mine` declares and `Bridge` does not, recursively. */
export type UnknownKeys<
  Mine,
  Bridge,
  Path extends string = "",
> = Mine extends readonly (infer M)[]
  ? Bridge extends readonly (infer B)[]
    ? UnknownKeys<M, B, `${Path}[].`>
    : never
  : Mine extends object
    ? Bridge extends object
      ? | `${Path}${Exclude<keyof Mine, keyof Bridge> & string}`
        | {
            [K in keyof Mine & keyof Bridge & string]-?: UnknownKeys<
              NonNullable<Mine[K]>,
              NonNullable<Bridge[K]>,
              `${Path}${K}.`
            >;
          }[keyof Mine & keyof Bridge & string]
      : never
    : never;

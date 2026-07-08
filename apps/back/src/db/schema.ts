import { relations } from "drizzle-orm";
import {
  pgTable,
  text,
  timestamp,
  boolean,
  jsonb,
  vector,
  index,
  uniqueIndex,
} from "drizzle-orm/pg-core";

export const user = pgTable("user", {
  id: text("id").primaryKey(),
  name: text("name").notNull(),
  email: text("email").notNull().unique(),
  emailVerified: boolean("email_verified").default(false).notNull(),
  image: text("image"),
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at")
    .defaultNow()
    .$onUpdate(() => /* @__PURE__ */ new Date())
    .notNull(),
  role: text("role"),
  banned: boolean("banned").default(false),
  banReason: text("ban_reason"),
  banExpires: timestamp("ban_expires"),
});

export const session = pgTable(
  "session",
  {
    id: text("id").primaryKey(),
    expiresAt: timestamp("expires_at").notNull(),
    token: text("token").notNull().unique(),
    createdAt: timestamp("created_at").defaultNow().notNull(),
    updatedAt: timestamp("updated_at")
      .$onUpdate(() => /* @__PURE__ */ new Date())
      .notNull(),
    ipAddress: text("ip_address"),
    userAgent: text("user_agent"),
    userId: text("user_id")
      .notNull()
      .references(() => user.id, { onDelete: "cascade" }),
    impersonatedBy: text("impersonated_by"),
    activeOrganizationId: text("active_organization_id"),
  },
  (table) => [index("session_userId_idx").on(table.userId)],
);

export const account = pgTable(
  "account",
  {
    id: text("id").primaryKey(),
    accountId: text("account_id").notNull(),
    providerId: text("provider_id").notNull(),
    userId: text("user_id")
      .notNull()
      .references(() => user.id, { onDelete: "cascade" }),
    accessToken: text("access_token"),
    refreshToken: text("refresh_token"),
    idToken: text("id_token"),
    accessTokenExpiresAt: timestamp("access_token_expires_at"),
    refreshTokenExpiresAt: timestamp("refresh_token_expires_at"),
    scope: text("scope"),
    password: text("password"),
    createdAt: timestamp("created_at").defaultNow().notNull(),
    updatedAt: timestamp("updated_at")
      .$onUpdate(() => /* @__PURE__ */ new Date())
      .notNull(),
  },
  (table) => [index("account_userId_idx").on(table.userId)],
);

export const verification = pgTable(
  "verification",
  {
    id: text("id").primaryKey(),
    identifier: text("identifier").notNull(),
    value: text("value").notNull(),
    expiresAt: timestamp("expires_at").notNull(),
    createdAt: timestamp("created_at").defaultNow().notNull(),
    updatedAt: timestamp("updated_at")
      .defaultNow()
      .$onUpdate(() => /* @__PURE__ */ new Date())
      .notNull(),
  },
  (table) => [index("verification_identifier_idx").on(table.identifier)],
);

export const organization = pgTable(
  "organization",
  {
    id: text("id").primaryKey(),
    name: text("name").notNull(),
    slug: text("slug").notNull().unique(),
    logo: text("logo"),
    createdAt: timestamp("created_at").notNull(),
    metadata: text("metadata"),
  },
  (table) => [uniqueIndex("organization_slug_uidx").on(table.slug)],
);

export const member = pgTable(
  "member",
  {
    id: text("id").primaryKey(),
    organizationId: text("organization_id")
      .notNull()
      .references(() => organization.id, { onDelete: "cascade" }),
    userId: text("user_id")
      .notNull()
      .references(() => user.id, { onDelete: "cascade" }),
    role: text("role").default("member").notNull(),
    createdAt: timestamp("created_at").notNull(),
  },
  (table) => [
    index("member_organizationId_idx").on(table.organizationId),
    index("member_userId_idx").on(table.userId),
  ],
);

export const invitation = pgTable(
  "invitation",
  {
    id: text("id").primaryKey(),
    organizationId: text("organization_id")
      .notNull()
      .references(() => organization.id, { onDelete: "cascade" }),
    email: text("email").notNull(),
    role: text("role"),
    status: text("status").default("pending").notNull(),
    expiresAt: timestamp("expires_at").notNull(),
    createdAt: timestamp("created_at").defaultNow().notNull(),
    inviterId: text("inviter_id")
      .notNull()
      .references(() => user.id, { onDelete: "cascade" }),
  },
  (table) => [
    index("invitation_organizationId_idx").on(table.organizationId),
    index("invitation_email_idx").on(table.email),
  ],
);

// ─────────────────────────────────────────────────────────────────────────────
// C3PO operational tables (SPEC §3 "Drizzle additions").
//
// New column types for this project — documented in CLAUDE.md:
//   • `jsonb`  — structured payloads (skill params/results, transcripts, poses).
//   • `vector(1024)` — pgvector embeddings, HNSW-indexed for similarity recall.
//     Requires `CREATE EXTENSION vector` — the migration enables it.
//
// IDs are app-generated text UUIDs (via `$defaultFn`), matching Better Auth's
// text-id convention. All tenant-scoped tables carry a nullable
// `organizationId` FK so they reuse the organization plugin's multi-tenancy.
//
// NOTE: `sessions` (plural, supervisor sessions below) is DISTINCT from Better
// Auth's `session` (singular, auth sessions above). Keep the two straight.
// ─────────────────────────────────────────────────────────────────────────────

/** A supervisor session — one operator run driving the robot (SPEC §12). */
export const sessions = pgTable(
  "sessions",
  {
    id: text("id")
      .primaryKey()
      .$defaultFn(() => crypto.randomUUID()),
    organizationId: text("organization_id").references(() => organization.id, {
      onDelete: "cascade",
    }),
    userId: text("user_id")
      .notNull()
      .references(() => user.id, { onDelete: "cascade" }),
    status: text("status", { enum: ["active", "idle", "ended"] })
      .default("active")
      .notNull(),
    summary: text("summary"),
    startedAt: timestamp("started_at").defaultNow().notNull(),
    endedAt: timestamp("ended_at"),
  },
  (table) => [
    index("sessions_organizationId_idx").on(table.organizationId),
    index("sessions_userId_idx").on(table.userId),
    index("sessions_status_idx").on(table.status),
  ],
);

/** Durable audit trail: one row per skill/tool dispatch to the bridge. */
export const toolCallLog = pgTable(
  "tool_call_log",
  {
    id: text("id")
      .primaryKey()
      .$defaultFn(() => crypto.randomUUID()),
    // Nullable + set-null: a direct skill invoke may have no session, and the
    // audit row must survive a session purge.
    sessionId: text("session_id").references(() => sessions.id, {
      onDelete: "set null",
    }),
    // Bridge task id (e.g. `tsk_…`) for long-running skills; not an FK.
    taskId: text("task_id"),
    skillName: text("skill_name").notNull(),
    params: jsonb("params").$type<Record<string, unknown>>(),
    result: jsonb("result").$type<Record<string, unknown>>(),
    status: text("status", {
      enum: ["pending", "running", "completed", "failed", "cancelled"],
    })
      .default("pending")
      .notNull(),
    // Bridge-reported environment the call ran against (mirrors SIM_MODE).
    env: text("env", { enum: ["stub", "isaac", "mujoco_local", "real"] }),
    startedAt: timestamp("started_at").defaultNow().notNull(),
    endedAt: timestamp("ended_at"),
  },
  (table) => [
    index("tool_call_log_sessionId_idx").on(table.sessionId),
    index("tool_call_log_skillName_idx").on(table.skillName),
    index("tool_call_log_startedAt_idx").on(table.startedAt),
  ],
);

/** A named place in the world the robot can recall by name or similarity. */
export const landmarks = pgTable(
  "landmarks",
  {
    id: text("id")
      .primaryKey()
      .$defaultFn(() => crypto.randomUUID()),
    organizationId: text("organization_id").references(() => organization.id, {
      onDelete: "cascade",
    }),
    name: text("name").notNull(),
    xyzWorld: jsonb("xyz_world")
      .$type<{ x: number; y: number; z: number }>()
      .notNull(),
    description: text("description"),
    embedding: vector("embedding", { dimensions: 1024 }),
    createdAt: timestamp("created_at").defaultNow().notNull(),
    lastSeenAt: timestamp("last_seen_at").defaultNow().notNull(),
  },
  (table) => [
    index("landmarks_organizationId_idx").on(table.organizationId),
    // HNSW cosine index for nearest-landmark recall; needs the `vector` extension.
    index("landmarks_embedding_idx").using(
      "hnsw",
      table.embedding.op("vector_cosine_ops"),
    ),
  ],
);

/** A summarised past run, embedded for "have I done this before?" recall. */
export const episodes = pgTable(
  "episodes",
  {
    id: text("id")
      .primaryKey()
      .$defaultFn(() => crypto.randomUUID()),
    organizationId: text("organization_id").references(() => organization.id, {
      onDelete: "cascade",
    }),
    sessionId: text("session_id").references(() => sessions.id, {
      onDelete: "set null",
    }),
    summary: text("summary"),
    transcript: jsonb("transcript").$type<unknown[]>(),
    outcome: text("outcome", {
      enum: ["success", "failure", "partial", "aborted"],
    }),
    embedding: vector("embedding", { dimensions: 1024 }),
    createdAt: timestamp("created_at").defaultNow().notNull(),
  },
  (table) => [
    index("episodes_organizationId_idx").on(table.organizationId),
    index("episodes_sessionId_idx").on(table.sessionId),
    index("episodes_embedding_idx").using(
      "hnsw",
      table.embedding.op("vector_cosine_ops"),
    ),
  ],
);

/** Hashed API tokens for the MCP adapter (SPEC §3 "MCP", Phase 5). */
export const mcpTokens = pgTable(
  "mcp_tokens",
  {
    id: text("id")
      .primaryKey()
      .$defaultFn(() => crypto.randomUUID()),
    organizationId: text("organization_id").references(() => organization.id, {
      onDelete: "cascade",
    }),
    name: text("name"),
    // Only the hash is stored — the plaintext token is shown once at creation.
    hashedToken: text("hashed_token").notNull(),
    scope: jsonb("scope").$type<string[]>(),
    createdAt: timestamp("created_at").defaultNow().notNull(),
    lastUsedAt: timestamp("last_used_at"),
  },
  (table) => [
    uniqueIndex("mcp_tokens_hashedToken_uidx").on(table.hashedToken),
    index("mcp_tokens_organizationId_idx").on(table.organizationId),
  ],
);

export const userRelations = relations(user, ({ many }) => ({
  sessions: many(session),
  accounts: many(account),
  members: many(member),
  invitations: many(invitation),
}));

export const sessionRelations = relations(session, ({ one }) => ({
  user: one(user, {
    fields: [session.userId],
    references: [user.id],
  }),
}));

export const accountRelations = relations(account, ({ one }) => ({
  user: one(user, {
    fields: [account.userId],
    references: [user.id],
  }),
}));

export const organizationRelations = relations(organization, ({ many }) => ({
  members: many(member),
  invitations: many(invitation),
}));

export const memberRelations = relations(member, ({ one }) => ({
  organization: one(organization, {
    fields: [member.organizationId],
    references: [organization.id],
  }),
  user: one(user, {
    fields: [member.userId],
    references: [user.id],
  }),
}));

export const invitationRelations = relations(invitation, ({ one }) => ({
  organization: one(organization, {
    fields: [invitation.organizationId],
    references: [organization.id],
  }),
  user: one(user, {
    fields: [invitation.inviterId],
    references: [user.id],
  }),
}));

// C3PO operational relations. Parent-side `many()` collectors are intentionally
// omitted (not needed by any query yet) to avoid colliding with Better Auth's
// `user.sessions` / `session` names; add them when a query needs them.

export const sessionsRelations = relations(sessions, ({ one, many }) => ({
  organization: one(organization, {
    fields: [sessions.organizationId],
    references: [organization.id],
  }),
  user: one(user, {
    fields: [sessions.userId],
    references: [user.id],
  }),
  toolCalls: many(toolCallLog),
  episodes: many(episodes),
}));

export const toolCallLogRelations = relations(toolCallLog, ({ one }) => ({
  session: one(sessions, {
    fields: [toolCallLog.sessionId],
    references: [sessions.id],
  }),
}));

export const landmarksRelations = relations(landmarks, ({ one }) => ({
  organization: one(organization, {
    fields: [landmarks.organizationId],
    references: [organization.id],
  }),
}));

export const episodesRelations = relations(episodes, ({ one }) => ({
  organization: one(organization, {
    fields: [episodes.organizationId],
    references: [organization.id],
  }),
  session: one(sessions, {
    fields: [episodes.sessionId],
    references: [sessions.id],
  }),
}));

export const mcpTokensRelations = relations(mcpTokens, ({ one }) => ({
  organization: one(organization, {
    fields: [mcpTokens.organizationId],
    references: [organization.id],
  }),
}));

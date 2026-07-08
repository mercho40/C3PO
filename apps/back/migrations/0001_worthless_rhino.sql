-- pgvector: the `vector` type + HNSW indexes below require this extension.
-- drizzle-kit does not emit it, so it is added here by hand. Needs a role with
-- CREATE privilege and the pgvector package installed on the server.
CREATE EXTENSION IF NOT EXISTS vector;--> statement-breakpoint
CREATE TABLE "episodes" (
	"id" text PRIMARY KEY NOT NULL,
	"organization_id" text,
	"session_id" text,
	"summary" text,
	"transcript" jsonb,
	"outcome" text,
	"embedding" vector(1024),
	"created_at" timestamp DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "landmarks" (
	"id" text PRIMARY KEY NOT NULL,
	"organization_id" text,
	"name" text NOT NULL,
	"xyz_world" jsonb NOT NULL,
	"description" text,
	"embedding" vector(1024),
	"created_at" timestamp DEFAULT now() NOT NULL,
	"last_seen_at" timestamp DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "mcp_tokens" (
	"id" text PRIMARY KEY NOT NULL,
	"organization_id" text,
	"name" text,
	"hashed_token" text NOT NULL,
	"scope" jsonb,
	"created_at" timestamp DEFAULT now() NOT NULL,
	"last_used_at" timestamp
);
--> statement-breakpoint
CREATE TABLE "sessions" (
	"id" text PRIMARY KEY NOT NULL,
	"organization_id" text,
	"user_id" text NOT NULL,
	"status" text DEFAULT 'active' NOT NULL,
	"summary" text,
	"started_at" timestamp DEFAULT now() NOT NULL,
	"ended_at" timestamp
);
--> statement-breakpoint
CREATE TABLE "tool_call_log" (
	"id" text PRIMARY KEY NOT NULL,
	"session_id" text,
	"task_id" text,
	"skill_name" text NOT NULL,
	"params" jsonb,
	"result" jsonb,
	"status" text DEFAULT 'pending' NOT NULL,
	"env" text,
	"started_at" timestamp DEFAULT now() NOT NULL,
	"ended_at" timestamp
);
--> statement-breakpoint
ALTER TABLE "episodes" ADD CONSTRAINT "episodes_organization_id_organization_id_fk" FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "episodes" ADD CONSTRAINT "episodes_session_id_sessions_id_fk" FOREIGN KEY ("session_id") REFERENCES "public"."sessions"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "landmarks" ADD CONSTRAINT "landmarks_organization_id_organization_id_fk" FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "mcp_tokens" ADD CONSTRAINT "mcp_tokens_organization_id_organization_id_fk" FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "sessions" ADD CONSTRAINT "sessions_organization_id_organization_id_fk" FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "sessions" ADD CONSTRAINT "sessions_user_id_user_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."user"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "tool_call_log" ADD CONSTRAINT "tool_call_log_session_id_sessions_id_fk" FOREIGN KEY ("session_id") REFERENCES "public"."sessions"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "episodes_organizationId_idx" ON "episodes" USING btree ("organization_id");--> statement-breakpoint
CREATE INDEX "episodes_sessionId_idx" ON "episodes" USING btree ("session_id");--> statement-breakpoint
CREATE INDEX "episodes_embedding_idx" ON "episodes" USING hnsw ("embedding" vector_cosine_ops);--> statement-breakpoint
CREATE INDEX "landmarks_organizationId_idx" ON "landmarks" USING btree ("organization_id");--> statement-breakpoint
CREATE INDEX "landmarks_embedding_idx" ON "landmarks" USING hnsw ("embedding" vector_cosine_ops);--> statement-breakpoint
CREATE UNIQUE INDEX "mcp_tokens_hashedToken_uidx" ON "mcp_tokens" USING btree ("hashed_token");--> statement-breakpoint
CREATE INDEX "mcp_tokens_organizationId_idx" ON "mcp_tokens" USING btree ("organization_id");--> statement-breakpoint
CREATE INDEX "sessions_organizationId_idx" ON "sessions" USING btree ("organization_id");--> statement-breakpoint
CREATE INDEX "sessions_userId_idx" ON "sessions" USING btree ("user_id");--> statement-breakpoint
CREATE INDEX "sessions_status_idx" ON "sessions" USING btree ("status");--> statement-breakpoint
CREATE INDEX "tool_call_log_sessionId_idx" ON "tool_call_log" USING btree ("session_id");--> statement-breakpoint
CREATE INDEX "tool_call_log_skillName_idx" ON "tool_call_log" USING btree ("skill_name");--> statement-breakpoint
CREATE INDEX "tool_call_log_startedAt_idx" ON "tool_call_log" USING btree ("started_at");
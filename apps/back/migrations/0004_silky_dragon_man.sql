ALTER TABLE "tool_call_log" DROP CONSTRAINT "tool_call_log_chat_id_chat_id_fk";
--> statement-breakpoint
ALTER TABLE "tool_call_log" ADD COLUMN "user_id" text;--> statement-breakpoint
ALTER TABLE "tool_call_log" ADD COLUMN "source" text;--> statement-breakpoint
ALTER TABLE "tool_call_log" ADD CONSTRAINT "tool_call_log_user_id_user_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."user"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "tool_call_log" ADD CONSTRAINT "tool_call_log_chat_id_chat_id_fk" FOREIGN KEY ("chat_id") REFERENCES "public"."chat"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "tool_call_log_created_idx" ON "tool_call_log" USING btree ("created_at","id");
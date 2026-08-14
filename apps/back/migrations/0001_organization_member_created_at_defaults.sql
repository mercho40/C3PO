-- Bring organization.created_at and member.created_at in line with every
-- other created_at column (user, session, account, verification all
-- default to now()). better-auth's organization plugin always supplies an
-- explicit value on insert, so this is defense-in-depth, not a behavior
-- change for the normal path -- it only matters if something ever inserts
-- a row without one.
ALTER TABLE "organization" ALTER COLUMN "created_at" SET DEFAULT now();
ALTER TABLE "member" ALTER COLUMN "created_at" SET DEFAULT now();

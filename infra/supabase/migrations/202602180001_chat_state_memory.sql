ALTER TABLE chat_state
ADD COLUMN IF NOT EXISTS memory jsonb;

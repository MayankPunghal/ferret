-- Ask conversations (docs/plan-answers.md step 3): one chat per row, its turns in order, each row owned by
-- the user who asked. Deleting a conversation deletes its turns; deleting a user deletes their conversations.

CREATE TABLE IF NOT EXISTS ask_conversations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       text NOT NULL DEFAULT '',
    model       text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ask_conversations_user ON ask_conversations (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS ask_messages (
    id              bigserial PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES ask_conversations(id) ON DELETE CASCADE,
    role            text NOT NULL CHECK (role IN ('user', 'assistant')),
    text            text NOT NULL DEFAULT '',
    citations       jsonb,
    thinking        text,
    outcome         text,
    model           text,
    seconds         double precision,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ask_messages_conversation ON ask_messages (conversation_id, id);

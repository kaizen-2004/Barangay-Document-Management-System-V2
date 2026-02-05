-- Run this ONLY if you already have an existing PostgreSQL database and want to keep data.
-- It adds new columns used by newer versions of the app.
--
-- This file is safe to run multiple times.
-- It also avoids hard errors if the target tables don't exist yet.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'document_types'
  ) THEN
    ALTER TABLE public.document_types
      ADD COLUMN IF NOT EXISTS description VARCHAR(255),
      ADD COLUMN IF NOT EXISTS template_path VARCHAR(255),
      ADD COLUMN IF NOT EXISTS requires_photo BOOLEAN NOT NULL DEFAULT FALSE;
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'residents'
  ) THEN
    ALTER TABLE public.residents
      ADD COLUMN IF NOT EXISTS photo_path VARCHAR(255);
  END IF;
END $$;

DO $$
BEGIN
  -- Residents: soft-delete + audit fields
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'residents'
  ) THEN
    ALTER TABLE public.residents
      ADD COLUMN IF NOT EXISTS created_by_id INTEGER,
      ADD COLUMN IF NOT EXISTS updated_by_id INTEGER,
      ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE,
      ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE,
      ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP WITHOUT TIME ZONE,
      ADD COLUMN IF NOT EXISTS archived_by_id INTEGER;
  END IF;

  -- Transaction logs: allow NULL user_id for immutable logs on user delete
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'transaction_logs'
  ) THEN
    ALTER TABLE public.transaction_logs
      ALTER COLUMN user_id DROP NOT NULL;
  END IF;

  -- Documents: workflow + soft-delete + audit fields
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'documents'
  ) THEN
    ALTER TABLE public.documents
      ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'draft',
      ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
      ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE,
      ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP WITHOUT TIME ZONE,
      ADD COLUMN IF NOT EXISTS issued_at TIMESTAMP WITHOUT TIME ZONE,
      ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE,
      ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP WITHOUT TIME ZONE,
      ADD COLUMN IF NOT EXISTS created_by_id INTEGER,
      ADD COLUMN IF NOT EXISTS updated_by_id INTEGER,
      ADD COLUMN IF NOT EXISTS approved_by_id INTEGER,
      ADD COLUMN IF NOT EXISTS issued_by_id INTEGER,
      ADD COLUMN IF NOT EXISTS archived_by_id INTEGER;

    UPDATE public.documents SET status='issued' WHERE status IS NULL;
    UPDATE public.documents SET created_at = issue_date WHERE created_at IS NULL;
    UPDATE public.documents SET issued_at = issue_date WHERE issued_at IS NULL AND status='issued';
  END IF;

  EXECUTE 'CREATE TABLE IF NOT EXISTS login_attempts (
    id SERIAL PRIMARY KEY,
    username VARCHAR(150),
    ip_address VARCHAR(64),
    success BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW() NOT NULL
  )';

  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'users'
  ) THEN
    EXECUTE 'CREATE TABLE IF NOT EXISTS login_mfa_codes (
      id SERIAL PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id),
      otp_code VARCHAR(20) NOT NULL,
      expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
      used BOOLEAN NOT NULL DEFAULT FALSE,
      created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW() NOT NULL
    )';
  END IF;
END $$;

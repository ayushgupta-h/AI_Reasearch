/**
 * Research AI Quest — Supabase Client & Google OAuth Integration
 */

import { createClient, type SupabaseClient } from '@supabase/supabase-js';

// Resolve configuration from window.__ENV__ or verified project defaults
const winEnv = (typeof window !== 'undefined' ? (window as unknown as { __ENV__?: Record<string, string> }).__ENV__ : null) || {};

export const SUPABASE_URL =
  winEnv.SUPABASE_URL ||
  winEnv.VITE_SUPABASE_URL ||
  (typeof window !== 'undefined' ? localStorage.getItem('quest_supabase_url') : null) ||
  'https://vxqalflbzpjkvmtbbqrv.supabase.co';

export const SUPABASE_ANON_KEY =
  winEnv.SUPABASE_ANON_KEY ||
  winEnv.VITE_SUPABASE_ANON_KEY ||
  (typeof window !== 'undefined' ? localStorage.getItem('quest_supabase_anon_key') : null) ||
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZ4cWFsZmxienBqa3ZtdGJicXJ2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzc3ODQyNzgsImV4cCI6MjA5MzM2MDI3OH0.Itqr5hTJLCULhkQr_pc6qg0h0dTWkJbb2CRdChCN5h4';

export const supabaseConfigured = Boolean(SUPABASE_URL && SUPABASE_ANON_KEY);

export const supabase: SupabaseClient = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
  auth: {
    persistSession: true,
    autoRefreshToken: true,
    detectSessionInUrl: true,
    storage: typeof window !== 'undefined' ? window.localStorage : undefined,
  },
});


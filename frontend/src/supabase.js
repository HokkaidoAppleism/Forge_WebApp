import { createClient } from '@supabase/supabase-js'

// The browser half of auth. This client's whole job is to get a signed access
// token and keep it fresh; it never talks to the question tables directly.
//
// It could -- supabase-js can query Postgres over PostgREST, and RLS would
// hold. It does not, because the reader needs the server to be the one that
// decides whether a buzz was right, and that decision cannot live in a page
// the player can edit. See web/api/routes/answers.py.
export const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_ANON_KEY,
  {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
    },
  },
)

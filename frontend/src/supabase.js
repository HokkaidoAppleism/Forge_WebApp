import { createClient } from '@supabase/supabase-js'

// The browser half of auth. This client's whole job is to get a signed access
// token and keep it fresh; it never talks to the question tables directly.
//
// It could -- supabase-js can query Postgres over PostgREST, and RLS would
// hold. It does not, because the reader needs the server to be the one that
// decides whether a buzz was right, and that decision cannot live in a page
// the player can edit. See web/api/routes/answers.py.

const REMEMBER_KEY = 'forgeqb.rememberMe'

// Unchecked by default: leaving "Keep me signed in" unticked should mean the
// session dies with the browser tab, not survive it. localStorage persists
// across restarts, sessionStorage does not -- so which one backs the actual
// Supabase session is picked per sign-in from the checkbox, via this flag.
let remember = localStorage.getItem(REMEMBER_KEY) === 'true'

/** Call before signing in (or signing up) with the login form's checkbox
 *  value, so this sign-in's session lands in the right backing store. */
export function setRememberMe(value) {
  remember = Boolean(value)
  localStorage.setItem(REMEMBER_KEY, String(remember))
}

// A storage adapter that reads/writes whichever backing store `remember`
// currently points at, decided at call time rather than baked in once at
// createClient -- the checkbox's choice on this sign-in has to take effect
// immediately, not on the next page load.
const authStorage = {
  getItem: (key) => (remember ? localStorage : sessionStorage).getItem(key),
  setItem: (key, value) => {
    (remember ? localStorage : sessionStorage).setItem(key, value)
    // Clears the store this sign-in isn't using, so switching the checkbox
    // between sign-ins can't leave a stale, still-valid session sitting in
    // the other one.
    ;(remember ? sessionStorage : localStorage).removeItem(key)
  },
  removeItem: (key) => {
    localStorage.removeItem(key)
    sessionStorage.removeItem(key)
  },
}

export const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_ANON_KEY,
  {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      storage: authStorage,
    },
  },
)

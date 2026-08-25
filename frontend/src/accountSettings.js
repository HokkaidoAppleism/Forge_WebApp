/**
 * Change email / change password, inside Settings.
 *
 * Both go straight to Supabase Auth (`supabase.auth.updateUser`), the same
 * client `main.js`'s sign-in form already uses -- there is no `web/api`
 * route for either and there should not be one. An account's email and
 * password are its identity with the auth provider, not app data scoped by
 * `user_id`; `web/api` only ever sees a *verified* identity (the JWT
 * `auth.py` checks on every request), never the credentials that produced
 * it, and changing them stays on that same side of the line.
 *
 * **Email changes are not instant.** Supabase's default flow sends a
 * confirmation link to the new address (and, with "secure email change" on,
 * a second one to the old address) before the change takes effect -- so
 * `updateUser({ email })` succeeding here means "the email was sent," not
 * "the email changed." The status message says so rather than implying it
 * already happened.
 *
 * **A password change can be refused for a stale session** -- Supabase asks
 * for a recent sign-in before allowing one, depending on project settings.
 * There is no re-authentication flow built here for that case; the error
 * comes back from Supabase with its own explanation, shown as written,
 * rather than this file guessing at a fix.
 */

import { supabase } from './supabase.js'
import { api } from './api.js'

const $ = (id) => document.getElementById(id)

/** @param {(username: string|null) => void} onUsernameChange called after a
 *  successful save, so the header's "signed in as" label can pick it up
 *  without this module needing to know where that label lives. */
export function initAccountSettings(onUsernameChange) {
  const el = {
    email: $('accountEmail'),
    newEmail: $('newEmailInput'), saveEmailBtn: $('saveEmailBtn'), emailMessage: $('emailMessage'),
    newPassword: $('newPasswordInput'), savePasswordBtn: $('savePasswordBtn'),
    passwordMessage: $('passwordMessage'),
    username: $('usernameInput'), saveUsernameBtn: $('saveUsernameBtn'),
    usernameMessage: $('usernameMessage'),
  }

  function setMessage(node, text, isError) {
    node.textContent = text
    node.className = `mt-1 text-xs ${isError ? 'text-red-400' : 'text-emerald-400'}`
  }

  el.saveEmailBtn.addEventListener('click', async () => {
    const next = el.newEmail.value.trim()
    if (!next) {
      setMessage(el.emailMessage, 'Enter the new email address first.', true)
      return
    }
    el.saveEmailBtn.disabled = true
    try {
      const { error } = await supabase.auth.updateUser({ email: next })
      if (error) throw error
      el.newEmail.value = ''
      // Not "Saved" -- nothing has actually changed yet, and saying so would
      // read as a lie the moment the player looks at accountEmail below and
      // sees the old address still there.
      setMessage(el.emailMessage,
        `Check ${next} for a confirmation link. Your email won't change until you click it.`,
        false)
    } catch (error) {
      setMessage(el.emailMessage, error.message, true)
    } finally {
      el.saveEmailBtn.disabled = false
    }
  })

  el.savePasswordBtn.addEventListener('click', async () => {
    const next = el.newPassword.value
    if (!next) {
      setMessage(el.passwordMessage, 'Enter a new password first.', true)
      return
    }
    if (next.length < 8) {
      setMessage(el.passwordMessage, 'Password must be at least 8 characters.', true)
      return
    }
    el.savePasswordBtn.disabled = true
    try {
      const { error } = await supabase.auth.updateUser({ password: next })
      if (error) throw error
      el.newPassword.value = ''
      setMessage(el.passwordMessage, 'Password changed.', false)
    } catch (error) {
      setMessage(el.passwordMessage, error.message, true)
    } finally {
      el.savePasswordBtn.disabled = false
    }
  })

  el.saveUsernameBtn.addEventListener('click', async () => {
    el.saveUsernameBtn.disabled = true
    try {
      const { username } = await api.saveUsername(el.username.value.trim())
      el.username.value = username ?? ''
      setMessage(el.usernameMessage, username ? 'Saved.' : 'Cleared — back to showing your email.', false)
      onUsernameChange?.(username)
    } catch (error) {
      setMessage(el.usernameMessage, error.message, true)
    } finally {
      el.saveUsernameBtn.disabled = false
    }
  })

  // Called each time Settings opens, alongside its other panels, so the
  // displayed address can't go stale after a confirmed email change.
  return async function loadAccountEmail() {
    setMessage(el.emailMessage, '', false)
    setMessage(el.passwordMessage, '', false)
    setMessage(el.usernameMessage, '', false)
    const { data: { session } } = await supabase.auth.getSession()
    el.email.textContent = session?.user?.email ?? '—'
    try {
      const { username } = await api.username()
      el.username.value = username ?? ''
    } catch (error) {
      console.error(error)
    }
  }
}

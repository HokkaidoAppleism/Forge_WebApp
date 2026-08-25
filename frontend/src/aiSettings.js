/**
 * The Gemini API key field inside Settings.
 *
 * Each account brings its own key -- see web/api/ai.py and
 * web/api/secrets_store.py for the full reasoning. The server can never show
 * the key back, only a hint of it (`configured`/`hint`, never the value) --
 * that part doesn't change here.
 *
 * **What this file caches, and why that's a different thing.** Without any
 * local cache, the box goes blank the instant you save and stays blank every
 * time Settings reopens, even though a key really is saved -- there's
 * nothing wrong, the box just has nothing to show. So the key you just typed
 * is kept in this browser's own `localStorage`, scoped to this account's
 * user id, and used to redisplay the box -- never fetched from the server.
 * That's a materially different thing from the server returning it: the
 * value was already sitting in this exact browser's memory the moment you
 * typed it, so keeping it around for your own convenience on your own
 * device doesn't hand it to a different session the way a network response
 * would. `configured`/`hint` from the server stay the source of truth for
 * *whether* a key is saved; the cached text is only ever a display
 * convenience, and is dropped the moment Remove or a real save supersedes it.
 */

import { api } from './api.js'
import { supabase } from './supabase.js'

const $ = (id) => document.getElementById(id)
const CACHE_PREFIX = 'forgeqb.geminiKeyCache.'

async function cacheKey() {
  const { data: { session } } = await supabase.auth.getSession()
  return session ? CACHE_PREFIX + session.user.id : null
}

export function initAiSettings() {
  const el = {
    status: $('aiKeyStatus'),
    input: $('aiKeyInput'),
    saveBtn: $('saveAiKeyBtn'),
    removeBtn: $('removeAiKeyBtn'),
    message: $('aiKeyMessage'),
  }

  function paintStatus(state) {
    el.status.textContent = state.configured
      ? `A key ending in …${state.hint} is saved.`
      : 'No key saved yet.'
    el.removeBtn.classList.toggle('hidden', !state.configured)
  }

  function setMessage(text, isError) {
    el.message.textContent = text
    el.message.className = `mt-2 text-xs ${isError ? 'text-red-400' : 'text-emerald-400'}`
  }

  el.saveBtn.addEventListener('click', async () => {
    const key = el.input.value.trim()
    if (!key) {
      setMessage('Paste your Gemini API key first.', true)
      return
    }
    el.saveBtn.disabled = true
    setMessage('Checking with Google…', false)
    try {
      // Verified against Google before it's stored -- see routes/settings.py
      // -- so a typo shows up here, next to the box, instead of as "study
      // guide generation failed" the first time an AI feature is tried.
      const saved = await api.saveAiKey(key)
      paintStatus(saved)
      setMessage('Saved.', false)
      const ck = await cacheKey()
      if (ck) localStorage.setItem(ck, key)
    } catch (error) {
      // A rejected key is not a setup mistake worth burying in a generic
      // banner -- Google's own reason (bad key vs. API not enabled) is what
      // routes/settings.py hands back, so show it as written.
      setMessage(error.message, true)
    } finally {
      el.saveBtn.disabled = false
    }
  })

  el.removeBtn.addEventListener('click', async () => {
    el.removeBtn.disabled = true
    try {
      const result = await api.deleteAiKey()
      paintStatus(result)
      setMessage('Key removed.', false)
      el.input.value = ''
      const ck = await cacheKey()
      if (ck) localStorage.removeItem(ck)
    } catch (error) {
      setMessage(error.message, true)
    } finally {
      el.removeBtn.disabled = false
    }
  })

  // Called once, when the Settings modal is opened, alongside its other
  // panels -- there is no separate "AI" tab, just this section of the form.
  return async function loadAiKeyStatus() {
    setMessage('', false)
    try {
      const state = await api.aiKeyStatus()
      paintStatus(state)
      // The cache is only trusted to *redisplay* a key the server confirms
      // is actually configured -- a stale cache from a key since removed (on
      // this device or another) must not make the box lie about what's live.
      const ck = await cacheKey()
      const cached = state.configured && ck ? localStorage.getItem(ck) : null
      el.input.value = cached ?? ''
    } catch (error) {
      setMessage(error.message, true)
    }
  }
}

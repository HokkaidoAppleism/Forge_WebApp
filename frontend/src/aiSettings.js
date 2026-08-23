/**
 * The Gemini API key field inside Settings.
 *
 * Each account brings its own key -- see web/api/ai.py and
 * web/api/secrets_store.py for the full reasoning. What that means here:
 * this panel can never show the key back, only a hint of it, and "Save"
 * reports whatever Google itself said about the key (wrong, disabled,
 * fine) rather than guessing from the shape of the string.
 */

import { api } from './api.js'

const $ = (id) => document.getElementById(id)

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
      el.input.value = ''
      paintStatus(saved)
      setMessage('Saved.', false)
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
      paintStatus(await api.aiKeyStatus())
    } catch (error) {
      setMessage(error.message, true)
    }
  }
}

/**
 * The Review Missed panel: the queue's four counts, and the three thresholds
 * that define them.
 *
 * The thresholds are not display preferences. `learnedAfterCorrect` and
 * `minCelerity` decide whether an answer advances a question toward Learned,
 * and `stuckAfterMissed` decides what the queue calls Stuck -- all three are
 * applied on the server, in `routes/answers.py` and `routes/review.py`, from
 * the same `review_settings.py`. This panel only reads and writes them; it
 * never re-implements the rule, which is why the counts are fetched back
 * after a save rather than adjusted here.
 *
 * **The slider is the one place the wording and the stored value differ.**
 * `minCelerity` is the fraction of the tossup still *unread* when the buzz
 * came, so 0.25 means "before the final quarter". Nobody thinks in that
 * direction, so the slider asks "buzz within the first 75%" and the two
 * conversions live here, together, where they can be read against each other.
 */

import { api } from './api.js'

const $ = (id) => document.getElementById(id)

/** A number out of a text field, or the default if the field is empty or
 *  gibberish. Deliberately does *not* clamp: `review_settings.clamp()` on the
 *  server already decides what is in range, and a second opinion here would
 *  mean a value typed in the box could be silently changed to something the
 *  server never saw. Typing 0 should come back as the server's floor of 1, not
 *  as whatever this file happens to think the default is. */
const numberOr = (value, fallback) => {
  const n = Number.parseInt(value, 10)
  return Number.isFinite(n) ? n : fallback
}

export function initReviewSettings({ onStartReviewing, onOpenReviewList }) {
  const el = {
    modal: $('reviewSettingsModal'),
    due: $('reviewDueCount'), toReview: $('reviewToReviewCount'),
    learned: $('reviewLearnedCount'), stuck: $('reviewStuckCount'),
    learnedCount: $('learnedCountInput'), stuckCount: $('stuckCountInput'),
    timing: $('learnedTimingRange'), timingDisplay: $('learnedTimingDisplay'),
    message: $('reviewSettingsMessage'),
    startBtn: $('startReviewFromSettingsBtn'),
    openListBtn: $('openReviewListBtn'),
    closeBtn: $('closeReviewSettingsBtn'), saveBtn: $('saveReviewSettingsBtn'),
  }

  function show(visible) {
    el.modal.classList.toggle('hidden', !visible)
    el.modal.classList.toggle('flex', visible)
  }

  el.timing.addEventListener('input', () => {
    el.timingDisplay.textContent = `${el.timing.value}%`
  })
  el.closeBtn.addEventListener('click', () => show(false))
  el.startBtn.addEventListener('click', () => { show(false); onStartReviewing() })
  el.openListBtn.addEventListener('click', () => { show(false); onOpenReviewList() })

  el.saveBtn.addEventListener('click', async () => {
    el.saveBtn.disabled = true
    el.message.textContent = ''
    try {
      const saved = await api.saveReviewSettings({
        learnedAfterCorrect: numberOr(el.learnedCount.value, 2),
        stuckAfterMissed: numberOr(el.stuckCount.value, 3),
        minCelerity: 1 - (numberOr(el.timing.value, 100) / 100),
      })
      // Draw what was stored, not what was typed: the server clamps, and a
      // panel still showing the rejected number looks like it saved.
      fill(saved)
      // Stuck is derived from a threshold that just changed, so the counts
      // are stale the moment the save lands. Refetched rather than adjusted
      // here, because working out which questions crossed the line is the
      // server's job and doing it twice is how the two answers diverge.
      await loadCounts()
      el.message.textContent = 'Saved.'
    } catch (error) {
      el.message.textContent = error.message
    } finally {
      el.saveBtn.disabled = false
    }
  })

  function fill(settings) {
    el.learnedCount.value = settings.learnedAfterCorrect
    el.stuckCount.value = settings.stuckAfterMissed
    const percent = Math.round((1 - settings.minCelerity) * 100)
    el.timing.value = String(percent)
    el.timingDisplay.textContent = `${percent}%`
  }

  async function loadCounts() {
    const counts = await api.reviewCounts()
    el.due.textContent = counts.due
    el.toReview.textContent = counts.toReview
    el.learned.textContent = counts.learned
    el.stuck.textContent = counts.stuck
  }

  return async function openReviewSettings() {
    el.message.textContent = ''
    show(true)
    try {
      // Both at once. They are independent reads and the panel needs both
      // before it means anything.
      const [settings] = await Promise.all([api.reviewSettings(), loadCounts()])
      fill(settings)
    } catch (error) {
      el.message.textContent = error.message
    }
  }
}

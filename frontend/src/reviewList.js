/**
 * The Missed Questions summary: the queue's four counts, a category scope,
 * and a Start Reviewing button. Matches the desktop's `reviewPage` header --
 * the actual list of questions is `browse.js`'s accordion underneath it,
 * defaulted to the "In review" status, which is what makes this single page
 * read as "missed questions" without needing a second list of its own.
 */

import { api } from './api.js'

const $ = (id) => document.getElementById(id)

const escapeHtml = (text) => String(text ?? '').replace(
  /[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))

function statTile(label, value) {
  return `
    <div class="rounded-lg bg-secondary-dark p-4 text-center">
      <div class="text-2xl font-bold">${escapeHtml(value)}</div>
      <div class="text-xs text-text-muted">${escapeHtml(label)}</div>
    </div>`
}

export function initReviewList({ onBack, onStartReviewing }) {
  const el = {
    backBtn: $('backFromReviewListBtn'),
    summary: $('reviewListSummary'), categoryFilter: $('reviewListCategoryFilter'),
    startBtn: $('startReviewFromListBtn'),
  }

  el.backBtn.addEventListener('click', onBack)
  el.startBtn.addEventListener('click', onStartReviewing)

  function updateStartLabel() {
    const category = el.categoryFilter.value
    el.startBtn.textContent = category && category !== 'all'
      ? `Review ${category}` : 'Start Reviewing'
  }
  el.categoryFilter.addEventListener('change', updateStartLabel)

  async function loadSummary() {
    try {
      const counts = await api.reviewCounts()
      // toReview counts everything not yet learned (due and stuck both fall
      // inside it), so the total is toReview + learned -- summing every tile
      // would double-count a stuck, overdue question three times over.
      const total = counts.toReview + counts.learned

      // An empty queue is the normal state for a new account -- and, since
      // negging a tossup stopped filing it here automatically, the normal
      // state for anyone who has not deliberately added one yet. Five tiles
      // reading zero above a button that can only fail says nothing about
      // how to change that, so say it instead of showing the scoreboard of
      // an empty list.
      if (total === 0) {
        el.summary.innerHTML = `
          <div class="col-span-full rounded-lg bg-secondary-dark p-6 text-center">
            <p class="font-bold">Nothing in your review list yet.</p>
            <p class="mt-2 text-sm text-text-muted">
              While you are reading a tossup, press
              <span class="font-bold text-amber-500">Add to Missed</span> once the
              question closes out to put it here. Anything you add comes back on a
              spaced-repetition schedule until you have relearned it.
            </p>
          </div>`
        el.startBtn.disabled = true
        el.startBtn.classList.add('opacity-50', 'cursor-not-allowed')
        el.startBtn.title = 'Add a question to your review list first'
      } else {
        el.startBtn.disabled = false
        el.startBtn.classList.remove('opacity-50', 'cursor-not-allowed')
        el.startBtn.title = ''
        el.summary.innerHTML =
          statTile('Due now', counts.due) +
          statTile("Haven't Reviewed", counts.toReview) +
          statTile('Relearned', counts.learned) +
          statTile('Stuck', counts.stuck) +
          statTile('Total', total)
      }

      const chosen = el.categoryFilter.value
      el.categoryFilter.innerHTML = '<option value="all">All categories</option>' +
        counts.categories.map((c) =>
          `<option value="${escapeHtml(c.category)}">` +
          `${escapeHtml(c.category)} (${c.waiting})</option>`).join('')
      // Counts only lists categories with something *unlearned* -- a
      // category filtered to "learned" alone would not appear, and choosing
      // it would otherwise leave the select blank. Fall back rather than
      // strand the picker on a value it no longer offers.
      if (chosen && ![...el.categoryFilter.options].some((o) => o.value === chosen)) {
        el.categoryFilter.value = 'all'
      } else {
        el.categoryFilter.value = chosen || 'all'
      }
      updateStartLabel()
    } catch (error) {
      el.summary.innerHTML = `<p class="text-red-400">${escapeHtml(error.message)}</p>`
    }
  }

  return function openReviewList() {
    loadSummary()
  }
}

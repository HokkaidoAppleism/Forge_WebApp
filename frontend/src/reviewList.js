/**
 * The review list: every question in the review queue, browsable rather than
 * only drillable.
 *
 * `GET /api/review/queue` has served this page's data since the queue itself
 * was built -- paging, a status filter, and a `stuck`/`timesMissed` flag on
 * each row, computed once server-side against the player's own threshold
 * (`review_settings.py`). This is the first page that renders it; until now
 * the only way into the queue from the UI was drilling it question by
 * question with no way to see what was in it or take one back out.
 *
 * Deliberately narrower than the desktop's "Browse All Questions" section,
 * which pages through the entire 169k-question set by status (unseen
 * included) with a text search on top. That is real scope on its own -- see
 * the note in NEXT_SESSION_PROMPT.md -- and this page does not attempt it.
 * It shows what is *in the queue*: at most a few hundred rows for the most
 * dedicated player, not 169,099.
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

/** A tossup is long; a list row is not the place to read the whole thing. */
function snippet(text, max = 220) {
  const clean = String(text ?? '').replace(/\s+/g, ' ').trim()
  return clean.length > max ? `${clean.slice(0, max).trimEnd()}…` : clean
}

const STATUSES = [
  { key: 'all', label: 'All' },
  { key: 'due', label: 'Due now' },
  { key: 'queued', label: 'To review' },
  { key: 'stuck', label: 'Stuck' },
  { key: 'learned', label: 'Learned' },
]

export function initReviewList({ onBack }) {
  const el = {
    screen: $('reviewListScreen'), backBtn: $('backFromReviewListBtn'),
    summary: $('reviewListSummary'), statusTabs: $('reviewListStatusTabs'),
    categoryFilter: $('reviewListCategoryFilter'),
    list: $('reviewListList'), paging: $('reviewListPaging'),
  }

  let status = 'queued'   // matches the desktop's default: what needs work,
                           // not the whole history of everything ever missed
  let page = 1

  el.backBtn.addEventListener('click', onBack)
  el.categoryFilter.addEventListener('change', () => { page = 1; load() })

  function statusTabs() {
    el.statusTabs.innerHTML = ''
    for (const s of STATUSES) {
      const button = document.createElement('button')
      button.textContent = s.label
      button.className = 'rounded-full px-4 py-2 text-sm font-bold ' +
        (s.key === status ? 'bg-[#efe0db] text-[#1d1816]' : 'bg-tertiary-dark')
      button.addEventListener('click', () => {
        status = s.key
        page = 1
        statusTabs()
        load()
      })
      el.statusTabs.append(button)
    }
  }

  async function load() {
    el.list.innerHTML = '<p class="text-text-muted">Loading…</p>'
    el.paging.innerHTML = ''

    let payload
    try {
      payload = await api.reviewQueue(page, status, el.categoryFilter.value)
    } catch (error) {
      el.list.innerHTML = `<p class="text-red-400">${escapeHtml(error.message)}</p>`
      return
    }

    renderList(payload.items)
    renderPaging(payload)
  }

  async function loadSummary() {
    try {
      const counts = await api.reviewCounts()
      el.summary.innerHTML =
        statTile('Due now', counts.due) +
        statTile('To review', counts.toReview) +
        statTile('Learned', counts.learned) +
        statTile('Stuck', counts.stuck)

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
    } catch (error) {
      el.summary.innerHTML = `<p class="text-red-400">${escapeHtml(error.message)}</p>`
    }
  }

  function renderList(items) {
    el.list.innerHTML = ''
    if (!items.length) {
      el.list.innerHTML = page > 1
        ? '<p class="text-text-muted">Nothing on this page.</p>'
        : '<p class="mt-4 text-center text-text-muted">Nothing here. ' +
          'Miss a tossup and it will show up in this list.</p>'
      return
    }
    for (const item of items) el.list.append(row(item))
  }

  function row(item) {
    const node = document.createElement('div')
    node.className = 'rounded-lg bg-secondary-dark p-4'

    const badges = []
    if (item.learned_at) {
      badges.push('<span class="rounded-full bg-green-700 px-2 py-0.5 text-xs font-bold">Learned</span>')
    } else if (item.stuck) {
      badges.push('<span class="rounded-full bg-amber-700 px-2 py-0.5 text-xs font-bold">Stuck</span>')
    }
    if (!item.learned_at && (!item.sm2_due || new Date(item.sm2_due) <= new Date())) {
      badges.push('<span class="rounded-full bg-[#f6b17a] px-2 py-0.5 text-xs font-bold text-[#1d1816]">Due now</span>')
    }

    // A subcategory that repeats its own category (Mythology, Geography,
    // Philosophy) is not a second fact worth showing -- same dedup the
    // desktop's category picker already applies.
    const meta = [item.category,
                  item.subcategory !== item.category ? item.subcategory : null,
                  `difficulty ${item.difficulty}`]
      .filter(Boolean).join(' · ')

    const lastAnswer = item.history?.length
      ? item.history[item.history.length - 1] : null

    node.innerHTML = `
      <div class="mb-2 flex flex-wrap items-start justify-between gap-2">
        <div class="text-xs text-text-muted">${escapeHtml(meta)}</div>
        <div class="flex flex-wrap gap-1">${badges.join('')}</div>
      </div>
      <p class="mb-1 text-sm font-bold text-[#f6b17a]">${escapeHtml(item.answer)}</p>
      <p class="mb-3 text-sm text-text-muted">${escapeHtml(snippet(item.question))}</p>
      <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-muted">
        <span>${item.attempts} attempt${item.attempts === 1 ? '' : 's'}</span>
        <span>${item.timesMissed} missed</span>
        <span>streak ${item.correct_streak}</span>
        ${lastAnswer ? `<span>last guess: “${escapeHtml(lastAnswer.guess ?? '(blank)')}” — ` +
          `${lastAnswer.correct ? 'correct' : 'wrong'}</span>` : ''}
      </div>`

    const remove = document.createElement('button')
    remove.textContent = 'Remove from review'
    remove.className = 'mt-3 rounded-full bg-red-700 px-3 py-1 text-xs font-bold ' +
      'text-white hover:bg-red-800'
    remove.addEventListener('click', async () => {
      if (!confirm(`Remove “${item.answer}” from your review list? ` +
                   'This also clears its answer history here. It comes back ' +
                   'automatically the next time you neg it.')) return
      remove.disabled = true
      try {
        await api.removeFromReview(item.question_id)
        await Promise.all([load(), loadSummary()])
      } catch (error) {
        remove.disabled = false
        alert(error.message)
      }
    })
    node.append(remove)
    return node
  }

  function renderPaging({ hasMore }) {
    el.paging.innerHTML = ''
    if (page === 1 && !hasMore) return

    const step = (delta, label, disabled) => {
      const button = document.createElement('button')
      button.textContent = label
      button.disabled = disabled
      button.className = 'rounded-full bg-tertiary-dark px-4 py-2 text-sm font-bold disabled:opacity-30'
      button.addEventListener('click', () => { page += delta; load() })
      return button
    }

    const label = document.createElement('span')
    label.className = 'text-sm text-text-muted'
    label.textContent = `Page ${page}`

    el.paging.append(step(-1, '← Previous', page <= 1), label,
                     step(1, 'Next →', !hasMore))
  }

  statusTabs()

  return function openReviewList() {
    page = 1
    loadSummary()
    load()
  }
}

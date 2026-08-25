/**
 * Browse all questions: the whole set, labelled with where you stand on it.
 *
 * The counterpart to `reviewList.js`. That page shows what is *in the queue* --
 * a few hundred rows at most, everything you have missed. This one pages
 * through all 169,056, so the set reads as a library rather than as a list of
 * failures, which is the same reason the desktop has both.
 *
 * **Search is capped at 3 characters, by the server.** `answer ilike '%x%'`
 * is served by a trigram index (see 0006_question_search.sql), and pg_trgm has
 * nothing to look up below three characters -- so a shorter term would fall
 * back to the sequential scan the index exists to avoid. The box says so
 * rather than firing a request that comes back 400.
 *
 * Typing is debounced for the same reason: one request per keystroke across a
 * 169k-row table is the kind of thing that looks fine locally and takes a
 * hosted database down.
 */

import { api } from './api.js'

const $ = (id) => document.getElementById(id)

const escapeHtml = (text) => String(text ?? '').replace(
  /[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))

/** A tossup is long; a list row is not the place to read the whole thing. */
function snippet(text, max = 220) {
  const clean = String(text ?? '').replace(/\s+/g, ' ').trim()
  return clean.length > max ? `${clean.slice(0, max).trimEnd()}…` : clean
}

const STATUSES = [
  { key: 'all', label: 'All' },
  { key: 'unseen', label: 'Unseen' },
  { key: 'queued', label: 'To review' },
  { key: 'stuck', label: 'Stuck' },
  { key: 'learned', label: 'Learned' },
]

// Matches the server's own floor -- see the module note above.
const MIN_SEARCH = 3
const DEBOUNCE_MS = 350

export function initBrowse({ onBack }) {
  const el = {
    screen: $('browseScreen'), backBtn: $('backFromBrowseBtn'),
    statusTabs: $('browseStatusTabs'), categoryFilter: $('browseCategoryFilter'),
    search: $('browseSearch'), searchNote: $('browseSearchNote'),
    list: $('browseList'), paging: $('browsePaging'),
  }

  let status = 'all'
  let page = 1
  let typingTimer = null
  // Bumped on every load; a response whose token is stale is dropped. Without
  // it, typing fast can land an earlier, slower response after a later one
  // and leave the list showing results for a term no longer in the box.
  let requestToken = 0

  el.backBtn.addEventListener('click', onBack)
  el.categoryFilter.addEventListener('change', () => { page = 1; load() })

  el.search.addEventListener('input', () => {
    clearTimeout(typingTimer)
    typingTimer = setTimeout(() => { page = 1; load() }, DEBOUNCE_MS)
  })

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
    const term = el.search.value.trim()
    // Said here rather than let through to a 400: a half-typed word is the
    // normal state of a search box, not an error the user made.
    if (term && term.length < MIN_SEARCH) {
      el.searchNote.textContent =
        `Type at least ${MIN_SEARCH} characters to search.`
      el.searchNote.classList.remove('hidden')
      return
    }
    el.searchNote.classList.add('hidden')

    const token = ++requestToken
    el.list.innerHTML = '<p class="text-text-muted">Loading…</p>'
    el.paging.innerHTML = ''

    let payload
    try {
      payload = await api.browseQuestions({
        page, status, q: term,
        category: el.categoryFilter.value === 'all' ? '' : el.categoryFilter.value,
      })
    } catch (error) {
      if (token !== requestToken) return
      el.list.innerHTML = `<p class="text-red-400">${escapeHtml(error.message)}</p>`
      return
    }
    if (token !== requestToken) return

    renderList(payload.items, term)
    renderPaging(payload)
  }

  /** The category picker is filled from the reader's own filter tree, so it
   *  offers every category in the set -- not just the ones already played,
   *  which is what the review page's counts-derived list gives. */
  async function loadCategories() {
    try {
      // `/api/questions/filters` answers `{categories: [...]}`, each with its
      // own question count -- shown here because "History (37,899)" tells you
      // what a filter is about to do in a way the bare name does not.
      const { categories } = await api.filters()
      const chosen = el.categoryFilter.value
      el.categoryFilter.innerHTML = '<option value="all">All categories</option>' +
        (categories ?? []).map((c) =>
          `<option value="${escapeHtml(c.category)}">` +
          `${escapeHtml(c.category)} (${c.questions.toLocaleString()})</option>`).join('')
      if (chosen) el.categoryFilter.value = chosen
    } catch {
      // A picker that failed to fill still leaves "All categories" usable,
      // which is the default anyway -- not worth blocking the page over.
    }
  }

  function renderList(items, term) {
    el.list.innerHTML = ''
    if (!items.length) {
      el.list.innerHTML = term
        ? `<p class="mt-4 text-center text-text-muted">Nothing matches “${escapeHtml(term)}”.</p>`
        : page > 1
          ? '<p class="text-text-muted">Nothing on this page.</p>'
          : '<p class="mt-4 text-center text-text-muted">Nothing here yet.</p>'
      return
    }
    for (const item of items) el.list.append(row(item))
  }

  const BADGES = {
    learned: '<span class="rounded-full bg-green-700 px-2 py-0.5 text-xs font-bold">Learned</span>',
    stuck: '<span class="rounded-full bg-amber-700 px-2 py-0.5 text-xs font-bold">Stuck</span>',
    queued: '<span class="rounded-full bg-tertiary-dark px-2 py-0.5 text-xs font-bold">To review</span>',
    unseen: '<span class="rounded-full bg-[#3e322e] px-2 py-0.5 text-xs font-bold text-text-muted">Unseen</span>',
  }

  function row(item) {
    const node = document.createElement('div')
    node.className = 'rounded-lg bg-secondary-dark p-4'

    // A subcategory that repeats its own category (Mythology, Geography,
    // Philosophy) is not a second fact worth showing -- same dedup the
    // review list and the desktop's category picker already apply.
    const meta = [item.category,
                  item.subcategory !== item.category ? item.subcategory : null,
                  item.difficulty != null ? `difficulty ${item.difficulty}` : null,
                  item.set_name]
      .filter(Boolean).join(' · ')

    const seen = item.status !== 'unseen'

    node.innerHTML = `
      <div class="mb-2 flex flex-wrap items-start justify-between gap-2">
        <div class="text-xs text-text-muted">${escapeHtml(meta)}</div>
        <div class="flex flex-wrap gap-1">
          ${BADGES[item.status] ?? ''}
          ${item.bookmarked
            ? '<span class="rounded-full bg-sky-800 px-2 py-0.5 text-xs font-bold">Bookmarked</span>' : ''}
        </div>
      </div>
      <p class="mb-1 text-sm font-bold text-[#f6b17a]">${escapeHtml(item.answer ?? '(no answerline)')}</p>
      <p class="${seen ? 'mb-3' : ''} text-sm text-text-muted">${escapeHtml(snippet(item.question))}</p>
      ${seen ? `
        <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-muted">
          <span>${item.attempts} attempt${item.attempts === 1 ? '' : 's'}</span>
          <span>${item.times_missed} missed</span>
          ${item.correct_streak != null ? `<span>streak ${item.correct_streak}</span>` : ''}
        </div>` : ''}`

    // Only for questions not already tracked: the review page owns removal,
    // and offering "add" on something already queued would be a no-op button.
    if (item.status === 'unseen') {
      const add = document.createElement('button')
      add.textContent = 'Add to review'
      add.className = 'mt-3 rounded-full bg-tertiary-dark px-3 py-1 text-xs font-bold hover:bg-[#4b3c37]'
      add.addEventListener('click', async () => {
        add.disabled = true
        try {
          await api.addToReview(item.id)
          add.textContent = 'Added'
        } catch (error) {
          add.textContent = error.message
          add.disabled = false
        }
      })
      node.append(add)
    }
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

  return function showBrowse() {
    loadCategories()
    load()
  }
}

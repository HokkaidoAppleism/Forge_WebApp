/**
 * Browse all questions: the whole set, labelled with where you stand on it.
 *
 * This is the desktop's own "Browse All Questions" section, one accordion
 * list defaulted to the "In review" status -- which is what makes it double
 * as the Missed Questions list (see reviewList.js, which only draws the
 * summary tiles above this). Rows are `<details>`/`<summary>`: a tight one-line
 * summary (answer, seen/streak/schedule, status badge) with the full tossup
 * text underneath, collapsed until clicked, matching the desktop instead of
 * showing every question's full text inline all the time.
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

// Matches the server's own floor -- see the module note above.
const MIN_SEARCH = 3
const DEBOUNCE_MS = 350
// Matches the server's own page size (routes/questions.py's _BROWSE_PAGE),
// needed here only to say which rows a page actually shows.
const PAGE_SIZE = 25

// Labels and pill colors, ported verbatim from the desktop's BROWSE_BADGES --
// "Never seen" and "Haven't Reviewed" read as two different facts (never
// drilled at all vs. drilled and currently missed) and the web port used to
// blur them into one "Unseen" / "To review" pair.
const BADGES = {
  unseen: ['Never seen', 'bg-[#3e322e] text-text-muted'],
  queued: ["Haven't Reviewed", 'bg-amber-700 text-white'],
  stuck: ['Stuck', 'bg-red-700 text-white'],
  learned: ['Relearned', 'bg-emerald-700 text-white'],
}

/** When SM-2 wants this question back, in words. Relearned questions have
 *  left the rotation, so a date for them would be misleading. A question
 *  that has never been queued has no schedule at all. */
function scheduleLabel(item) {
  if (item.status === 'learned') return 'not scheduled'
  if (item.status === 'unseen') return 'not queued'
  if (!item.sm2_due) return 'due now'
  const due = new Date(item.sm2_due)
  if (Number.isNaN(due.getTime())) return 'due now'
  const days = Math.ceil((due - Date.now()) / 86400000)
  if (days <= 0) return 'due now'
  if (days === 1) return 'due tomorrow'
  return `due in ${days} days`
}

/** "What you answered" for one row -- ported from the desktop's
 *  `answerHistoryEl`. An empty history has two different causes and they
 *  must not read the same: attempts with nothing recorded were answered
 *  before this history existed, and a genuinely fresh bookmark has none. */
function answerHistoryEl(item) {
  const wrap = document.createElement('div')
  wrap.className = 'mt-3 border-t border-[#584741] pt-2'

  const heading = document.createElement('p')
  heading.className = 'mb-1 text-xs uppercase tracking-wide text-text-muted'
  heading.textContent = 'What you answered'
  wrap.append(heading)

  const history = item.history || []
  if (!history.length) {
    const note = document.createElement('p')
    note.className = 'text-xs text-text-muted'
    note.textContent = item.attempts
      ? `Answered ${item.attempts}× before this was being recorded, so those ` +
        'guesses weren’t kept. Your next attempt will show here.'
      : 'No attempts yet — your next one will show here.'
    wrap.append(note)
    return wrap
  }

  const list = document.createElement('ol')
  list.className = 'space-y-0.5'
  history.forEach((h, i) => {
    const li = document.createElement('li')
    li.className = 'flex items-baseline gap-2 text-xs'
    const at = h.at ? new Date(h.at) : null
    const when = at && !Number.isNaN(at.getTime()) ? at.toLocaleDateString() : ''
    // celerity is the fraction of the tossup still *unread* at the buzz, so
    // 0.8 unread means the buzz came a fifth of the way in.
    const into = (h.celerity === null || h.celerity === undefined)
      ? '' : `${Math.round((1 - h.celerity) * 100)}% in`
    li.innerHTML =
      `<span class="w-4 flex-shrink-0 tabular-nums text-text-muted">${i + 1}.</span>` +
      `<span class="flex-shrink-0 ${h.correct ? 'text-green-400' : 'text-red-400'}">${h.correct ? '✓' : '✗'}</span>` +
      `<span class="min-w-0 flex-1 break-words ${h.guess ? '' : 'italic text-text-muted'}">` +
      `${h.guess ? escapeHtml(h.guess) : 'no answer given'}</span>` +
      (into ? `<span class="flex-shrink-0 tabular-nums text-text-muted" title="How far into the tossup you buzzed">${into}</span>` : '') +
      (when ? `<span class="flex-shrink-0 text-text-muted">${escapeHtml(when)}</span>` : '')
    list.append(li)
  })
  wrap.append(list)

  // `attempts` counts everything; the history only starts where it starts --
  // said explicitly rather than letting the numbering imply earlier guesses
  // never happened.
  const missing = (item.attempts || 0) - history.length
  if (missing > 0) {
    const gap = document.createElement('p')
    gap.className = 'mt-1 text-xs italic text-text-muted'
    gap.textContent = `${missing} earlier attempt${missing === 1 ? '' : 's'} ` +
      'weren’t recorded, so this list starts partway through.'
    wrap.append(gap)
  }
  return wrap
}

export function initBrowse() {
  const el = {
    categoryFilter: $('browseCategoryFilter'),
    status: $('browseStatus'),
    search: $('browseSearch'), searchNote: $('browseSearchNote'),
    list: $('browseList'), paging: $('browsePaging'),
  }

  let page = 1
  let typingTimer = null
  // Bumped on every load; a response whose token is stale is dropped. Without
  // it, typing fast can land an earlier, slower response after a later one
  // and leave the list showing results for a term no longer in the box.
  let requestToken = 0

  el.categoryFilter.addEventListener('change', () => { page = 1; load() })
  el.status.addEventListener('change', () => { page = 1; load() })

  el.search.addEventListener('input', () => {
    clearTimeout(typingTimer)
    typingTimer = setTimeout(() => { page = 1; load() }, DEBOUNCE_MS)
  })

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
        page, status: el.status.value, q: term,
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

  function row(item) {
    const [label, cls] = BADGES[item.status] ?? BADGES.unseen
    const seen = item.status !== 'unseen'

    // The compact accordion row the desktop has always used: the whole
    // summary line is the toggle, the tossup text sits collapsed underneath
    // until clicked.
    const node = document.createElement('details')
    node.className = 'rounded-lg bg-secondary-dark'

    const summary = document.createElement('summary')
    summary.className = 'flex cursor-pointer select-none items-center gap-2 px-4 py-3'
    summary.innerHTML =
      `<span class="flex-1 truncate font-bold text-[#f6b17a]">${escapeHtml(item.answer ?? '(no answerline)')}</span>` +
      // seen / streak / schedule -- the same line the desktop keeps on the
      // *summary* itself, visible without opening the row, rather than
      // buried in the collapsed body.
      (seen
        ? `<span class="hidden flex-shrink-0 text-xs text-text-muted sm:inline">` +
          `seen ${item.attempts}× · streak ${item.correct_streak ?? 0} · ${escapeHtml(scheduleLabel(item))}</span>`
        : '') +
      (item.difficulty != null
        ? `<span class="hidden flex-shrink-0 text-xs text-text-muted md:inline">diff ${item.difficulty}</span>` : '') +
      (item.bookmarked
        ? `<span class="flex-shrink-0 rounded-full bg-sky-800 px-2 py-0.5 text-xs font-bold text-white" title="You added this yourself rather than missing it">Bookmarked</span>`
        : '') +
      `<span class="flex-shrink-0 rounded-full px-2 py-0.5 text-xs font-bold ${cls}">${label}</span>`
    node.append(summary)

    const body = document.createElement('div')
    body.className = 'px-4 pb-4 text-sm text-text-light'

    // A subcategory that repeats its own category (Mythology, Geography,
    // Philosophy) is not a second fact worth showing.
    const meta = [item.category,
                  item.subcategory !== item.category ? item.subcategory : null,
                  item.set_name]
      .filter(Boolean).join(' · ')
    if (meta) {
      const metaEl = document.createElement('p')
      metaEl.className = 'mb-2 text-xs text-text-muted'
      metaEl.textContent = meta
      body.append(metaEl)
    }
    if (seen) {
      const statsEl = document.createElement('p')
      statsEl.className = 'mb-2 text-xs text-text-muted sm:hidden'
      statsEl.textContent = `seen ${item.attempts}× · ${item.times_missed} missed · ` +
        `streak ${item.correct_streak ?? 0} · ${scheduleLabel(item)}`
      body.append(statsEl)
    }
    const qText = document.createElement('p')
    qText.className = 'mb-3'
    qText.textContent = item.question ?? ''
    body.append(qText)

    // "What you answered" underneath the tossup itself -- the point of
    // opening a row is reading the question again *and* seeing how you've
    // actually been doing against it, the same two things the desktop's
    // accordion shows here.
    if (seen) body.append(answerHistoryEl(item))

    // Only for questions not already tracked: the review list otherwise owns
    // removal, and offering "add" on something already queued would be a
    // no-op button.
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
      body.append(add)
    } else {
      const remove = document.createElement('button')
      remove.textContent = 'Remove from review'
      remove.className = 'mt-3 rounded-full bg-red-700 px-3 py-1 text-xs font-bold text-white hover:bg-red-800'
      remove.addEventListener('click', async () => {
        if (!confirm(`Remove “${item.answer}” from your review list? ` +
                     'This also clears its answer history here. It comes back ' +
                     'automatically the next time you neg it.')) return
        remove.disabled = true
        try {
          await api.removeFromReview(item.id)
          load()
        } catch (error) {
          remove.disabled = false
          alert(error.message)
        }
      })
      body.append(remove)
    }
    node.append(body)
    return node
  }

  function renderPaging({ items, hasMore }) {
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

    // "Page 9" nine pages into the full set is 225 of 169,099 -- fine once it
    // says so, alarming when it doesn't. Matches the desktop's own wording.
    const first = items.length ? (page - 1) * PAGE_SIZE + 1 : 0
    const last = (page - 1) * PAGE_SIZE + items.length
    const label = document.createElement('span')
    label.className = 'text-sm text-text-muted'
    label.textContent = items.length
      ? `Page ${page} · showing ${first}–${last}${hasMore ? '' : ` of ${last}`}`
      : `Page ${page}`

    el.paging.append(step(-1, '← Previous', page <= 1), label,
                     step(1, 'Next →', !hasMore))
  }

  return function showBrowse() {
    loadCategories()
    load()
  }
}

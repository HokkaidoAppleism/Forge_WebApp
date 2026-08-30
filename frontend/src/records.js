/**
 * The records book: every Adaptive Learning sitting saved with "Save & Quit".
 *
 * `category_user_state` only ever holds the *latest* skill per subject, so
 * these summary rows are the only history there is -- which is why they are
 * written at the end of a sitting rather than derived on the way out here.
 *
 * Clicking a record expands its stats inline, in a single detail panel below
 * the list -- matching the desktop's own records page, and not navigating to
 * the Profile screen the way this used to. The chart-drawing itself is still
 * one implementation, not two: `profile.js` exports `initSessionPanels` for
 * exactly this, reusing the same `PANELS` table and draw functions the
 * Profile screen uses, just targeting this page's own elements.
 *
 * Not every record can do that. A sitting saved before anything tracked a
 * session id has no answers to point at, so its row says so instead of
 * offering a button that would open an empty panel.
 */

import { api } from './api.js'
import { initSessionPanels } from './profile.js'

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

/** Timestamps arrive as ISO strings in UTC; the player reads them in their
 *  own zone, which is the only zone they can check against their memory. */
function when(iso) {
  if (!iso) return ''
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString()
}

export function initRecords({ onBack }) {
  const el = {
    screen: $('recordsScreen'), backBtn: $('backFromRecordsBtn'),
    summary: $('recordsSummary'), filter: $('recordsCategoryFilter'),
    list: $('recordsList'), paging: $('recordsPaging'),
    detail: $('recordsDetail'), detailTitle: $('recordsDetailTitle'),
    closeDetailBtn: $('closeRecordsDetailBtn'), detailStats: $('recordsDetailStats'),
    detailPicker: $('recordsDetailPicker'), detailSubPicker: $('recordsDetailSubPicker'),
    detailPanelTitle: $('recordsDetailPanelTitle'), detailPanelWhat: $('recordsDetailPanelWhat'),
    detailView: $('recordsDetailView'), detailFinding: $('recordsDetailFinding'),
  }

  const showSessionPanels = initSessionPanels({
    statPicker: el.detailPicker, statSubPicker: el.detailSubPicker, statView: el.detailView,
    statAboutTitle: el.detailPanelTitle, statAboutWhat: el.detailPanelWhat,
    statAboutFinding: el.detailFinding,
  })

  let page = 1

  // Same shape as browse.js's `pageCache`: paging Previous/Next, or flipping
  // the category filter back to one already seen this visit, used to pay a
  // fresh round trip every time even though the page had just shown that
  // exact list. Keyed on everything that changes the result set, warmed for
  // whichever page is next in reach right after a page renders, and dropped
  // whenever a session is deleted (every cached page's counts and "which page
  // a row falls on" are stale the instant a row leaves the table) or the
  // screen is opened fresh (see `openRecords` below).
  const pageCache = new Map()
  const keyFor = (p, category) => JSON.stringify({ page: p, category })
  function fetchPage(p, category) { return api.adaptiveSessions(category, p) }
  function prefetchNeighbors(category, hasMore) {
    const targets = page > 1 ? [page - 1] : []
    if (hasMore) targets.push(page + 1)
    for (const p of targets) {
      const key = keyFor(p, category)
      if (pageCache.has(key)) continue
      fetchPage(p, category).then((payload) => pageCache.set(key, payload)).catch(() => {})
    }
  }

  el.backBtn.addEventListener('click', onBack)
  el.filter.addEventListener('change', () => { page = 1; load() })
  el.closeDetailBtn.addEventListener('click', () => el.detail.classList.add('hidden'))

  async function openDetail(session) {
    el.detail.classList.remove('hidden')
    el.detailTitle.textContent = `${session.category} — ${when(session.endedAt)}`
    el.detail.scrollIntoView({ behavior: 'smooth', block: 'nearest' })

    el.detailStats.innerHTML = statTile('Tossups', '…')
    try {
      // Points, P/T/N and celerity aren't on the row itself -- only this
      // sitting's session id is -- so they come from the same session-scoped
      // lifetime endpoint the old profile-page detour used.
      const { lifetime } = await api.stats('', session.sessionId)
      const start = Number(session.startDifficulty ?? 0)
      const end = Number(session.endDifficulty ?? 0)
      el.detailStats.innerHTML =
        statTile('Tossups', lifetime.tossups) +
        statTile('Points', lifetime.points) +
        statTile('P / T / N', `${lifetime.powers} / ${lifetime.tens} / ${lifetime.negs}`) +
        statTile('Avg celerity', lifetime.averageCelerity === null
          ? '0.000' : lifetime.averageCelerity.toFixed(3)) +
        statTile('Difficulty', `${start.toFixed(1)} → ${end.toFixed(1)}`)
    } catch (error) {
      el.detailStats.innerHTML = `<p class="text-red-400">${escapeHtml(error.message)}</p>`
    }
    showSessionPanels(session.sessionId)
  }

  async function load() {
    // A filtered-out or deleted session can't stay open behind the list.
    el.detail.classList.add('hidden')

    const category = el.filter.value || 'all'
    const key = keyFor(page, category)
    const cached = pageCache.get(key)
    if (cached) {
      paint(cached)
      prefetchNeighbors(category, cached.hasMore)
      return
    }

    el.list.innerHTML = '<p class="text-text-muted">Loading records…</p>'
    el.paging.innerHTML = ''

    let payload
    try {
      payload = await fetchPage(page, category)
    } catch (error) {
      el.list.innerHTML = `<p class="text-red-400">${escapeHtml(error.message)}</p>`
      el.summary.innerHTML = ''
      return
    }

    pageCache.set(key, payload)
    paint(payload)
    prefetchNeighbors(category, payload.hasMore)
  }

  function paint(payload) {
    const { sessions, summary } = payload
    el.summary.innerHTML =
      statTile('Sessions', summary.totalSessions) +
      statTile('Questions', summary.totalQuestions) +
      statTile('Correct', summary.totalCorrect) +
      statTile('Accuracy', `${summary.accuracy}%`)

    renderFilter(summary.categories)
    renderList(sessions)
    renderPaging(payload)
  }

  /** The picker is rebuilt from every category ever played, never from the
   *  filtered rows. Deleting the last session in a category does drop it, and
   *  a select left holding a value no option carries goes *blank* -- which on
   *  the desktop stranded the page on "no saved sessions" with an empty filter
   *  while other categories still had some. Falling back to "all" and
   *  reloading is that guard. */
  function renderFilter(categories) {
    const chosen = el.filter.value
    el.filter.innerHTML = '<option value="all">All categories</option>' +
      (categories ?? []).map((c) =>
        `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('')

    if (chosen && ![...el.filter.options].some((o) => o.value === chosen)) {
      el.filter.value = 'all'
      page = 1
      load()
      return
    }
    el.filter.value = chosen || 'all'
  }

  function renderList(sessions) {
    el.list.innerHTML = ''
    if (!sessions.length) {
      el.list.innerHTML = page > 1
        ? '<p class="text-text-muted">Nothing on this page.</p>'
        : '<p class="mt-4 text-center text-text-muted">No saved sessions yet. ' +
          'Finish an Adaptive Learning session with “Save &amp; Quit” to record one.</p>'
      return
    }

    for (const session of sessions) {
      el.list.append(row(session))
    }
  }

  function row(session) {
    const answered = session.questionsAnswered ?? 0
    const correct = session.correctAnswers ?? 0
    const accuracy = answered ? Math.round((correct / answered) * 100) : 0
    const start = Number(session.startDifficulty ?? 0)
    const end = Number(session.endDifficulty ?? 0)
    const delta = end - start

    // A tenth of a skill point either way is noise, not a direction. Saying
    // "you improved" off 5.02 to 5.06 is the same mistake the panels' minimum
    // sample gates exist to stop.
    const arrow = delta > 0.05 ? '▲' : delta < -0.05 ? '▼' : '→'
    const colour = delta > 0.05 ? 'text-green-400'
      : delta < -0.05 ? 'text-red-400' : 'text-text-muted'

    const clickable = Boolean(session.sessionId)
    const node = document.createElement('div')
    node.className = 'flex flex-wrap items-center justify-between gap-4 ' +
      'rounded-lg bg-secondary-dark p-3' +
      (clickable ? ' cursor-pointer hover:bg-tertiary-dark' : '')
    if (clickable) {
      // The row itself opens the session -- no separate "View stats" button
      // to hit first. Keyboard-reachable the same way a real button is,
      // since this is now the row's one interactive purpose beyond Delete.
      node.tabIndex = 0
      node.setAttribute('role', 'button')
      node.addEventListener('click', () => openDetail(session))
      node.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          openDetail(session)
        }
      })
    }
    node.innerHTML = `
      <div class="min-w-0">
        <div class="truncate font-semibold">${escapeHtml(session.category)}</div>
        <div class="text-xs text-text-muted">${escapeHtml(when(session.endedAt))}</div>
      </div>
      <div class="flex flex-shrink-0 items-center gap-6 text-sm">
        <div class="text-center">
          <div class="font-bold">${correct}/${answered}</div>
          <div class="text-xs text-text-muted">${accuracy}% correct</div>
        </div>
        <div class="text-center">
          <div class="font-bold ${colour}">${start.toFixed(1)} ${arrow} ${end.toFixed(1)}</div>
          <div class="text-xs text-text-muted">difficulty</div>
        </div>
      </div>`

    const actions = document.createElement('div')
    actions.className = 'flex flex-shrink-0 items-center gap-2'

    if (!clickable) {
      // Recorded before session ids were kept. There is nothing to filter the
      // answers by, and an empty chart would read as "you got nothing right" --
      // and nothing to click through to, so the row stays plain, unlike the
      // ones beside it.
      const note = document.createElement('span')
      note.className = 'text-xs text-text-muted'
      note.textContent = 'No per-answer detail recorded'
      actions.append(note)
    }

    const remove = document.createElement('button')
    remove.textContent = 'Delete'
    remove.className = 'rounded-full bg-red-700 px-3 py-1 text-xs font-bold text-white hover:bg-red-800'
    remove.addEventListener('click', async (event) => {
      // The row itself is a click target now (opens the session); Delete
      // sits inside it, so its click must not also bubble up and open the
      // very session the click is about to remove.
      event.stopPropagation()
      const label = `${session.category} session from ${when(session.endedAt)}`
      // Says what survives, because the wording is the whole question here:
      // this removes the record, not the answers behind it.
      if (!confirm(`Delete the ${label}?\n\nThe record goes; the answers stay ` +
                   'in your lifetime stats. This cannot be undone.')) return
      remove.disabled = true
      try {
        await api.deleteAdaptiveSession(session.id)
        // Every cached page's counts and row membership are stale the instant
        // this session leaves the table -- same reasoning as browse.js's own
        // remove button clearing its page cache.
        pageCache.clear()
        await load()
      } catch (error) {
        remove.disabled = false
        alert(error.message)
      }
    })
    actions.append(remove)
    node.append(actions)
    return node
  }

  /** Paging exists because the list is unbounded in sittings played. The
   *  server reports whether another page exists rather than counting the
   *  whole table for a number nothing shows. */
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

  return function openRecords() {
    // A session saved elsewhere (Adaptive Learning's "Save & Quit") since the
    // last visit would be missing from a list served out of this cache --
    // same reasoning as browse.js's `showBrowse` clearing its own page cache
    // on every fresh open. Paging back and forth *within* this visit still
    // comes from the cache; only a fresh open pays for a real fetch again.
    pageCache.clear()
    page = 1
    load()
  }
}

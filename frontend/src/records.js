/**
 * The records book: every Adaptive Learning sitting saved with "Save & Quit".
 *
 * `category_user_state` only ever holds the *latest* skill per subject, so
 * these summary rows are the only history there is -- which is why they are
 * written at the end of a sitting rather than derived on the way out here.
 *
 * The one design decision worth stating: **a record does not draw its own
 * stats.** The desktop's records page renders a second copy of the profile
 * charts scoped to one session; here, clicking a record opens the profile
 * itself with a session filter, because the profile already draws those
 * panels and the API already answers them per session. A second set of
 * drawing code for the same five charts is the thing that drifts.
 *
 * Not every record can do that. A sitting saved before anything tracked a
 * session id has no answers to point at, so its row says so instead of
 * offering a button that would open an empty page.
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

/** Timestamps arrive as ISO strings in UTC; the player reads them in their
 *  own zone, which is the only zone they can check against their memory. */
function when(iso) {
  if (!iso) return ''
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString()
}

export function initRecords({ onBack, onOpenSession }) {
  const el = {
    screen: $('recordsScreen'), backBtn: $('backFromRecordsBtn'),
    summary: $('recordsSummary'), filter: $('recordsCategoryFilter'),
    list: $('recordsList'), paging: $('recordsPaging'),
  }

  let page = 1

  el.backBtn.addEventListener('click', onBack)
  el.filter.addEventListener('change', () => { page = 1; load() })

  async function load() {
    el.list.innerHTML = '<p class="text-text-muted">Loading records…</p>'
    el.paging.innerHTML = ''

    let payload
    try {
      payload = await api.adaptiveSessions(el.filter.value || 'all', page)
    } catch (error) {
      el.list.innerHTML = `<p class="text-red-400">${escapeHtml(error.message)}</p>`
      el.summary.innerHTML = ''
      return
    }

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

    const node = document.createElement('div')
    node.className = 'flex flex-wrap items-center justify-between gap-4 ' +
      'rounded-lg bg-secondary-dark p-3'
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

    if (session.sessionId) {
      const stats = document.createElement('button')
      stats.textContent = 'View stats'
      stats.className = 'rounded-full bg-tertiary-dark px-3 py-1 text-xs font-bold hover:bg-accent-dark'
      stats.addEventListener('click', () => onOpenSession(session))
      actions.append(stats)
    } else {
      // Recorded before session ids were kept. There is nothing to filter the
      // answers by, and an empty chart would read as "you got nothing right".
      const note = document.createElement('span')
      note.className = 'text-xs text-text-muted'
      note.textContent = 'No per-answer detail recorded'
      actions.append(note)
    }

    const remove = document.createElement('button')
    remove.textContent = 'Delete'
    remove.className = 'rounded-full bg-red-700 px-3 py-1 text-xs font-bold text-white hover:bg-red-800'
    remove.addEventListener('click', async () => {
      const label = `${session.category} session from ${when(session.endedAt)}`
      // Says what survives, because the wording is the whole question here:
      // this removes the record, not the answers behind it.
      if (!confirm(`Delete the ${label}?\n\nThe record goes; the answers stay ` +
                   'in your lifetime stats. This cannot be undone.')) return
      remove.disabled = true
      try {
        await api.deleteAdaptiveSession(session.id)
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
    page = 1
    load()
  }
}

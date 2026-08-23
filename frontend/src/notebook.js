/**
 * The notebook: shelves, study guides, per-question notes, clues, flashcards.
 *
 * Two screens and two dialogs, following the desktop's shape -- a hub of
 * category tiles, then one shelf at a time with guides and clues on the left
 * and flashcards on the right.
 *
 * The one structural decision worth stating: **a shelf is loaded in one go.**
 * Opening a category fetches its notes, clues and cards as three parallel
 * requests and renders from what comes back, rather than fetching a list and
 * then a detail per row. The backend was written the same way (see the N+1
 * notes in routes/notebook.py), and it would be a shame to undo that in the
 * client by asking per item.
 */

import { api } from './api.js'
import { renderMarkdown } from './markdown.js'

const escapeHtml = (text) => String(text ?? '').replace(
  /[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))

const $ = (id) => document.getElementById(id)

export function initNotebook({ onBack, onNeedAiKey }) {
  const el = {
    hubScreen: $('notebookHubScreen'), hubList: $('notebookHubList'),
    hubEmpty: $('notebookHubEmpty'),
    detailScreen: $('notebookDetailScreen'), detailTitle: $('notebookDetailTitle'),
    search: $('notebookSearch'), searchCount: $('notebookSearchCount'),

    mergeBar: $('mergeBar'), mergeCount: $('mergeCount'),
    mergeCancelBtn: $('mergeCancelBtn'), mergeNotesBtn: $('mergeNotesBtn'),
    mergeDeleteOriginals: $('mergeDeleteOriginals'),
    exportSelectedNotesBtn: $('exportSelectedNotesBtn'),

    guidesSection: $('guidesSection'), guidesList: $('guidesList'),
    generateGuideBtn: $('generateGuideBtn'), generateGuideStatus: $('generateGuideStatus'),
    exportNotesBtn: $('exportNotesBtn'),
    notesSection: $('notesSection'), notesList: $('notesList'),
    notesEmpty: $('notesEmpty'),

    clueCount: $('clueCount'), cluesList: $('cluesList'),
    deleteAllCluesBtn: $('deleteAllCluesBtn'),

    cardsList: $('cardsList'), cardCount: $('cardCount'),
    addFlashcardBtn: $('addFlashcardBtn'), deleteAllFlashcardsBtn: $('deleteAllFlashcardsBtn'),
    exportFlashcardsBtn: $('exportFlashcardsBtn'),

    viewer: $('noteViewerModal'), viewerTitle: $('modalNoteTitle'),
    viewerSubtitle: $('modalNoteSubtitle'), viewerContent: $('modalNoteContent'),
    viewerEditor: $('modalNoteEditor'), viewerStatus: $('noteEditStatus'),
    closeViewerBtn: $('closeNoteViewerBtn'), editNoteBtn: $('editNoteBtn'),
    saveNoteChangesBtn: $('saveNoteChangesBtn'), cancelNoteEditBtn: $('cancelNoteEditBtn'),
    deleteNoteBtn: $('deleteNoteBtn'),

    nameModal: $('guideNameModal'), nameHeading: $('guideNameHeading'),
    nameSubtitle: $('guideNameSubtitle'), nameInput: $('guideNameInput'),
    nameError: $('guideNameError'), nameTargetWrap: $('guideTargetWrap'),
    nameTargetSelect: $('guideTargetSelect'),
    nameCancelBtn: $('guideNameCancelBtn'), nameConfirmBtn: $('guideNameConfirmBtn'),
  }

  // The shelf currently open, and everything on it. Held so the search box can
  // filter without refetching -- it hides rows that are already rendered.
  let shelf = null
  let notes = []
  let clues = []
  let cards = []
  let selected = new Set()
  let openNote = null

  // ------------------------------------------------------------------ hub --

  async function openHub() {
    show('hubScreen')
    el.hubList.innerHTML = '<p class="text-text-muted">Loading…</p>'
    try {
      const { categories } = await api.notebookCategories()
      el.hubEmpty.classList.toggle('hidden', categories.length > 0)
      el.hubList.innerHTML = categories.map((c) => {
        const bits = [
          c.notes ? `${c.notes} note${c.notes === 1 ? '' : 's'}` : null,
          c.flashcards ? `${c.flashcards} card${c.flashcards === 1 ? '' : 's'}` : null,
          c.clues ? `${c.clues} clue${c.clues === 1 ? '' : 's'}` : null,
        ].filter(Boolean).join(' · ')
        return `
          <button data-category="${escapeHtml(c.category)}"
                  class="flex w-full items-center justify-between rounded-lg bg-secondary-dark p-4 text-left hover:bg-tertiary-dark">
            <span class="font-bold">${escapeHtml(c.category)}</span>
            <span class="text-sm text-text-muted">${escapeHtml(bits)}</span>
          </button>`
      }).join('')
    } catch (error) {
      el.hubList.innerHTML = `<p class="text-red-400">${escapeHtml(error.message)}</p>`
    }
  }

  el.hubList.addEventListener('click', (event) => {
    const button = event.target.closest('[data-category]')
    if (button) openShelf(button.dataset.category)
  })

  // ---------------------------------------------------------------- shelf --

  async function openShelf(category) {
    shelf = category
    selected.clear()
    show('detailScreen')
    el.detailTitle.textContent = category
    el.search.value = ''
    el.notesList.innerHTML = '<p class="text-text-muted">Loading…</p>'
    el.cluesList.innerHTML = ''
    el.cardsList.innerHTML = ''

    // Three requests in parallel rather than in sequence: they do not depend on
    // each other, and awaiting them one at a time is three round trips of
    // latency for no reason.
    const [noteResult, clueResult, cardResult] = await Promise.allSettled([
      api.notes(category), api.clues(category), api.flashcards(category),
    ])
    notes = noteResult.status === 'fulfilled' ? noteResult.value.notes : []
    clues = clueResult.status === 'fulfilled' ? clueResult.value.clues : []
    cards = cardResult.status === 'fulfilled' ? cardResult.value.flashcards : []

    paintNotes()
    paintClues()
    paintCards()
  }

  function noteTitle(note) {
    return note.title || note.answer_text || 'Untitled'
  }

  function paintNotes() {
    const guides = notes.filter((n) => n.is_merged)
    const singles = notes.filter((n) => !n.is_merged)

    el.guidesSection.classList.toggle('hidden', guides.length === 0)
    el.notesSection.classList.toggle('hidden', singles.length === 0)
    el.notesEmpty.classList.toggle('hidden', notes.length > 0)
    el.exportNotesBtn.classList.toggle('hidden', notes.length === 0)
    if (!notes.length) {
      el.notesEmpty.innerHTML =
        `<p class="text-sm text-text-muted">Nothing saved here yet. Highlight a clue while
         reading and press <span class="font-bold text-text-light">Save Highlight</span>,
         and it turns up under Saved Clues.</p>`
    }

    el.guidesList.innerHTML = guides.map((n) => `
      <div data-note-row="${n.id}" class="rounded-lg bg-secondary-dark p-3">
        <button data-open-note="${n.id}" class="w-full text-left">
          <span class="font-bold text-sky-400">${escapeHtml(noteTitle(n))}</span>
          <span class="ml-2 text-xs text-text-muted">${sectionCount(n)}</span>
        </button>
      </div>`).join('')

    // Per-question notes carry a checkbox: two or more of them can be rolled
    // up into a guide, which is the point of keeping them separately at all.
    el.notesList.innerHTML = singles.map((n) => `
      <div data-note-row="${n.id}" class="flex items-start gap-2 rounded-lg bg-secondary-dark p-3">
        <input type="checkbox" data-pick="${n.id}" class="mt-1 h-4 w-4 shrink-0 accent-sky-500"
               ${selected.has(n.id) ? 'checked' : ''}>
        <button data-open-note="${n.id}" class="flex-1 text-left">
          <span class="font-bold">${escapeHtml(noteTitle(n))}</span>
          ${n.difficulty != null
            ? `<span class="ml-2 text-xs text-text-muted">difficulty ${n.difficulty}</span>` : ''}
        </button>
      </div>`).join('')

    paintMergeBar()
    applySearch()
  }

  const sectionCount = (note) => {
    const n = (note.notes_content || '').split(/\n\s*---\s*\n/).length
    return `${n} section${n === 1 ? '' : 's'}`
  }

  function paintClues() {
    el.clueCount.textContent = clues.length ? `(${clues.length})` : ''
    el.deleteAllCluesBtn.classList.toggle('hidden', clues.length === 0)

    if (!clues.length) {
      el.cluesList.innerHTML =
        '<p class="text-sm text-text-muted">No clues saved on this shelf yet.</p>'
      return
    }

    // Grouped by the answer they point at, which is what makes a pile of clues
    // into something you can revise from.
    const groups = new Map()
    for (const clue of clues) {
      const key = clue.answer_text || 'Unattributed'
      if (!groups.has(key)) groups.set(key, [])
      groups.get(key).push(clue)
    }

    el.cluesList.innerHTML = [...groups].map(([answer, items]) => `
      <div data-clue-group class="rounded-lg bg-secondary-dark p-3">
        <p class="mb-2 text-sm font-bold text-[#f6b17a]">${escapeHtml(answer)}</p>
        ${items.map((c) => `
          <div data-clue-row class="mb-1 flex items-start gap-2 text-sm">
            <span class="flex-1 text-text-muted">${escapeHtml(c.clue_text)}</span>
            <button data-delete-clue="${c.id}"
                    class="shrink-0 text-xs text-red-400 hover:text-red-300"
                    title="Remove this clue">✕</button>
          </div>`).join('')}
      </div>`).join('')
  }

  function paintCards() {
    el.cardCount.textContent = cards.length ? `(${cards.length})` : ''
    el.deleteAllFlashcardsBtn.classList.toggle('hidden', cards.length === 0)
    el.exportFlashcardsBtn.classList.toggle('hidden', cards.length === 0)

    if (!cards.length) {
      el.cardsList.innerHTML =
        '<p class="text-sm text-text-muted">No flashcards on this shelf yet.</p>'
      return
    }

    el.cardsList.innerHTML = cards.map((c) => `
      <div data-card-row class="rounded-lg bg-secondary-dark p-4">
        <div class="flex items-start justify-between gap-3">
          <p class="font-bold">${escapeHtml(c.term)}</p>
          <button data-delete-card="${c.id}"
                  class="shrink-0 text-xs text-red-400 hover:text-red-300"
                  title="Delete this card">✕</button>
        </div>
        <p class="mt-1 text-sm text-text-muted">${escapeHtml(c.definition)}</p>
        ${c.source_answer
          ? `<p class="mt-2 text-xs text-text-muted">from
             <span class="text-[#f6b17a]">${escapeHtml(c.source_answer)}</span></p>` : ''}
      </div>`).join('')
  }

  // --------------------------------------------------------------- search --

  /** Hides rows already in the DOM rather than refetching. One box filters all
   *  three columns, the same as the desktop. */
  function applySearch() {
    const term = el.search.value.trim().toLowerCase()
    let shown = 0, total = 0

    for (const row of el.detailScreen.querySelectorAll(
      '[data-note-row], [data-clue-group], [data-card-row]')) {
      total++
      const match = !term || row.textContent.toLowerCase().includes(term)
      row.classList.toggle('hidden', !match)
      if (match) shown++
    }

    el.searchCount.classList.toggle('hidden', !term)
    el.searchCount.textContent = `${shown} of ${total} match “${el.search.value.trim()}”`
  }

  el.search.addEventListener('input', applySearch)

  // ------------------------------------------------------- guide generation --

  el.generateGuideBtn.addEventListener('click', async () => {
    el.generateGuideBtn.disabled = true
    el.generateGuideStatus.classList.remove('hidden')
    el.generateGuideStatus.textContent = 'Building a guide from your saved clues…'
    try {
      await api.generateGuide(shelf)
      el.generateGuideStatus.classList.add('hidden')
      await openShelf(shelf)
    } catch (error) {
      // ApiError.empty on a 404 with no clues -- an instruction, not a
      // failure. Google credential setup is a different kind of "not ready
      // yet" and gets routed to Settings instead of read on this page.
      if (error.payload?.code === 'no_key' && onNeedAiKey) {
        el.generateGuideStatus.textContent = error.message
        onNeedAiKey()
      } else {
        el.generateGuideStatus.textContent = error.message
      }
    } finally {
      el.generateGuideBtn.disabled = false
    }
  })

  // -------------------------------------------------------------- export --

  /** Runs a download and puts the button back the way it found it -- showing
   *  the error in the button's own label rather than a shared status line,
   *  since this shelf has three separate export buttons and no one place
   *  all of their failures naturally belong. */
  async function runExport(button, label, action) {
    button.disabled = true
    const original = button.textContent
    try {
      await action()
    } catch (error) {
      button.textContent = error.message
      setTimeout(() => { button.textContent = original }, 3000)
      return
    } finally {
      button.disabled = false
    }
    button.textContent = original
  }

  el.exportNotesBtn.addEventListener('click', () =>
    runExport(el.exportNotesBtn, 'Export', () => api.exportNotes(shelf)))

  el.exportFlashcardsBtn.addEventListener('click', () =>
    runExport(el.exportFlashcardsBtn, 'Export', () => api.exportFlashcards(shelf)))

  el.exportSelectedNotesBtn.addEventListener('click', () =>
    runExport(el.exportSelectedNotesBtn, 'Export selected',
      () => api.exportSelectedNotes([...selected])))

  // ---------------------------------------------------------------- merge --

  function paintMergeBar() {
    el.mergeCount.textContent = selected.size
    // The bar itself is useful from one pick (export doesn't need a pair);
    // merging still does, so that button alone stays gated at two.
    el.mergeBar.classList.toggle('hidden', selected.size < 1)
    el.mergeNotesBtn.classList.toggle('hidden', selected.size < 2)
  }

  el.notesList.addEventListener('change', (event) => {
    const box = event.target.closest('[data-pick]')
    if (!box) return
    const id = Number(box.dataset.pick)
    if (box.checked) selected.add(id)
    else selected.delete(id)
    paintMergeBar()
  })

  el.mergeCancelBtn.addEventListener('click', () => {
    selected.clear()
    for (const box of el.notesList.querySelectorAll('[data-pick]')) box.checked = false
    paintMergeBar()
  })

  el.mergeNotesBtn.addEventListener('click', () => {
    const guides = notes.filter((n) => n.is_merged)
    el.nameHeading.textContent = 'What do you want to name it?'
    el.nameSubtitle.textContent =
      `${selected.size} notes will be rolled into one guide, sorted A–Z by answerline.`
    el.nameInput.value = ''
    el.nameError.classList.add('hidden')

    // A guide can be started or grown. Hidden entirely when there are none yet,
    // because "add to an existing guide" with nothing to add to is a dead
    // control that only raises a question.
    el.nameTargetWrap.classList.toggle('hidden', guides.length === 0)
    el.nameTargetSelect.innerHTML =
      '<option value="__new__">➕ A new guide…</option>' +
      guides.map((g) => `<option value="${g.id}">${escapeHtml(noteTitle(g))}</option>`).join('')

    openModal(el.nameModal)
    el.nameInput.focus()
  })

  el.nameTargetSelect.addEventListener('change', () => {
    const appending = el.nameTargetSelect.value !== '__new__'
    el.nameInput.classList.toggle('hidden', appending)
    el.nameConfirmBtn.textContent = appending ? 'Add to guide' : 'Create Guide'
  })

  el.nameCancelBtn.addEventListener('click', () => closeModal(el.nameModal))

  el.nameConfirmBtn.addEventListener('click', async () => {
    const target = el.nameTargetSelect.value
    const noteIds = [...selected]
    const deleteOriginals = el.mergeDeleteOriginals.checked
    el.nameError.classList.add('hidden')
    el.nameConfirmBtn.disabled = true

    try {
      if (target === '__new__') {
        const title = el.nameInput.value.trim()
        if (!title) throw new Error('Give the guide a name.')
        await api.mergeGuide({ title, noteIds, deleteOriginals, category: shelf })
      } else {
        await api.appendGuide(Number(target), { noteIds, deleteOriginals })
      }
      closeModal(el.nameModal)
      selected.clear()
      await openShelf(shelf)
    } catch (error) {
      el.nameError.textContent = error.message
      el.nameError.classList.remove('hidden')
    } finally {
      el.nameConfirmBtn.disabled = false
    }
  })

  // ---------------------------------------------------------- note viewer --

  el.detailScreen.addEventListener('click', async (event) => {
    const openBtn = event.target.closest('[data-open-note]')
    if (openBtn) return showNote(Number(openBtn.dataset.openNote))

    const clueBtn = event.target.closest('[data-delete-clue]')
    if (clueBtn) {
      const id = Number(clueBtn.dataset.deleteClue)
      clueBtn.disabled = true
      try {
        await api.deleteClue(id)
        clues = clues.filter((c) => c.id !== id)
        paintClues()
        applySearch()
      } catch (error) { clueBtn.textContent = '!' }
      return
    }

    const cardBtn = event.target.closest('[data-delete-card]')
    if (cardBtn) {
      const id = Number(cardBtn.dataset.deleteCard)
      cardBtn.disabled = true
      try {
        await api.deleteFlashcard(id)
        cards = cards.filter((c) => c.id !== id)
        paintCards()
        applySearch()
      } catch (error) { cardBtn.textContent = '!' }
    }
  })

  async function showNote(id) {
    openNote = notes.find((n) => n.id === id)
    if (!openNote) return

    el.viewerTitle.textContent = noteTitle(openNote)
    el.viewerSubtitle.textContent = [
      openNote.is_merged ? 'Study guide' : 'Question note',
      openNote.subcategory, openNote.category,
    ].filter(Boolean).join(' · ')
    el.viewerSubtitle.classList.remove('hidden')
    el.viewerContent.innerHTML = renderMarkdown(openNote.notes_content)
    setEditing(false)
    openModal(el.viewer)
  }

  function setEditing(editing) {
    el.viewerContent.classList.toggle('hidden', editing)
    el.viewerEditor.classList.toggle('hidden', !editing)
    el.editNoteBtn.classList.toggle('hidden', editing)
    el.saveNoteChangesBtn.classList.toggle('hidden', !editing)
    el.cancelNoteEditBtn.classList.toggle('hidden', !editing)
    el.viewerStatus.textContent = ''
    if (editing) {
      el.viewerEditor.value = openNote.notes_content
      el.viewerEditor.focus()
    }
  }

  el.editNoteBtn.addEventListener('click', () => setEditing(true))
  el.cancelNoteEditBtn.addEventListener('click', () => setEditing(false))
  el.closeViewerBtn.addEventListener('click', () => closeModal(el.viewer))

  el.saveNoteChangesBtn.addEventListener('click', async () => {
    const content = el.viewerEditor.value
    el.saveNoteChangesBtn.disabled = true
    el.viewerStatus.textContent = 'Saving…'
    try {
      await api.updateNote(openNote.id, { content })
      openNote.notes_content = content
      el.viewerContent.innerHTML = renderMarkdown(content)
      setEditing(false)
      el.viewerStatus.textContent = 'Saved.'
      paintNotes()
    } catch (error) {
      el.viewerStatus.textContent = error.message
    } finally {
      el.saveNoteChangesBtn.disabled = false
    }
  })

  el.deleteNoteBtn.addEventListener('click', async () => {
    if (!openNote) return
    // Two clicks, not a confirm dialog: the button says what it will do and
    // then asks for it again. A guide can be a lot of work to lose.
    if (el.deleteNoteBtn.dataset.armed !== 'yes') {
      el.deleteNoteBtn.dataset.armed = 'yes'
      el.deleteNoteBtn.textContent = 'Really delete?'
      return
    }
    try {
      await api.deleteNote(openNote.id)
      notes = notes.filter((n) => n.id !== openNote.id)
      selected.delete(openNote.id)
      closeModal(el.viewer)
      paintNotes()
    } catch (error) {
      el.viewerStatus.textContent = error.message
    }
  })

  // --------------------------------------------------------- bulk deletes --

  for (const [button, run, label] of [
    [el.deleteAllCluesBtn, () => api.deleteAllClues(shelf), 'clues'],
    [el.deleteAllFlashcardsBtn, () => api.deleteAllFlashcards(shelf), 'flashcards'],
  ]) {
    button.addEventListener('click', async () => {
      if (button.dataset.armed !== 'yes') {
        button.dataset.armed = 'yes'
        button.textContent = `Really delete all ${label}?`
        return
      }
      button.disabled = true
      try {
        await run()
        await openShelf(shelf)
      } finally {
        button.disabled = false
        delete button.dataset.armed
      }
    })
  }

  // ------------------------------------------------------- manual cards ----

  el.addFlashcardBtn.addEventListener('click', async () => {
    // Prompt rather than a dialog of its own: the desktop generates cards with
    // the AI and only offers "+" as an escape hatch, and until the server-side
    // key exists that escape hatch is the whole feature. A real editor here
    // would be building UI for a path that is about to change shape.
    const term = window.prompt('Front of the card (the term):')
    if (!term?.trim()) return
    const definition = window.prompt('Back of the card (the definition):')
    if (!definition?.trim()) return

    try {
      await api.saveFlashcards({
        category: shelf,
        flashcards: [{ term: term.trim(), definition: definition.trim() }],
      })
      await openShelf(shelf)
    } catch (error) {
      window.alert(error.message)
    }
  })

  // -------------------------------------------------------------- helpers --

  function show(which) {
    el.hubScreen.classList.toggle('hidden', which !== 'hubScreen')
    el.detailScreen.classList.toggle('hidden', which !== 'detailScreen')
  }

  function openModal(modal) {
    modal.classList.remove('hidden')
    modal.classList.add('flex')
  }

  function closeModal(modal) {
    modal.classList.add('hidden')
    modal.classList.remove('flex')
    // Re-arm the destructive buttons, so a modal reopened later does not still
    // be one click from deleting something.
    delete el.deleteNoteBtn.dataset.armed
    el.deleteNoteBtn.textContent = 'Delete'
  }

  for (const modal of [el.viewer, el.nameModal]) {
    modal.addEventListener('click', (event) => {
      if (event.target === modal) closeModal(modal)
    })
  }

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return
    for (const modal of [el.viewer, el.nameModal]) {
      if (!modal.classList.contains('hidden')) closeModal(modal)
    }
  })

  for (const button of document.querySelectorAll('[data-notebook-back]')) {
    button.addEventListener('click', () => {
      if (button.dataset.notebookBack === 'hub') openHub()
      else onBack()
    })
  }

  return { openHub }
}

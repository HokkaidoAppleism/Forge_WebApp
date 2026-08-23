import { supabase } from './supabase.js'

const BASE = import.meta.env.VITE_API_URL

/**
 * Every call to our own API goes through here, so the Authorization header is
 * attached in exactly one place. Scattering `fetch` calls that each remember
 * to add the token is how one of them ends up not remembering.
 *
 * getSession() returns the cached token and refreshes it when it is close to
 * expiring, so this is not a network round trip per request.
 */
async function call(path, { method = 'GET', body, params } = {}) {
  const { data: { session } } = await supabase.auth.getSession()
  if (!session) throw new ApiError('You are signed out.', 401)

  const url = new URL(BASE + path)
  for (const [key, value] of Object.entries(params ?? {})) {
    if (Array.isArray(value)) value.forEach((v) => url.searchParams.append(key, v))
    else if (value !== undefined && value !== null && value !== '') {
      url.searchParams.set(key, value)
    }
  }

  const response = await fetch(url, {
    method,
    headers: {
      Authorization: `Bearer ${session.access_token}`,
      ...(body ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  })

  // fetch only rejects on a network failure, so a 404 or a 500 arrives here
  // looking exactly like success. Checking response.ok is the whole difference
  // between an error being handled and an error being rendered as data.
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new ApiError(payload.error ?? 'That request failed.', response.status, payload)
  }
  return payload
}

export class ApiError extends Error {
  constructor(message, status, payload = {}) {
    super(message)
    this.status = status
    this.payload = payload
    // Distinguishes "the query ran and matched nothing" from a real failure.
    // Retrying the first with the same filters cannot ever work, so the two
    // must not be reported the same way.
    this.empty = Boolean(payload.empty)
  }
}

// The player's own timezone, so the server can cut days on their calendar
// rather than on the region it happens to be deployed in.
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone

export const api = {
  filters: () => call('/api/questions/filters'),

  randomQuestion: (filters) => call('/api/questions/random', { params: filters }),

  submitAnswer: (answer) =>
    call('/api/answers', { method: 'POST', body: { ...answer, timezone } }),

  nextReview: (categories) =>
    call('/api/review/next', { params: { category: categories } }),

  reviewQueue: (page, status, category) =>
    call('/api/review/queue', { params: { page, status, category } }),

  // The four numbers the Review Settings panel opens with, and what is left
  // to drill in each category. One request, because the server groups them.
  reviewCounts: () => call('/api/review/counts'),

  reviewSettings: () => call('/api/settings/review'),
  saveReviewSettings: (settings) =>
    call('/api/settings/review', { method: 'POST', body: settings }),

  addToReview: (questionId) =>
    call('/api/review/add', { method: 'POST', body: { questionId } }),

  removeFromReview: (questionId) =>
    call('/api/review/remove', { method: 'POST', body: { questionId } }),

  // `session` narrows these to one saved Adaptive Learning sitting. Only the
  // three panels built on `user_stats` accept it: a day and a review queue
  // outlive the sitting that produced them, so Retention and Progress take the
  // category and nothing else -- see the note at the top of routes/stats.py.
  stats: (category, session) =>
    call('/api/stats/summary', { params: { category, session, timezone } }),

  // Adaptive Learning. There is no "start a session" call: the server holds no
  // session object to create, so the first question builds the state row and
  // resuming is just loading it -- see web/api/adaptive.py.
  adaptiveCategories: () => call('/api/adaptive/categories'),

  adaptiveQuestion: (categories, weights) =>
    call('/api/adaptive/question', { params: { category: categories, weight: weights } }),

  adaptiveEnd: (restoreKey, sessionId, startedAt) =>
    call('/api/adaptive/end', {
      method: 'POST', body: { restoreKey, sessionId, startedAt },
    }),

  // The records book. `category` filters the rows and the totals; the picker
  // it is chosen from always lists every category ever played, so a filter
  // can never hide the option that undoes it.
  adaptiveSessions: (category, page) =>
    call('/api/adaptive/sessions', { params: { category, page } }),

  deleteAdaptiveSession: (id) =>
    call(`/api/adaptive/sessions/${id}/delete`, { method: 'POST' }),

  // ---------------------------------------------------------- the notebook --
  notebookCategories: () => call('/api/notebook/categories'),

  notes: (category) => call('/api/notebook/notes', { params: { category } }),
  note: (id) => call(`/api/notebook/notes/${id}`),
  saveNote: (body) => call('/api/notebook/notes', { method: 'POST', body }),
  updateNote: (id, body) => call(`/api/notebook/notes/${id}`, { method: 'POST', body }),
  deleteNote: (id) => call(`/api/notebook/notes/${id}/delete`, { method: 'POST' }),

  mergeGuide: (body) => call('/api/notebook/guides', { method: 'POST', body }),
  appendGuide: (id, body) =>
    call(`/api/notebook/guides/${id}/append`, { method: 'POST', body }),

  flashcards: (category) => call('/api/notebook/flashcards', { params: { category } }),
  saveFlashcards: (body) => call('/api/notebook/flashcards', { method: 'POST', body }),
  deleteFlashcard: (id) =>
    call(`/api/notebook/flashcards/${id}/delete`, { method: 'POST' }),
  deleteAllFlashcards: (category) =>
    call('/api/notebook/flashcards/delete-all', { method: 'POST', body: { category } }),

  clues: (category) => call('/api/notebook/clues', { params: { category } }),
  saveClue: (body) => call('/api/notebook/clues', { method: 'POST', body }),
  deleteClue: (id) => call(`/api/notebook/clues/${id}/delete`, { method: 'POST' }),
  deleteAllClues: (category) =>
    call('/api/notebook/clues/delete-all', { method: 'POST', body: { category } }),

  // The five analysis panels. Each returns numbers and a written finding, not
  // a picture -- see web/api/panels.py -- so everything that draws them lives
  // in the browser now, in src/profile.js.
  buzzpoints: (category, session) =>
    call('/api/stats/buzzpoints', { params: { category, session } }),
  ceiling: (category, session) =>
    call('/api/stats/ceiling', { params: { category, session } }),
  negAutopsy: (category, session) =>
    call('/api/stats/negs', { params: { category, session } }),
  retention: (category) => call('/api/stats/retention', { params: { category } }),
  progress: (category, month) =>
    call('/api/stats/progress', { params: { category, month } }),

  // Deletes user_stats and nothing else -- the review queue, the notebook and
  // the Adaptive Learning skill model all survive. See routes/stats.py.
  resetStats: () => call('/api/stats/reset', { method: 'POST' }),

  // ---------------------------------------------------------- AI, per-user --
  // Each account brings its own Gemini key; see web/api/ai.py. GET never
  // returns the key itself, only a hint -- see routes/settings.py.
  aiKeyStatus: () => call('/api/settings/ai-key'),
  saveAiKey: (apiKey) =>
    call('/api/settings/ai-key', { method: 'POST', body: { apiKey } }),
  deleteAiKey: () => call('/api/settings/ai-key/delete', { method: 'POST' }),

  explainQuestion: (questionId, userAnswer) =>
    call('/api/ai/explain', { method: 'POST', body: { questionId, userAnswer } }),
  explainSentence: (questionId, sentence) =>
    call('/api/ai/explain-sentence', { method: 'POST', body: { questionId, sentence } }),
  generateFlashcards: (questionId) =>
    call('/api/ai/flashcards', { method: 'POST', body: { questionId } }),
  generateGuide: (category) =>
    call('/api/ai/guide', { method: 'POST', body: { category } }),
}

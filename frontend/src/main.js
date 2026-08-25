import { supabase } from './supabase.js'
import { api, ApiError } from './api.js'
import { initProfile } from './profile.js'
import { initNotebook } from './notebook.js'
import { initRecords } from './records.js'
import { initReviewSettings } from './reviewSettings.js'
import { initReviewList } from './reviewList.js'
import { initBrowse } from './browse.js'
import { initAiSettings } from './aiSettings.js'
import { initAccountSettings } from './accountSettings.js'
import { renderMarkdown } from './markdown.js'
import * as voice from './voice.js'

// ---------------------------------------------------------------- elements --

const $ = (id) => document.getElementById(id)
const el = {
  authScreen: $('authScreen'), authForm: $('authForm'), authMessage: $('authMessage'),
  email: $('email'), password: $('password'),
  appScreen: $('appScreen'), whoami: $('whoami'), signOutBtn: $('signOutBtn'),
  readerScreen: $('readerScreen'), profileScreen: $('profileScreen'),
  aboutScreen: $('aboutScreen'), adaptiveSetupScreen: $('adaptiveSetupScreen'),
  notebookHubScreen: $('notebookHubScreen'),
  notebookDetailScreen: $('notebookDetailScreen'),
  recordsScreen: $('recordsScreen'), reviewListScreen: $('reviewListScreen'),
  browseScreen: $('browseScreen'), browseBtn: $('browseBtn'),
  profileBtn: $('profileBtn'), backToReaderBtn: $('backToReaderBtn'),
  aboutBtn: $('aboutBtn'), backFromAboutBtn: $('backFromAboutBtn'),
  notebookBtn: $('notebookBtn'), saveHighlightBtn: $('saveHighlightBtn'),
  recordsBtn: $('recordsBtn'), reviewSettingsBtn: $('reviewSettingsBtn'),

  settingsBtn: $('settingsBtn'), settingsModal: $('settingsModal'),
  closeSettingsBtn: $('closeSettingsBtn'),
  fontSizeRange: $('fontSizeRange'), fontSizeDisplay: $('fontSizeDisplay'),
  fontSizeResetBtn: $('fontSizeResetBtn'), shortcutsToggle: $('shortcutsToggle'),
  shortcutListSettings: $('shortcutListSettings'), shortcutListAbout: $('shortcutListAbout'),
  shortcutToast: $('shortcutToast'),

  adaptiveLearningBtn: $('adaptiveLearningBtn'),
  adaptiveCategorySelect: $('adaptiveCategorySelect'),
  adaptiveWeightsWrap: $('adaptiveWeightsWrap'), adaptiveWeights: $('adaptiveWeights'),
  adaptiveWeightTotal: $('adaptiveWeightTotal'),
  resetAdaptiveWeightsBtn: $('resetAdaptiveWeightsBtn'),
  startAdaptiveSessionBtn: $('startAdaptiveSessionBtn'),
  cancelAdaptiveBtn: $('cancelAdaptiveBtn'), adaptiveSetupMessage: $('adaptiveSetupMessage'),
  adaptiveSessionStats: $('adaptiveSessionStats'),
  adaptiveSubject: $('adaptiveSubject'), adaptiveSkill: $('adaptiveSkill'),
  adaptiveAnswered: $('adaptiveAnswered'), adaptiveCorrect: $('adaptiveCorrect'),
  adaptiveAccuracy: $('adaptiveAccuracy'), adaptiveResumed: $('adaptiveResumed'),
  saveAndQuitAdaptiveBtn: $('saveAndQuitAdaptiveBtn'),
  profileCategoryFilter: $('profileCategoryFilter'),
  profileTossupsHeard: $('profileTossupsHeard'), profilePoints: $('profilePoints'),
  profilePowers: $('profilePowers'), profileTens: $('profileTens'),
  profileNegs: $('profileNegs'), profileCelerity: $('profileCelerity'),
  statAboutTitle: $('statAboutTitle'), statAboutWhat: $('statAboutWhat'),
  statAboutFinding: $('statAboutFinding'),
  statPicker: $('statPicker'), statView: $('statView'),
  progressNav: $('progressNav'), progressView: $('progressView'),
  profileTitle: $('profileTitle'), profileCategoryLabel: $('profileCategoryLabel'),
  progressSection: $('progressSection'),
  profileSessionNotice: $('profileSessionNotice'),
  profileSessionText: $('profileSessionText'),
  profileExitSessionBtn: $('profileExitSessionBtn'),
  resetStatsBtn: $('resetStatsBtn'), resetStatsModal: $('resetStatsModal'),
  confirmResetStatsBtn: $('confirmResetStatsBtn'),
  cancelResetStatsBtn: $('cancelResetStatsBtn'),
  resetStatsMessage: $('resetStatsMessage'),

  categorySelect: $('categorySelect'), subcategorySelect: $('subcategorySelect'),
  subcategoryWrapper: $('subcategoryWrapper'), difficultySelect: $('difficultySelect'),

  powerHighlightToggle: $('powerHighlightToggle'), allowRebuzzToggle: $('allowRebuzzToggle'),
  readingSpeed: $('readingSpeed'), speedDisplay: $('speedDisplay'),
  voiceModeToggle: $('voiceModeToggle'), showTextRow: $('showTextRow'),
  showTextToggle: $('showTextToggle'), voiceUnsupported: $('voiceUnsupported'),

  getNewQuestionBtn: $('getNewQuestionBtn'), getManyQuestionsBtn: $('getManyQuestionsBtn'),
  reviewMissedBtn: $('reviewMissedBtn'), stopReviewBtn: $('stopReviewBtn'),
  pauseBtn: $('pauseBtn'),

  ptn: $('ptn'), tossupsHeard: $('tossupsHeard'), pointsScored: $('pointsScored'),
  celerity: $('celerity'),

  questionContainer: $('questionContainer'), questionMeta: $('questionMeta'),
  reviewAheadNotice: $('reviewAheadNotice'),
  answerInput: $('answerInput'), buzzTimer: $('buzzTimer'),
  submitAnswerBtn: $('submitAnswerBtn'), answerFeedback: $('answerFeedback'),
  addToMissedBtn: $('addToMissedBtn'),
  getExplanationBtn: $('getExplanationBtn'), explanationContainer: $('explanationContainer'),
  createFlashcardBtn: $('createFlashcardBtn'), draftFlashcardsContainer: $('draftFlashcardsContainer'),
}

// ------------------------------------------------------------------ state --

// One id per sitting, so a session's answers can be found again later. It is
// generated here and sent up, but nothing is trusted to it: the server files
// every row against the user id from the token, never against anything the
// page says.
const sessionId = crypto.randomUUID()

const DEAD_TIME_SECONDS = 5    // to buzz after the question finishes reading
const ANSWER_TIME_SECONDS = 10 // to answer after buzzing

let words = []          // the tossup, split for the word-by-word read
let wordIndex = 0       // how many words have been shown
let powerIdx = -1       // index of the word holding the (*), or -1
let ticker = null       // the setTimeout chain driving the read
let question = null     // the row currently on screen
let buzzed = false
let paused = false
let allowRebuzz = false // practice aid: keep reading after a wrong guess, unscored -- see finish()
let voiceMode = false        // read the tossup aloud instead of word-by-word
let showTossupText = false   // Voice Mode + also print the words as they're spoken
// True while tick() is estimating word position from the timer because this
// platform's speech engine never fires `onboundary` -- the audio is still
// playing, and voice.js's onEnd (not the estimate reaching the end) is what
// closes the tossup out in that case. See voice.js's `onFallback`.
let voiceEstimating = false
let submitting = false  // guards double-submit; see finish()
let reviewMode = false
let filters = []        // the category tree from /api/questions/filters

// Adaptive Learning. `adaptive` is null outside a session and otherwise holds
// the picks, their weights and the running session totals. The skill model
// itself lives in Postgres, not here -- the browser is told the current number
// so it can show it, and is never the thing that remembers it.
let adaptive = null
let adaptiveCatalogue = null

// Declared up here with the rest of the state, not beside the code that uses
// them, and that placement is load-bearing: `onAuthStateChange` below fires
// during module evaluation (Supabase emits INITIAL_SESSION as soon as it is
// registered), and its handler reaches both `updateWhoami` -> signedInEmail
// and `abandonTossup` -> stopCountdown -> countdownInterval. A `let` further
// down the file is still in its temporal dead zone at that moment, so the
// handler threw "Cannot access 'signedInEmail' before initialization" and
// took the rest of the sign-in path with it.
let signedInEmail = ''
let countdownInterval = null

// -------------------------------------------------------------------- auth --

el.authForm.addEventListener('submit', async (event) => {
  event.preventDefault()
  const mode = event.submitter?.dataset.mode ?? 'signin'
  el.authMessage.textContent = ''

  const credentials = { email: el.email.value.trim(), password: el.password.value }
  const { error } = mode === 'signup'
    ? await supabase.auth.signUp(credentials)
    : await supabase.auth.signInWithPassword(credentials)

  if (error) {
    // Supabase deliberately does not say whether it was the address or the
    // password that was wrong, and neither should we -- the difference tells
    // a stranger which addresses have accounts.
    el.authMessage.textContent = error.message
    return
  }
  if (mode === 'signup') {
    el.authMessage.textContent = 'Check your email to confirm the account, then sign in.'
  }
})

el.signOutBtn.addEventListener('click', () => supabase.auth.signOut())

supabase.auth.onAuthStateChange((_event, session) => {
  const signedIn = Boolean(session)
  // `hidden` is a class here, not the attribute: the two screens are flex
  // containers, and `display: flex` from a utility class beats the attribute's
  // `display: none`, so a screen hidden by attribute alone stays on screen.
  el.authScreen.classList.toggle('hidden', signedIn)
  el.authScreen.classList.toggle('flex', !signedIn)
  el.appScreen.classList.toggle('hidden', !signedIn)

  if (signedIn) {
    signedInEmail = session.user.email
    updateWhoami(signedInEmail)
    loadFilters()
    loadStats()
  } else {
    abandonTossup()
    openReader()
  }
})

// ---------------------------------------------------------------- screens --

// The desktop is one document with seven hidden pages and no router; this
// keeps that shape rather than adding one. A URL per screen would be better
// on the web and it is a change to make once there are screens worth linking
// to, not while there are two.
const showProfile = initProfile(el)

const SCREENS = [
  'readerScreen', 'profileScreen', 'aboutScreen', 'adaptiveSetupScreen',
  'notebookHubScreen', 'notebookDetailScreen', 'recordsScreen', 'reviewListScreen',
  'browseScreen',
]

/** Show exactly one screen. Leaving a tossup half-read to look at something
 *  else must not leave a timer running behind the page, or score a pass while
 *  nobody is looking at it -- so every departure from the reader abandons the
 *  question in flight. */
function showScreen(name) {
  const leavingReader = name !== 'readerScreen'
    && !el.readerScreen.classList.contains('hidden')
  if (leavingReader) {
    abandonTossup()
    el.questionContainer.textContent = 'Click “Start Reader” to start practicing'
  }
  for (const screen of SCREENS) el[screen].classList.toggle('hidden', screen !== name)
}

function openReader() { showScreen('readerScreen') }

el.profileBtn.addEventListener('click', () => {
  showScreen('profileScreen')
  showProfile(filters)
})

// ---------------------------------------------------- records and review --

const showRecords = initRecords({
  onBack: openReader,
  // A record does not draw its own charts: it opens the profile scoped to
  // that sitting, because the profile already draws them and the API already
  // answers them per session. See the note at the top of records.js.
  onOpenSession: (session) => {
    showScreen('profileScreen')
    showProfile(filters, session)
  },
})

el.recordsBtn.addEventListener('click', () => {
  showScreen('recordsScreen')
  showRecords()
})

const showReviewList = initReviewList({ onBack: openReader })
const showBrowse = initBrowse({ onBack: openReader })

el.browseBtn.addEventListener('click', () => {
  showScreen('browseScreen')
  showBrowse()
})

const loadAiKeyStatus = initAiSettings()

// The header falls back to the email until a username comes back, rather
// than waiting on the request -- a signed-in header that is blank for a
// moment reads as broken. `signedInEmail` itself is declared with the other
// state at the top of the file; see the note there for why.

function updateWhoami(fallbackEmail) {
  el.whoami.textContent = fallbackEmail
  api.username().then(({ username }) => {
    if (username) el.whoami.textContent = username
  }).catch((error) => console.error(error))
}

const loadAccountEmail = initAccountSettings(
  (username) => { el.whoami.textContent = username || signedInEmail })

const openReviewSettings = initReviewSettings({
  onStartReviewing: () => {
    openReader()
    el.reviewMissedBtn.click()
  },
  onOpenReviewList: () => {
    showScreen('reviewListScreen')
    showReviewList()
  },
})

el.reviewSettingsBtn.addEventListener('click', openReviewSettings)

// ------------------------------------------------------------ reset stats --

function showResetModal(visible) {
  el.resetStatsModal.classList.toggle('hidden', !visible)
  el.resetStatsModal.classList.toggle('flex', visible)
  if (visible) el.resetStatsMessage.textContent = ''
}

el.resetStatsBtn.addEventListener('click', () => showResetModal(true))
el.cancelResetStatsBtn.addEventListener('click', () => showResetModal(false))

el.confirmResetStatsBtn.addEventListener('click', async () => {
  el.confirmResetStatsBtn.disabled = true
  try {
    await api.resetStats()
    showResetModal(false)
    // The lifetime tiles on the reader and the profile both read from the
    // same account-wide totals this just changed, so both are refreshed --
    // not toggled to zero locally, which would drift the moment the server's
    // own accounting (streak, review counts) turned out to disagree.
    await loadStats()
    if (!el.profileScreen.classList.contains('hidden')) {
      showProfile(filters)
    }
  } catch (error) {
    el.resetStatsMessage.textContent = error.message
  } finally {
    el.confirmResetStatsBtn.disabled = false
  }
})
el.backToReaderBtn.addEventListener('click', openReader)
el.aboutBtn.addEventListener('click', () => showScreen('aboutScreen'))
el.backFromAboutBtn.addEventListener('click', openReader)

// The notebook owns two of the screens above and swaps between them itself;
// showScreen only has to get us onto the first one.
const notebook = initNotebook({ onBack: openReader, onNeedAiKey: openSettings })

el.notebookBtn.addEventListener('click', () => {
  showScreen('notebookHubScreen')
  notebook.openHub()
})

// ----------------------------------------------------------------- filters --

/** Selected values of a multi-select, dropping the "Any …" empty option. */
function chosen(select) {
  return [...select.selectedOptions].map((o) => o.value).filter(Boolean)
}

async function loadFilters() {
  try {
    const payload = await api.filters()
    filters = payload.categories
    el.categorySelect.innerHTML = '<option value="" selected>Any Category</option>'
    for (const category of filters) {
      const option = document.createElement('option')
      option.value = category.category
      option.textContent = `${category.category} (${category.questions.toLocaleString()})`
      el.categorySelect.append(option)
    }
  } catch (error) {
    console.error(error)
  }
}

/** Offer subcategories for whatever categories are picked, or hide the box.
 *
 * Hidden rather than shown empty: an empty select that cannot be filled looks
 * broken, and several real categories (Mythology, Geography) have no
 * subcategories at all.
 */
function refreshSubcategories() {
  const picked = chosen(el.categorySelect)
  const available = filters
    .filter((c) => picked.includes(c.category))
    .flatMap((c) => c.subcategories)

  el.subcategorySelect.innerHTML = ''
  for (const sub of available) {
    const option = document.createElement('option')
    option.value = sub.name
    option.textContent = `${sub.name} (${sub.questions.toLocaleString()})`
    el.subcategorySelect.append(option)
  }
  el.subcategoryWrapper.classList.toggle('hidden', available.length === 0)
}

el.categorySelect.addEventListener('change', () => {
  refreshSubcategories()
  loadStats()
})

/** What the reader should ask for. Subcategory wins over category when set:
 *  picking "American Literature" under "Literature" means the narrower one. */
function questionFilters() {
  const subcategory = chosen(el.subcategorySelect)
  const difficulty = chosen(el.difficultySelect)
  return {
    ...(subcategory.length ? { subcategory } : { category: chosen(el.categorySelect) }),
    ...(difficulty.length ? { difficulty } : {}),
  }
}

// ---------------------------------------------------------------- the read --

/** Milliseconds between words. Copied from the desktop: the slider reads as
 *  speed, the timer wants a delay, so the scale is inverted here. */
function readingDelayMs() {
  return 550 - parseInt(el.readingSpeed.value, 10)
}

function updateSpeedDisplay() {
  if (!voiceMode) {
    el.speedDisplay.textContent = `${readingDelayMs()} ms per word`
    return
  }
  // A speaking utterance's rate is fixed by the Web Speech API -- it can only
  // be set when speech starts. Restarting mid-tossup to apply a change
  // repeats a word and makes the text flicker, so the change lands on the
  // next tossup and the label says so rather than looking broken.
  const pending = voice.isSpeaking()
  el.speedDisplay.textContent =
    `${voice.speechRateFor(el.readingSpeed.value).toFixed(1)}x speaking speed` +
    (pending ? ' — next tossup' : '')
}

el.readingSpeed.addEventListener('input', updateSpeedDisplay)
updateSpeedDisplay()

// The powermark toggle is a class on <body> rather than a change to what the
// reader emits, so it applies to the tossup already on screen instead of only
// to the next one.
el.powerHighlightToggle.addEventListener('change', () => {
  document.body.classList.toggle('no-power', !el.powerHighlightToggle.checked)
})

// ------------------------------------------------------------- voice mode --

if (!voice.voiceSupported()) {
  el.voiceModeToggle.disabled = true
  el.voiceUnsupported.classList.remove('hidden')
}

el.voiceModeToggle.addEventListener('change', () => {
  voiceMode = el.voiceModeToggle.checked
  el.showTextRow.classList.toggle('hidden', !voiceMode)
  updateSpeedDisplay()
  // Switching mode mid-tossup would leave the reader half-spoken and
  // half-printed, so stop and let the player start the next one cleanly.
  abandonTossup()
  el.questionContainer.textContent = voiceMode
    ? 'Voice Mode on — press "Start Reader" to hear a tossup.'
    : 'Click "Start Reader" to start practicing'
})

el.showTextToggle.addEventListener('change', () => {
  showTossupText = el.showTextToggle.checked
  // Reflect the change straight away rather than waiting for the next tossup.
  if (voiceMode && words.length) {
    if (showTossupText) paintWords(Math.min(wordIndex + 1, words.length))
    else el.questionContainer.textContent = '🔊 Listening…'
  }
})

// Speech keeps running after the tab closes on some platforms.
window.addEventListener('beforeunload', () => voice.stopSpeaking())

/** One word, marked up if it falls inside the power.
 *
 * Shared by every path that paints question text, so the highlight cannot be
 * applied while reading and then lost on the end-of-tossup reveal -- which is
 * exactly what used to happen on the desktop: both reveal paths assigned the
 * raw question text, so the marking vanished at the moment you would most want
 * it, looking back at what you just answered.
 */
function wordHtml(i) {
  const word = escapeHtml(words[i])
  if (powerIdx === -1 || i > powerIdx) return word
  // The (*) is a marker rather than part of the sentence, so it is styled
  // apart from the words around it instead of being bolded along with them.
  const marked = word.replace(/\(\*\)/g, '<span class="power-mark">(*)</span>')
  return `<strong class="power-text">${marked}</strong>`
}

/** Question text is data, and it goes into innerHTML for the power markup, so
 *  it is escaped first. The set is packet text rather than user input, but the
 *  rule is about where a string is going, not where it came from. */
function escapeHtml(text) {
  return String(text).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))
}

function paintWords(upto) {
  let html = ''
  for (let i = 0; i < upto && i < words.length; i++) html += wordHtml(i) + ' '
  el.questionContainer.innerHTML = html
}

function stopTicker() {
  clearTimeout(ticker)
  ticker = null
}

// Read all the way out with nobody buzzing -- shared by the plain reveal and
// Voice Mode, since a tossup counts as heard the same way either way. Five
// seconds of dead time before the tossup closes; ending the instant the last
// word lands would score a pass on a question the reader is still finishing
// saying.
function onTossupFullyRead() {
  if (!buzzed && !countdownInterval) {
    startCountdown(DEAD_TIME_SECONDS, 'Buzz in', () => finish({ didBuzz: false, guess: '' }))
  }
}

function tick() {
  if (buzzed || paused) return
  if (wordIndex >= words.length) {
    // While the timer is only estimating position for still-playing speech
    // (voice.js couldn't get onboundary events on this platform), voice.js's
    // own onEnd closes the tossup out instead -- reaching the end of the
    // estimate does not mean the voice has actually finished.
    if (voiceEstimating) return
    onTossupFullyRead()
    return
  }
  // In Voice Mode the words are being spoken, so only paint them here if the
  // user also asked to see the text -- this is also the fallback ticker
  // while voice.js estimates position, and that path must stay silent unless
  // showTossupText says otherwise.
  if (!voiceMode || showTossupText) {
    // Appended one word at a time rather than repainting the whole container
    // on every tick, which is why this does not just call paintWords().
    el.questionContainer.insertAdjacentHTML('beforeend', wordHtml(wordIndex) + ' ')
  }
  wordIndex++
  ticker = setTimeout(tick, readingDelayMs())
}

/** Read the tossup aloud from `startIndex`, falling back to the plain ticker
 *  if speech isn't supported or dies mid-question. */
function speakCurrentTossup(startIndex = 0) {
  if (!voice.voiceSupported()) { tick(); return }
  voiceEstimating = false

  voice.speak(words, startIndex, voice.speechRateFor(el.readingSpeed.value), {
    onWord: (idx) => {
      wordIndex = idx
      voiceEstimating = false
      // `onboundary` fires as a word *starts*; wordIndex counts words already
      // finished, so revealing only up to wordIndex would leave the text one
      // word behind the voice. Include the word currently being spoken.
      if (showTossupText) paintWords(Math.min(wordIndex + 1, words.length))
    },
    onEnd: () => {
      wordIndex = words.length
      if (showTossupText) paintWords(words.length)
      onTossupFullyRead()
    },
    onError: () => tick(),      // speech died -- still playable, just silent
    onFallback: () => {
      // No boundary events on this platform. Audio keeps playing; the ticker
      // estimates position so celerity stays meaningful, but onEnd above is
      // still what closes the tossup out.
      voiceEstimating = true
      tick()
    },
  })
}

// ---------------------------------------------------------------- countdown --

// `countdownInterval` itself is declared with the other state at the top of
// the file rather than here; see the note there for why.

function stopCountdown() {
  clearInterval(countdownInterval)
  countdownInterval = null
  el.buzzTimer.classList.add('hidden')
}

/** Show a ticking countdown; call onExpire() when it hits zero.
 *
 * The deadline is wall-clock rather than a tick count, so a tab that was
 * backgrounded (where timers are throttled) comes back with the right answer
 * instead of with however many ticks the browser felt like running.
 */
function startCountdown(seconds, label, onExpire) {
  stopCountdown()
  const endsAt = Date.now() + seconds * 1000

  const render = () => {
    const left = Math.max(0, endsAt - Date.now())
    el.buzzTimer.textContent = `${label} ${(left / 1000).toFixed(1)}s`
    el.buzzTimer.classList.toggle('text-red-500', left <= 3000)
    el.buzzTimer.classList.toggle('text-text-muted', left > 3000)
    if (left <= 0) {
      stopCountdown()
      onExpire()
    }
  }

  el.buzzTimer.classList.remove('hidden')
  render()
  countdownInterval = setInterval(render, 100)
}

// ------------------------------------------------------------ load a tossup --

/** Reset everything a live tossup owns. */
function abandonTossup() {
  stopTicker()
  stopCountdown()
  voice.stopSpeaking()
  voiceEstimating = false
  words = []
  wordIndex = 0
  powerIdx = -1
  question = null
  buzzed = false
  paused = false
  submitting = false
  el.pauseBtn.textContent = 'Pause'
  el.answerInput.disabled = true
  el.answerInput.value = ''
  el.answerInput.placeholder = 'Buzz to answer'
  el.submitAnswerBtn.dataset.state = 'buzz'
  el.submitAnswerBtn.textContent = 'Buzz'
  el.submitAnswerBtn.disabled = true
  el.answerFeedback.classList.add('hidden')
  el.addToMissedBtn.classList.add('hidden')
  el.addToMissedBtn.disabled = false
  el.questionMeta.textContent = ''
  el.reviewAheadNotice.classList.add('hidden')
  el.getExplanationBtn.disabled = true
  el.explanationContainer.textContent =
    'Click "Get Explanation" to see an AI-generated explanation of the question.'
  el.createFlashcardBtn.disabled = true
  el.draftFlashcardsContainer.textContent =
    'Click "Create Flashcard" to draft flashcards from this tossup.'
}

async function loadQuestion(fetcher) {
  abandonTossup()
  el.questionContainer.textContent = 'Loading…'
  try {
    question = await fetcher()
  } catch (error) {
    el.questionContainer.textContent = error instanceof ApiError && error.empty
      ? error.message
      : 'Could not load a question.'
    if (error instanceof ApiError && error.empty && reviewMode) leaveReview()
    return
  }

  words = String(question.question).split(/\s+/).filter(Boolean)
  // 41% of the set has no powermark, and the mark can be fused to punctuation
  // ("beta,(*)"), so this looks for it inside a word rather than as a word --
  // the same rule the server scores by (routes/answers.py:_power_index).
  powerIdx = words.findIndex((w) => w.includes('(*)'))
  wordIndex = 0

  el.questionMeta.textContent = [
    question.category, question.subcategory,
    question.difficulty != null ? `difficulty ${question.difficulty}` : null,
    question.set_name,
  ].filter(Boolean).join(' · ')

  // An adaptive question carries the state the server used to pick it. The
  // restore key in particular has to come back on the answer, so the skill
  // update lands on the same selection this session is playing.
  if (adaptive && question.adaptive) {
    adaptive.restoreKey = question.adaptive.restoreKey
    adaptive.subject = question.adaptive.subcategory
    adaptive.skill = question.adaptive.skill
    paintAdaptiveStats()

    // Said once, on the first question, when there was already a model to pick
    // up. Silence here would make a resumed session look like a fresh one.
    if (question.adaptive.resumed && adaptive.answered === 0) {
      el.adaptiveResumed.textContent =
        `Picking up where you left off — ${question.adaptive.questionsServed - 1} ` +
        'questions already seen in this subject.'
      el.adaptiveResumed.classList.remove('hidden')
    }
  }

  // Review serves whatever is scheduled soonest when nothing is actually due,
  // and says so rather than pretending the schedule was met.
  if (reviewMode && question.is_due === false) {
    el.reviewAheadNotice.textContent =
      'Nothing is due yet — this one is scheduled ahead of time.'
    el.reviewAheadNotice.classList.remove('hidden')
  }

  el.submitAnswerBtn.disabled = false
  el.getExplanationBtn.disabled = false
  el.createFlashcardBtn.disabled = false
  el.questionContainer.innerHTML = ''

  // Voice Mode speaks the tossup; normal mode reveals it word by word.
  if (voiceMode && voice.voiceSupported()) {
    if (!showTossupText) el.questionContainer.textContent = '🔊 Listening…'
    speakCurrentTossup()
  } else {
    tick()
  }
}

el.getNewQuestionBtn.addEventListener('click', () => {
  // Start Reader is how you get back to ordinary play, so it leaves both of the
  // modes rather than only review.
  if (reviewMode) leaveReview()
  if (adaptive) leaveAdaptive()
  loadQuestion(() => api.randomQuestion(questionFilters()))
})

el.getManyQuestionsBtn.addEventListener('click', () => {
  // Skipping mid-read records nothing. A question you walked away from was not
  // heard, and counting it would quietly inflate every rate on the profile.
  if (adaptive) nextAdaptiveQuestion()
  else if (reviewMode) loadQuestion(() => api.nextReview(chosen(el.categorySelect)))
  else loadQuestion(() => api.randomQuestion(questionFilters()))
})

el.reviewMissedBtn.addEventListener('click', () => {
  if (adaptive) leaveAdaptive()
  reviewMode = true
  el.stopReviewBtn.classList.remove('hidden')
  el.reviewMissedBtn.textContent = 'Review Next'
  loadQuestion(() => api.nextReview(chosen(el.categorySelect)))
})

el.stopReviewBtn.addEventListener('click', () => {
  leaveReview()
  // Leaving review does NOT start a fresh question. Stopping something should
  // stop it, not swap it for a random tossup you did not ask for.
  abandonTossup()
  el.questionContainer.textContent = 'Click “Start Reader” to start practicing'
})

function leaveReview() {
  reviewMode = false
  el.stopReviewBtn.classList.add('hidden')
  el.reviewMissedBtn.textContent = 'Review Missed'
}

el.pauseBtn.addEventListener('click', () => {
  if (!words.length || buzzed) return
  paused = !paused
  el.pauseBtn.textContent = paused ? 'Resume' : 'Pause'
  const speaking = voiceMode && voice.isSpeaking()
  if (paused) {
    stopTicker()
    if (speaking) voice.pauseSpeaking()
  } else if (speaking) {
    voice.resumeSpeaking()
  } else if (voiceMode && voice.voiceSupported()) {
    // Not every pause leaves an utterance to resume: `onend`/`onerror` can
    // land between the pause click and this one and null it out first, and
    // `speech.resume()` has nothing left to act on then. Restart the
    // utterance at the current word instead of falling through to the plain
    // ticker, which would start the *text* ticker with no audio behind it.
    speakCurrentTossup(wordIndex)
  } else {
    tick()
  }
})

// -------------------------------------------------------------------- buzz --

function buzz() {
  if (buzzed || !words.length || wordIndex === 0 || submitting) return
  buzzed = true
  stopTicker()
  stopCountdown()
  // Cut the audio immediately -- hearing another word after buzzing would be
  // wrong both for the feel of it and for the celerity already recorded.
  voice.stopSpeaking()
  el.answerInput.disabled = false
  el.answerInput.placeholder = 'Your answer'
  el.answerInput.focus()
  el.submitAnswerBtn.dataset.state = 'submit'
  el.submitAnswerBtn.textContent = 'Submit'
  startCountdown(ANSWER_TIME_SECONDS, 'Answer in', () => {
    // Out of time. Submitting blank is allowed and scores a neg, which is what
    // a real timeout costs.
    finish({ didBuzz: true, guess: el.answerInput.value })
  })
}

el.submitAnswerBtn.addEventListener('click', () => {
  if (el.submitAnswerBtn.dataset.state === 'buzz') buzz()
  else finish({ didBuzz: true, guess: el.answerInput.value })
})

el.answerInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') finish({ didBuzz: true, guess: el.answerInput.value })
})

document.addEventListener('keydown', (event) => {
  // A range slider is an <input> you cannot type into, so testing the tag name
  // alone would kill every shortcut after touching one. Only real text entry
  // swallows keys.
  const typing = event.target.matches('input:not([type=range]), textarea, select')
  if (typing) return
  if (el.appScreen.classList.contains('hidden')) return

  // "?" is the exception to every rule below: it is how you find out the rest
  // exist, so it works with shortcuts switched off and on any screen.
  if (event.key === '?') {
    el.settingsBtn.click()
    return
  }
  if (!el.shortcutsToggle.checked) return
  // The reader's shortcuts belong to the reader. Pressing S on the profile
  // page would start a tossup on a screen that cannot show it.
  if (el.readerScreen.classList.contains('hidden')) return

  const key = event.key.toLowerCase()
  if ((event.code === 'Space' || key === 'b') && !buzzed && words.length) {
    event.preventDefault()
    buzz()
  } else if (key === 's') {
    el.getNewQuestionBtn.click()
    toast('Start Reader')
  } else if (key === 'n') {
    el.getManyQuestionsBtn.click()
    toast('Next question')
  } else if (key === 'a') {
    el.adaptiveLearningBtn.click()
  } else if (key === 'p') {
    el.pauseBtn.click()
    toast(paused ? 'Paused' : 'Resumed')
  } else if (key === 'e' && !el.getExplanationBtn.disabled) {
    el.getExplanationBtn.click()
  }
})

// ------------------------------------------------------------------ submit --

async function finish({ didBuzz, guess }) {
  // Locked *before* the request, not after. The round trip can take seconds,
  // and Enter, the button and the timeout path all reach this function -- so
  // disabling the button after awaiting leaves a window where the answer can
  // be scored twice. On the desktop build that window wrote two user_stats
  // rows, two review attempts and -10 points for one neg.
  if (submitting || !question) return
  submitting = true
  stopTicker()
  stopCountdown()
  voice.stopSpeaking()
  el.submitAnswerBtn.disabled = true
  el.answerInput.disabled = true

  try {
    const result = await api.submitAnswer({
      questionId: question.id,
      // An adaptive sitting is its own session -- see the note where it is
      // created. Ordinary play keeps the page-wide id.
      sessionId: adaptive?.sessionId ?? sessionId,
      guess,
      buzzed: didBuzz,
      wordsRead: wordIndex,
      rebuzzable: allowRebuzz,
      // Only the restore key travels. Which cluster's skill moves is decided
      // server-side from the question row -- see _apply_to_skill_model.
      ...(adaptive?.restoreKey ? { adaptive: { restoreKey: adaptive.restoreKey } } : {}),
    })

    if (result.retry) {
      // Wrong, but words remain and rebuzzes are on -- the server recorded
      // nothing (see routes/answers.py), so this tossup just keeps reading.
      buzzed = false
      el.submitAnswerBtn.dataset.state = 'buzz'
      el.submitAnswerBtn.textContent = 'Buzz'
      el.submitAnswerBtn.disabled = false
      el.answerInput.value = ''
      el.answerInput.disabled = true
      el.answerInput.placeholder = 'Buzz to answer'
      showFeedback('Not it — keep listening.', false)
      if (voiceMode && voice.voiceSupported()) speakCurrentTossup(wordIndex)
      else tick()
      return
    }

    showResult(result)
    loadStats()

    if (adaptive && result.adaptive?.graded) {
      adaptive.answered += 1
      if (result.correct) adaptive.correct += 1
      adaptive.skill = result.adaptive.skill
      paintAdaptiveStats()
    }
  } catch (error) {
    showFeedback(error.message, false)
  } finally {
    // Released in a finally: a backend that disappears mid-answer would
    // otherwise leave this stuck and silently ignore every later submit.
    submitting = false
  }
}

function showFeedback(html, good) {
  el.answerFeedback.className =
    `mt-4 rounded-lg p-3 text-sm ${good ? 'bg-emerald-900/50 text-emerald-200'
                                        : 'bg-red-900/50 text-red-200'}`
  el.answerFeedback.innerHTML = html
}

function showResult(result) {
  const verdict = { power: 'Power!', ten: 'Correct', neg: 'Neg', pass: 'No buzz' }[result.outcome]
  const sign = result.points > 0 ? '+' : ''
  showFeedback(
    `<strong>${verdict} (${sign}${result.points})</strong> — ANSWER: ` +
    `${escapeHtml(result.answer)}` +
    (result.scoredOffline ? ' · scored offline' : ''),
    Boolean(result.correct))

  // The whole tossup, with the power still marked -- this is the moment the
  // marking is most worth having, and the desktop used to lose it right here.
  paintWords(words.length)

  // The label follows the outcome. "Add to missed" on a question you just got
  // right reads as the wrong control, and the endpoint is the same either way.
  el.addToMissedBtn.classList.toggle('hidden', reviewMode)
  el.addToMissedBtn.textContent = result.correct ? 'Review this again' : 'Add to Missed'
}

el.addToMissedBtn.addEventListener('click', async () => {
  if (!question) return
  el.addToMissedBtn.disabled = true
  try {
    await api.addToReview(question.id)
    el.addToMissedBtn.textContent = 'Added'
  } catch (error) {
    el.addToMissedBtn.textContent = error.message
  }
})

// -------------------------------------------------------------- AI explain --

el.getExplanationBtn.addEventListener('click', async () => {
  if (!question) return
  el.getExplanationBtn.disabled = true
  el.explanationContainer.textContent = 'Asking the AI…'
  try {
    // The question and answer are read off the id on the server -- see
    // routes/ai.py -- so only the id and whatever was typed as a guess go up.
    const { explanation } = await api.explainQuestion(question.id, el.answerInput.value)
    el.explanationContainer.innerHTML = renderMarkdown(explanation)
  } catch (error) {
    if (error instanceof ApiError && error.payload?.code === 'no_key') {
      // Not a failure -- a setup step. Routed to Settings rather than to the
      // generic error text, with the field it needs already in view.
      el.explanationContainer.innerHTML =
        `<p>${escapeHtml(error.message)}</p>`
      openSettings()
      $('aiKeyInput')?.focus()
    } else {
      el.explanationContainer.textContent = error.message
    }
    el.getExplanationBtn.disabled = false
  }
})

// ---------------------------------------------------------------- flashcards --

function paintDraftCards(cards, category) {
  if (!cards.length) {
    el.draftFlashcardsContainer.textContent = 'No usable cards came back. Try again.'
    return
  }
  // Each card gets its own Save -- a draft is not all-or-nothing, and the
  // same "half a card is not a card" rule the notebook applies server-side
  // means a player should be able to keep the three good ones and discard a
  // fourth that came back garbled, rather than losing all four to one bad card.
  el.draftFlashcardsContainer.innerHTML = cards.map((c, i) => `
    <div data-draft-card="${i}" class="rounded-lg bg-secondary-dark p-3">
      <p class="font-bold">${escapeHtml(c.term)}</p>
      <p class="mt-1 text-text-muted">${escapeHtml(c.definition)}</p>
      <button data-save-draft="${i}" class="mt-2 rounded-full bg-green-600 px-3 py-1 text-xs font-bold text-white hover:bg-green-700">Save</button>
    </div>`).join('')

  el.draftFlashcardsContainer.querySelectorAll('[data-save-draft]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const card = cards[Number(btn.dataset.saveDraft)]
      btn.disabled = true
      btn.textContent = 'Saving…'
      try {
        await api.saveFlashcards({
          category, sourceQuestionId: question?.id,
          flashcards: [{ term: card.term, definition: card.definition }],
        })
        btn.textContent = 'Saved'
      } catch (error) {
        btn.textContent = error.message
        btn.disabled = false
      }
    })
  })
}

el.createFlashcardBtn.addEventListener('click', async () => {
  if (!question) return
  el.createFlashcardBtn.disabled = true
  el.draftFlashcardsContainer.textContent = 'Asking the AI…'
  try {
    const { cards } = await api.generateFlashcards(question.id)
    paintDraftCards(cards, question.category)
  } catch (error) {
    if (error instanceof ApiError && error.payload?.code === 'no_key') {
      el.draftFlashcardsContainer.innerHTML = `<p>${escapeHtml(error.message)}</p>`
      openSettings()
      $('aiKeyInput')?.focus()
    } else {
      el.draftFlashcardsContainer.textContent = error.message
    }
  } finally {
    el.createFlashcardBtn.disabled = false
  }
})

// ------------------------------------------------------------------- stats --

async function loadStats() {
  try {
    const category = chosen(el.categorySelect)[0] ?? ''
    const { lifetime } = await api.stats(category)
    el.ptn.textContent = `${lifetime.powers} / ${lifetime.tens} / ${lifetime.negs}`
    el.tossupsHeard.textContent = lifetime.tossups
    el.pointsScored.textContent = lifetime.points
    el.celerity.textContent = lifetime.averageCelerity === null
      ? '0.000' : lifetime.averageCelerity.toFixed(3)
  } catch (error) {
    console.error(error)
  }
}

// -------------------------------------------------------- save a highlight --

/** The phrase currently selected inside the tossup, or ''. */
function selectedClue() {
  const selection = window.getSelection()
  if (!selection || selection.isCollapsed) return ''
  // Only a selection that actually lies inside the question text counts --
  // selecting the answerline in the feedback box, or a menu label, is not a
  // clue and would otherwise be saved as one.
  if (!el.questionContainer.contains(selection.anchorNode)) return ''
  return selection.toString().trim()
}

document.addEventListener('selectionchange', () => {
  const has = Boolean(question) && selectedClue().length > 2
  el.saveHighlightBtn.classList.toggle('hidden', !has)
  if (has) {
    el.saveHighlightBtn.disabled = false
    el.saveHighlightBtn.textContent = 'Save Highlight'
  }
})

el.saveHighlightBtn.addEventListener('click', async () => {
  const clueText = selectedClue()
  if (!clueText || !question) return

  el.saveHighlightBtn.disabled = true
  el.saveHighlightBtn.textContent = 'Saving…'
  try {
    // Only the text and which question it came from. The answerline and the
    // shelf are both worked out server-side from the question id -- see
    // routes/notebook.py -- so a clue saved mid-tossup still files correctly
    // even though this page has not been told the answer yet.
    await api.saveClue({ clueText, sourceQuestionId: question.id })
    el.saveHighlightBtn.textContent = 'Saved'
    toast('Clue saved to your notebook')
  } catch (error) {
    el.saveHighlightBtn.textContent = error.message
  }
})

// ------------------------------------------------------- adaptive learning --

/** Build the subject picker from what the server says it can actually serve.
 *
 * Not a hardcoded list: the recommender only works on subjects that have a
 * real topic clustering behind them, and which those are is a property of the
 * question set rather than of the app. `inProgress` is what the user has
 * already built up, so a subject they have played reads as somewhere to
 * return to instead of as a fresh start.
 */
async function loadAdaptiveCatalogue() {
  if (adaptiveCatalogue) return adaptiveCatalogue
  adaptiveCatalogue = await api.adaptiveCategories()

  el.adaptiveCategorySelect.innerHTML = ''
  for (const category of adaptiveCatalogue.categories) {
    const group = document.createElement('optgroup')
    group.label = category.name

    // The general category itself, which stands for all of its shelves.
    const all = new Option(`${category.name} — all subjects`, category.name)
    group.append(all)

    for (const sub of category.subcategories) {
      const saved = adaptiveCatalogue.inProgress[sub.name]
      group.append(new Option(
        saved
          ? `  ${sub.name} (${sub.clusters} topics · ${saved.questionsServed} seen)`
          : `  ${sub.name} (${sub.clusters} topics)`,
        sub.name))
    }
    el.adaptiveCategorySelect.append(group)
  }
  return adaptiveCatalogue
}

/** One weight slider per pick, shown only when there is a split to make. */
function refreshAdaptiveWeights() {
  const picks = chosen(el.adaptiveCategorySelect)
  el.startAdaptiveSessionBtn.disabled = picks.length === 0
  el.adaptiveWeightsWrap.classList.toggle('hidden', picks.length < 2)
  if (picks.length < 2) return

  const existing = new Map(
    [...el.adaptiveWeights.querySelectorAll('input')].map((i) => [i.dataset.name, i.value]))

  el.adaptiveWeights.innerHTML = ''
  for (const name of picks) {
    const row = document.createElement('div')
    row.innerHTML = `
      <div class="flex items-baseline justify-between text-xs text-[#baa7a1]">
        <span>${escapeHtml(name)}</span>
        <span data-share class="font-bold text-white"></span>
      </div>
      <input type="range" min="0" max="100" step="5"
             data-name="${escapeHtml(name)}"
             value="${existing.get(name) ?? 50}"
             class="mt-1 h-2 w-full cursor-pointer appearance-none rounded-lg bg-[#584741]">`
    el.adaptiveWeights.append(row)
  }
  paintAdaptiveShares()
}

/** Show each slider as a share of the whole rather than as its raw value.
 *
 * The sliders move independently, so their raw numbers do not add to anything
 * in particular. What the user is actually choosing is a ratio, and the server
 * normalises it the same way -- so showing the normalised share is showing
 * what will happen, and showing "70" next to "70" would not.
 */
function paintAdaptiveShares() {
  const sliders = [...el.adaptiveWeights.querySelectorAll('input')]
  const total = sliders.reduce((sum, s) => sum + Number(s.value), 0)
  for (const slider of sliders) {
    const share = total > 0 ? Math.round((Number(slider.value) / total) * 100) : 0
    slider.parentElement.querySelector('[data-share]').textContent = `${share}%`
  }
  // All-zero is the one state the server has to fall back on, so say what it
  // will do rather than letting it look like a session of nothing.
  el.adaptiveWeightTotal.textContent = total > 0
    ? 'Shares are relative — they do not need to add to 100.'
    : 'Everything is at zero, so the split will be even.'
  el.adaptiveWeightTotal.className = total > 0
    ? 'mt-2 text-xs font-bold text-green-400'
    : 'mt-2 text-xs font-bold text-[#f6b17a]'
}

el.adaptiveCategorySelect.addEventListener('change', refreshAdaptiveWeights)
el.adaptiveWeights.addEventListener('input', paintAdaptiveShares)
el.resetAdaptiveWeightsBtn.addEventListener('click', () => {
  for (const slider of el.adaptiveWeights.querySelectorAll('input')) slider.value = 50
  paintAdaptiveShares()
})

el.adaptiveLearningBtn.addEventListener('click', async () => {
  showScreen('adaptiveSetupScreen')
  el.adaptiveSetupMessage.textContent = ''
  try {
    await loadAdaptiveCatalogue()
    refreshAdaptiveWeights()
  } catch (error) {
    el.adaptiveSetupMessage.textContent = error.message
  }
})

el.cancelAdaptiveBtn.addEventListener('click', openReader)

el.startAdaptiveSessionBtn.addEventListener('click', () => {
  const picks = chosen(el.adaptiveCategorySelect)
  if (!picks.length) return

  const sliders = [...el.adaptiveWeights.querySelectorAll('input')]
  const weights = picks.length < 2
    ? picks.map(() => 1)
    : picks.map((name) => {
        const slider = sliders.find((s) => s.dataset.name === name)
        return slider ? Number(slider.value) : 1
      })

  if (reviewMode) leaveReview()
  adaptive = {
    picks, weights,
    // Its own session id, not the page's. /api/adaptive/end counts the sitting
    // out of user_stats by session_id, so sharing the page-wide one made the
    // summary include every ordinary tossup answered before the session began
    // -- a Save & Quit after one adaptive question reported two answered.
    sessionId: crypto.randomUUID(),
    startedAt: new Date().toISOString(),
    answered: 0, correct: 0,
    restoreKey: null, skill: null, subject: null,
  }
  el.adaptiveSessionStats.classList.remove('hidden')
  paintAdaptiveStats()
  openReader()
  nextAdaptiveQuestion()
})

function nextAdaptiveQuestion() {
  loadQuestion(() => api.adaptiveQuestion(adaptive.picks, adaptive.weights))
}

function paintAdaptiveStats() {
  if (!adaptive) return
  el.adaptiveSubject.textContent = adaptive.subject ?? '—'
  el.adaptiveSkill.textContent = adaptive.skill == null ? '—' : adaptive.skill.toFixed(2)
  el.adaptiveAnswered.textContent = adaptive.answered
  el.adaptiveCorrect.textContent = adaptive.correct
  el.adaptiveAccuracy.textContent = adaptive.answered
    ? `${Math.round((adaptive.correct / adaptive.answered) * 100)}%`
    : '0%'
}

el.saveAndQuitAdaptiveBtn.addEventListener('click', async () => {
  if (!adaptive) return
  const { restoreKey } = adaptive
  el.saveAndQuitAdaptiveBtn.disabled = true
  try {
    // Nothing is flushed here -- the skill model was written on every question
    // and every answer, so closing the tab loses none of it. This only records
    // the session summary.
    if (restoreKey) {
      await api.adaptiveEnd(restoreKey, adaptive.sessionId, adaptive.startedAt)
    }
  } catch (error) {
    console.error(error)
  } finally {
    el.saveAndQuitAdaptiveBtn.disabled = false
    leaveAdaptive()
    abandonTossup()
    el.questionContainer.textContent = 'Click “Start Reader” to start practicing'
  }
})

function leaveAdaptive() {
  adaptive = null
  el.adaptiveSessionStats.classList.add('hidden')
  el.adaptiveResumed.classList.add('hidden')
}

// ---------------------------------------------------------------- settings --

// Every shortcut the reader actually handles, written out once so the two
// places that list them cannot drift apart from each other or from the handler.
const SHORTCUTS = [
  ['Space / B', 'Buzz'],
  ['Enter', 'Submit your answer'],
  ['S', 'Start Reader'],
  ['N', 'Next question'],
  ['A', 'Adaptive Learning'],
  ['P', 'Pause or resume'],
  ['E', 'Get AI explanation'],
  ['?', 'This list'],
]

function paintShortcutLists() {
  const html = SHORTCUTS.map(([key, what]) => `
    <div class="flex items-baseline justify-between gap-4">
      <dt class="font-mono text-amber-400">${escapeHtml(key)}</dt>
      <dd class="text-right text-text-muted">${escapeHtml(what)}</dd>
    </div>`).join('')
  el.shortcutListSettings.innerHTML = html
  el.shortcutListAbout.innerHTML = html
}
paintShortcutLists()

// Font size and the shortcut switch are local prefs, kept in localStorage.
// The Gemini API key is not: it is a per-account secret, so it round-trips
// through the server on every open/save -- see aiSettings.js.
const PREFS = 'forgeqb.prefs'

function loadPrefs() {
  let saved = {}
  try { saved = JSON.parse(localStorage.getItem(PREFS)) ?? {} } catch { saved = {} }
  applyFontSize(saved.fontSize ?? 16)
  el.shortcutsToggle.checked = saved.shortcuts !== false
  el.allowRebuzzToggle.checked = saved.allowRebuzz === true
  allowRebuzz = el.allowRebuzzToggle.checked
  if (saved.readingSpeed) {
    el.readingSpeed.value = saved.readingSpeed
    updateSpeedDisplay()
  }
  if (saved.powermark === false) {
    el.powerHighlightToggle.checked = false
    document.body.classList.add('no-power')
  }
}

function savePrefs() {
  localStorage.setItem(PREFS, JSON.stringify({
    fontSize: Number(el.fontSizeRange.value),
    shortcuts: el.shortcutsToggle.checked,
    readingSpeed: Number(el.readingSpeed.value),
    powermark: el.powerHighlightToggle.checked,
    allowRebuzz: el.allowRebuzzToggle.checked,
  }))
}

/** Scales the whole interface, not just the tossup: every size in the app is
 *  in rem, which is relative to the root font size, so text and the spacing
 *  around it grow together instead of text overflowing boxes that stayed put. */
function applyFontSize(px) {
  el.fontSizeRange.value = px
  el.fontSizeDisplay.textContent = `${px}px`
  document.documentElement.style.fontSize = `${px}px`
}

function openSettings() {
  el.settingsModal.classList.remove('hidden')
  el.settingsModal.classList.add('flex')
  loadAiKeyStatus()
  loadAccountEmail()
}
el.settingsBtn.addEventListener('click', openSettings)

function closeSettings() {
  el.settingsModal.classList.add('hidden')
  el.settingsModal.classList.remove('flex')
}

el.closeSettingsBtn.addEventListener('click', closeSettings)
el.settingsModal.addEventListener('click', (event) => {
  // Only the backdrop itself, not a click that happened to bubble out of the
  // dialog -- dragging the font slider and releasing outside the panel would
  // otherwise close it mid-adjustment.
  if (event.target === el.settingsModal) closeSettings()
})

// Applied live while dragging and saved immediately: a font size you cannot
// see until you press Save is a guess.
el.fontSizeRange.addEventListener('input', () => {
  applyFontSize(Number(el.fontSizeRange.value))
  savePrefs()
})
el.fontSizeResetBtn.addEventListener('click', () => { applyFontSize(16); savePrefs() })
el.shortcutsToggle.addEventListener('change', savePrefs)
el.allowRebuzzToggle.addEventListener('change', () => {
  allowRebuzz = el.allowRebuzzToggle.checked
  savePrefs()
})
el.readingSpeed.addEventListener('change', savePrefs)
el.powerHighlightToggle.addEventListener('change', savePrefs)

let toastTimer = null
function toast(message) {
  el.shortcutToast.textContent = message
  el.shortcutToast.classList.remove('hidden')
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => el.shortcutToast.classList.add('hidden'), 1400)
}

loadPrefs()

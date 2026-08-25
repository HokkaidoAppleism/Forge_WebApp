/**
 * Voice Mode: reads a tossup aloud via the Web Speech API instead of
 * revealing it word by word, so practice matches a real moderator.
 *
 * Ported from `electron-app/renderer.js`, which is the desktop's whole
 * implementation of this feature -- it is pure browser API, nothing server
 * side, so there is nothing to build in `web/api/` for it. The port keeps
 * the desktop's approach unchanged and only reshapes *how* it's called:
 * the desktop reaches into its own module-level `currentWords`/`wordIndex`
 * globals directly; this file owns none of the reader's state and instead
 * takes `words` as an argument and reports position back through callbacks,
 * so `main.js` stays the one place that owns `wordIndex` -- the same
 * variable the answer's celerity, and the adaptive skill update, are
 * computed from.
 *
 * **`onboundary` drives word position, not a timer.** That isn't cosmetic:
 * celerity is `(length - wordIndex) / length` and feeds the adaptive
 * difficulty recommender, so a position that drifted from what was actually
 * spoken would quietly corrupt scoring. Some platforms (espeak on Linux,
 * most often) never fire `onboundary` at all -- `onFallback` below is what
 * that platform gets instead: the timer estimates position while the audio
 * keeps playing, rather than freezing at word 0 for the whole tossup.
 */

const VOICE_FALLBACK_MS = 1200

// Preference order, best-sounding first. Anything not installed is skipped,
// so this degrades quietly to whatever the system has: Mark is
// Windows-OneCore-only and may not be visible to Chromium at all, and macOS
// will match on its own (much better) voices further down the list.
const VOICE_PREFERENCE = ['mark', 'samantha', 'alex', 'daniel', 'david', 'zira']

export function voiceSupported() {
  return typeof window !== 'undefined'
    && !!window.speechSynthesis
    && typeof window.SpeechSynthesisUtterance === 'function'
}

// --------------------------------------------------------- voice selection --

let cachedVoice = null

function currentVoice() {
  if (!voiceSupported()) return null
  if (cachedVoice) return cachedVoice

  const all = speechSynthesis.getVoices()
  if (!all.length) return null   // not loaded yet; the listener below retries

  // English only -- a Spanish voice reading English tossups is unusable.
  const english = all.filter((v) => /^en[-_]/i.test(v.lang))
  const pool = english.length ? english : all

  for (const want of VOICE_PREFERENCE) {
    const match = pool.find((v) => v.name.toLowerCase().includes(want))
    if (match) { cachedVoice = match; return cachedVoice }
  }
  cachedVoice = pool.find((v) => v.default) || pool[0] || null
  return cachedVoice
}

// getVoices() returns [] on the first call in Chromium and fills in later.
if (voiceSupported()) {
  speechSynthesis.addEventListener('voiceschanged', () => {
    cachedVoice = null   // re-resolve against the now-populated list
    currentVoice()
  })
}

// Map the slider straight onto a speech rate, in two segments so the default
// (350) lands on a natural 1.0x with usable range either side.
export function speechRateFor(sliderValue) {
  const v = parseInt(sliderValue, 10)   // 50 (slow) .. 500 (fast)
  const rate = v <= 350
    ? 0.5 + ((v - 50) / 300) * 0.5      // 50 -> 0.5x,  350 -> 1.0x
    : 1.0 + ((v - 350) / 150) * 1.0     // 350 -> 1.0x, 500 -> 2.0x
  return Math.min(2, Math.max(0.5, rate))
}

// ----------------------------------------------------------- spoken text --

// Strip the powermark so it isn't spoken as "open paren asterisk".
// Quizbowl questions gloss hard names with a pronunciation guide in
// brackets -- "Akutagawa (ah-koo-tah-GAH-wah)". Speaking both reads the name
// twice, badly the first time. Speak only the guide and drop the word it
// explains.
//
// Kept deliberately narrow so ordinary asides -- "(1919)", "(the poet)" --
// are left alone: a guide has a hyphen, no digits, and no sentence
// punctuation.
function isPronunciationGuide(inner) {
  const s = inner.trim()
  if (!s || !s.includes('-')) return false
  if (/\d/.test(s)) return false
  return /^[A-Za-zÀ-ɏ'’\- ]+$/.test(s)
}

// The Web Speech API exposes no control over pause length, and Chromium
// doesn't support SSML, so the only lever on pacing is the punctuation fed
// to the engine. Quizbowl text is unusually heavy in the marks that cause
// the longest breaks -- quotes around titles average ~3.6 per tossup -- and
// those pauses carry no meaning when spoken aloud. Sentence periods and
// commas are left alone: they carry real structure.
function softenPunctuation(token) {
  return token
    .replace(/["“”„‟]/g, '')
    .replace(/\.{2,}/g, ',')
    .replace(/[—–]/g, ',')
    .replace(/[;:]/g, ',')
    .replace(/,{2,}/g, ',')
    .trim()
}

/** The text to speak from `startIndex` on, plus a map from each spoken
 *  word's position back to its index in `words` -- `onboundary` reports a
 *  character offset into the spoken string, and that map is what turns it
 *  back into a `words` index. */
function spokenTextFor(words, startIndex) {
  const out = []
  const indexMap = []

  const push = (text, originIndex) => {
    // Soften per token so word count is preserved -- dropping a word here
    // would skew the boundary-to-index mapping for everything after it.
    const spoken = softenPunctuation(text)
    if (!spoken || !/[A-Za-z0-9À-ɏ]/.test(spoken)) return
    out.push(spoken)
    indexMap.push(originIndex)
  }

  for (let i = startIndex; i < words.length; i++) {
    const clean = words[i].replace(/\(\*\)/g, '').trim()
    if (!clean) continue

    // Collect a bracketed group, which may span several tokens:
    // "(ah-koo-tah-GAH-wah)" or "(AH koo tah GAH wah)".
    if (clean.startsWith('(')) {
      let group = clean
      let j = i
      const closed = (s) => /^\((.+)\)([.,;:!?'"”]*)$/.exec(s)
      while (!closed(group) && j + 1 < words.length && j - i < 8) {
        j++
        group += ' ' + words[j].trim()
      }
      const m = closed(group)
      if (m) {
        const inner = m[1]
        const trailing = m[2] || ''
        if (isPronunciationGuide(inner)) {
          // Replace the word this guide explains, so the name is said once,
          // correctly, but keep that word's *index* -- celerity still has
          // to point at the right place in the original question.
          const originIndex = out.length ? indexMap[indexMap.length - 1] : i
          if (out.length) { out.pop(); indexMap.pop() }
          push(inner.trim() + trailing, originIndex)
          i = j
          continue
        }
      }
    }
    push(clean, i)
  }
  return { text: out.join(' '), indexMap }
}

/** How many spoken words are complete at this character offset, mapped back
 *  to an index into the original `words` array. */
function wordIndexForCharOffset(text, charIndex, indexMap, wordsLength) {
  const before = text.slice(0, charIndex).trim()
  const spokenCount = before ? before.split(/\s+/).length : 0
  if (spokenCount <= 0) return 0
  const mapped = indexMap[Math.min(spokenCount, indexMap.length) - 1]
  return Math.min((mapped ?? 0) + 1, wordsLength)
}

// ------------------------------------------------------------ orchestration --

let currentUtterance = null
let boundaryFired = false
let fallbackTimer = null

export function isSpeaking() {
  return !!currentUtterance
}

export function stopSpeaking() {
  clearTimeout(fallbackTimer)
  fallbackTimer = null
  if (!voiceSupported()) return
  if (currentUtterance) {
    // Drop the handlers first -- cancel() fires onend, which would otherwise
    // run the end-of-tossup path on a question that was just abandoned.
    currentUtterance.onboundary = null
    currentUtterance.onend = null
    currentUtterance.onerror = null
    currentUtterance = null
  }
  speechSynthesis.cancel()
}

export function pauseSpeaking() {
  if (voiceSupported()) speechSynthesis.pause()
}

export function resumeSpeaking() {
  if (voiceSupported()) speechSynthesis.resume()   // picks up mid-sentence
}

/**
 * Speak `words` from `startIndex`, at `rate`.
 *
 * `handlers`:
 *   onWord(wordIndex) -- called as each word starts, with its index into
 *                        `words`. The caller repaints and advances its own
 *                        `wordIndex` from this; nothing here holds that
 *                        state.
 *   onEnd()            -- the whole tossup finished speaking.
 *   onError()          -- speech died mid-tossup; the caller should fall
 *                        back to its plain word-by-word ticker so the
 *                        question is still playable.
 *   onFallback()       -- no `onboundary` event arrived within
 *                        VOICE_FALLBACK_MS, so this platform doesn't
 *                        support them. The audio keeps playing; the caller
 *                        should start estimating position with its own
 *                        ticker so celerity stays meaningful, and let
 *                        `onEnd` (not the ticker) be what closes the
 *                        tossup out.
 */
export function speak(words, startIndex, rate, { onWord, onEnd, onError, onFallback }) {
  stopSpeaking()
  boundaryFired = false

  const { text, indexMap } = spokenTextFor(words, startIndex)
  const utter = new SpeechSynthesisUtterance(text)
  utter.rate = rate
  const voice = currentVoice()
  if (voice) {
    utter.voice = voice
    utter.lang = voice.lang   // some engines ignore `voice` unless lang agrees
  }
  currentUtterance = utter

  utter.onboundary = (e) => {
    if (currentUtterance !== utter) return   // stale utterance, ignore
    if (e.name && e.name !== 'word') return
    boundaryFired = true
    clearTimeout(fallbackTimer)
    onWord(wordIndexForCharOffset(utter.text, e.charIndex, indexMap, words.length))
  }
  utter.onend = () => {
    if (currentUtterance !== utter) return
    currentUtterance = null
    onEnd()
  }
  utter.onerror = () => {
    if (currentUtterance !== utter) return
    currentUtterance = null
    onError()
  }

  speechSynthesis.speak(utter)

  fallbackTimer = setTimeout(() => {
    if (!boundaryFired && currentUtterance === utter) onFallback()
  }, VOICE_FALLBACK_MS)
}

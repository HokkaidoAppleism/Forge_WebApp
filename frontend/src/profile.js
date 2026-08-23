/**
 * The profile page: the five analysis panels, drawn.
 *
 * The desktop renders each of these as a matplotlib PNG on the server and
 * ships base64 inside a JSON body. The API here returns numbers and a written
 * finding instead (see web/api/panels.py), so the drawing is this file's job.
 *
 * Charts are hand-built SVG rather than a charting library. Five charts is not
 * enough to earn a dependency, the shapes are simple (bars, a grid, a pair of
 * lines), and inline SVG inherits the page's own colours instead of needing a
 * theme adapter to be told about them.
 *
 * **The minimum-sample gate is drawn, not just carried.** Every panel marks
 * buckets below its gate as hatched and dimmed, and says so in a legend. The
 * server already refuses to compare them or reach a verdict off them; showing
 * them identically to the solid ones would put that judgement back in the
 * reader's hands after the API had deliberately taken it out.
 */

import { api } from './api.js'

const NS = 'http://www.w3.org/2000/svg'

// Palette, matching tailwind.config.js so the charts belong to the same app.
const INK = '#efe0db'
const MUTED = '#baa7a1'
const GRID = '#584741'
const GOOD = '#4ade80'
const BAD = '#f87171'
const WARM = '#f6b17a'

// ------------------------------------------------------------ svg helpers --

function tag(name, attrs = {}, children = []) {
  const node = document.createElementNS(NS, name)
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== null && value !== undefined) node.setAttribute(key, value)
  }
  for (const child of [].concat(children)) {
    node.append(typeof child === 'string' ? document.createTextNode(child) : child)
  }
  return node
}

function text(x, y, value, attrs = {}) {
  return tag('text', {
    x, y, fill: MUTED, 'font-size': 11, 'font-family': 'inherit', ...attrs,
  }, String(value))
}

/** A chart canvas that scales to its container rather than to a pixel size. */
function canvas(width, height) {
  const svg = tag('svg', {
    viewBox: `0 0 ${width} ${height}`,
    width: '100%',
    role: 'img',
    style: 'display:block',
  })
  // The hatch every panel uses for a bucket below its gate. Defined once per
  // chart because an id has to be reachable from the shapes referencing it.
  svg.append(tag('defs', {}, [
    tag('pattern', {
      id: 'thin', width: 6, height: 6, patternUnits: 'userSpaceOnUse',
      patternTransform: 'rotate(45)',
    }, [tag('rect', { width: 3, height: 6, fill: GRID, opacity: 0.9 })]),
  ]))
  return svg
}

/** Below-gate buckets are hatched; solid ones take their real colour. */
function fillFor(reliable, colour) {
  return reliable ? colour : 'url(#thin)'
}

function legend(minSample, unit) {
  const note = document.createElement('p')
  note.className = 'mt-3 text-xs text-text-muted'
  note.textContent =
    `Hatched bars have fewer than ${minSample} ${unit} — shown, but never ` +
    `compared or used to reach the finding on the left.`
  return note
}

function emptyNote(message) {
  const p = document.createElement('p')
  p.className = 'text-sm text-text-muted'
  p.textContent = message
  return p
}

/** A plain table, for the numbers behind whichever chart is on screen. */
function table(headers, rows) {
  const wrap = document.createElement('div')
  wrap.className = 'mt-4 overflow-x-auto'
  const t = document.createElement('table')
  t.className = 'w-full text-left text-sm'
  t.innerHTML =
    `<thead><tr class="border-b border-[#584741] text-xs text-text-muted">` +
    headers.map((h) => `<th class="py-1 pr-3 font-semibold">${h}</th>`).join('') +
    `</tr></thead><tbody>` +
    rows.map((row) =>
      `<tr class="border-b border-[#3e322e]">` +
      row.map((cell) => `<td class="py-1 pr-3">${cell}</td>`).join('') +
      `</tr>`).join('') +
    `</tbody>`
  wrap.append(t)
  return wrap
}

// -------------------------------------------------------- where you buzz --

/** Points per buzz, by quarter of the tossup.
 *
 * Bars run from a zero line rather than from the left edge, because the whole
 * point of the panel is that a buzz point can be worth *negative* points and a
 * bar chart with an implicit zero at the axis hides exactly that.
 */
function drawBuzzpoints(data) {
  const frame = document.createDocumentFragment()
  if (!data.hasData) return frame.append(emptyNote(data.evaluation)), frame

  const width = 560
  const rowH = 46
  const height = data.bands.length * rowH + 40
  const svg = canvas(width, height)

  const labelW = 130
  const plotW = width - labelW - 60
  const values = data.bands.map((b) => b.perBuzz ?? 0)
  const span = Math.max(15, ...values.map(Math.abs))
  const zero = labelW + plotW / 2
  const scale = (plotW / 2) / span

  svg.append(tag('line', {
    x1: zero, y1: 8, x2: zero, y2: height - 26, stroke: GRID, 'stroke-width': 1,
  }))
  svg.append(text(zero, height - 10, '0', { 'text-anchor': 'middle' }))

  data.bands.forEach((band, i) => {
    const y = i * rowH + 12
    svg.append(text(labelW - 8, y + 18, band.label,
      { 'text-anchor': 'end', fill: INK, 'font-size': 12 }))

    if (!band.buzzes) {
      svg.append(text(zero + 8, y + 18, 'no buzzes'))
      return
    }
    const value = band.perBuzz
    const length = Math.abs(value) * scale
    svg.append(tag('rect', {
      x: value >= 0 ? zero : zero - length,
      y, width: length, height: 24, rx: 3,
      fill: fillFor(band.reliable, value >= 0 ? GOOD : BAD),
    }))
    svg.append(text(
      value >= 0 ? zero + length + 6 : zero - length - 6, y + 17,
      `${value > 0 ? '+' : ''}${value.toFixed(1)}`,
      { 'text-anchor': value >= 0 ? 'start' : 'end', fill: INK, 'font-size': 12 }))
    svg.append(text(zero + (value >= 0 ? 6 : -6), y + 17,
      `${band.buzzes} buzz${band.buzzes === 1 ? '' : 'es'} · ${band.conversion}%`,
      { 'text-anchor': value >= 0 ? 'start' : 'end', 'font-size': 10 }))
  })

  frame.append(svg, legend(data.minSample, 'buzzes'))
  frame.append(table(
    ['Buzz point', 'Buzzes', 'P / T / N', 'Points', 'Per buzz', 'Converted'],
    data.bands.map((b) => [
      b.label, b.buzzes, `${b.powers} / ${b.tens} / ${b.negs}`, b.points,
      b.perBuzz === null ? '—' : `${b.perBuzz > 0 ? '+' : ''}${b.perBuzz.toFixed(1)}`,
      b.conversion === null ? '—' : `${b.conversion}%`,
    ])))
  return frame
}

// --------------------------------------------------------------- ceiling --

/** Accuracy at each difficulty, with the 50% line the ceiling is defined by.
 *
 * Bars rather than a connected line: difficulties you have never played are
 * gaps in the data, and a line would draw straight through them as though the
 * number in between were known.
 */
function drawCeiling(data) {
  const frame = document.createDocumentFragment()
  if (!data.hasData) return frame.append(emptyNote(data.evaluation)), frame

  const width = 560
  const height = 260
  const svg = canvas(width, height)
  const left = 34
  const bottom = height - 46
  const plotW = width - left - 12
  const plotH = bottom - 20
  const barW = Math.min(46, plotW / data.levels.length - 8)

  // Gridlines at 0 / 50 / 100. The 50 is the one that means something: the
  // ceiling is the hardest level still above it.
  for (const pct of [0, 50, 100]) {
    const y = bottom - (pct / 100) * plotH
    svg.append(tag('line', {
      x1: left, y1: y, x2: width - 12, y2: y, stroke: GRID,
      'stroke-width': 1, 'stroke-dasharray': pct === 50 ? '4 3' : null,
      opacity: pct === 50 ? 1 : 0.5,
    }))
    svg.append(text(left - 6, y + 4, `${pct}%`, { 'text-anchor': 'end', 'font-size': 10 }))
  }

  data.levels.forEach((level, i) => {
    const x = left + 8 + i * (plotW / data.levels.length)
    const h = (level.accuracy / 100) * plotH
    const isWall = data.wall !== null && level.difficulty >= data.wall
    svg.append(tag('rect', {
      x, y: bottom - h, width: barW, height: Math.max(h, 1), rx: 3,
      fill: fillFor(level.reliable, isWall ? BAD : level.difficulty === data.ceiling ? GOOD : WARM),
    }))
    svg.append(text(x + barW / 2, bottom - h - 5, `${level.accuracy}%`,
      { 'text-anchor': 'middle', fill: INK, 'font-size': 10 }))
    svg.append(text(x + barW / 2, bottom + 14, level.difficulty,
      { 'text-anchor': 'middle', fill: INK, 'font-size': 11 }))
    // The tier name is what makes a difficulty mean something you can enter.
    if (level.tier) {
      svg.append(text(x + barW / 2, bottom + 28, level.tier,
        { 'text-anchor': 'middle', 'font-size': 8 }))
    }
  })

  frame.append(svg, legend(data.minSample, 'answers'))
  frame.append(table(
    ['Difficulty', 'Tier', 'Answers', 'Correct', 'Accuracy', 'Per question'],
    data.levels.map((l) => [
      l.difficulty, l.tier ?? '—', l.attempts, l.correct, `${l.accuracy}%`,
      `${l.perQuestion > 0 ? '+' : ''}${l.perQuestion.toFixed(1)}`,
    ])))
  return frame
}

// ----------------------------------------------------------- neg autopsy --

/** Neg rate as a difficulty by buzz-point grid.
 *
 * A heatmap of *rates*, never counts. Raw neg counts follow wherever you
 * happen to buzz most, so 12 negs out of 12 and 12 out of 40 look identical in
 * a tally and are nothing alike -- which is the mistake this panel exists to
 * stop the outcome pie making.
 */
function drawNegAutopsy(data) {
  const frame = document.createDocumentFragment()
  if (!data.hasData) return frame.append(emptyNote(data.evaluation)), frame

  const quarters = data.byQuarter.map((q) => q.label)
  const difficulties = data.byDifficulty.map((d) => d.difficulty)
  const cell = 54
  const left = 108
  const top = 30
  const width = left + difficulties.length * cell + 16
  const height = top + quarters.length * cell + 40
  const svg = canvas(Math.max(width, 360), height)

  difficulties.forEach((d, x) => {
    svg.append(text(left + x * cell + cell / 2, top - 10, d,
      { 'text-anchor': 'middle', fill: INK, 'font-size': 11 }))
  })

  const byCell = new Map(data.grid.map((g) => [`${g.difficulty}|${g.quarter}`, g]))

  quarters.forEach((q, y) => {
    svg.append(text(left - 8, top + y * cell + cell / 2 + 4, q,
      { 'text-anchor': 'end', fill: INK, 'font-size': 11 }))

    difficulties.forEach((d, x) => {
      const found = byCell.get(`${d}|${q}`)
      const px = left + x * cell
      const py = top + y * cell
      if (!found) {
        svg.append(tag('rect', {
          x: px + 2, y: py + 2, width: cell - 4, height: cell - 4, rx: 3,
          fill: GRID, opacity: 0.25,
        }))
        return
      }
      // Rate drives the colour; a below-gate cell is hatched so a 100% built
      // on two buzzes cannot read as the hottest thing on the grid.
      svg.append(tag('rect', {
        x: px + 2, y: py + 2, width: cell - 4, height: cell - 4, rx: 3,
        fill: found.reliable ? BAD : 'url(#thin)',
        opacity: found.reliable ? 0.25 + 0.75 * (found.negRate / 100) : 1,
      }))
      svg.append(text(px + cell / 2, py + cell / 2, `${found.negRate}%`,
        { 'text-anchor': 'middle', fill: INK, 'font-size': 12, 'font-weight': 700 }))
      svg.append(text(px + cell / 2, py + cell / 2 + 14, `${found.negs}/${found.buzzes}`,
        { 'text-anchor': 'middle', 'font-size': 9 }))
    })
  })

  svg.append(text(left, height - 10, 'Difficulty across, buzz point down · darker is a higher neg rate',
    { 'font-size': 10 }))

  frame.append(svg, legend(data.minSample, 'buzzes'))
  frame.append(table(
    ['Buzz point', 'Buzzes', 'Negs', 'Neg rate'],
    data.byQuarter.map((q) => [
      q.label, q.buzzes, q.negs, q.negRate === null ? '—' : `${q.negRate}%`])))
  frame.append(table(
    ['Difficulty', 'Buzzes', 'Negs', 'Neg rate'],
    data.byDifficulty.map((d) => [
      d.difficulty, d.buzzes, d.negs, d.negRate === null ? '—' : `${d.negRate}%`])))
  return frame
}

// ------------------------------------------------------------- retention --

/** SM-2 easiness per subject, drawn against the 2.5 starting line.
 *
 * The line is what makes the chart readable at all: easiness only means
 * anything relative to where every question starts, so "below the line" is the
 * finding and an absolute height is not.
 */
function drawRetention(data) {
  const frame = document.createDocumentFragment()
  if (!data.subjects.length) return frame.append(emptyNote(data.summary)), frame

  const width = 560
  const rowH = 34
  const height = data.subjects.length * rowH + 40
  const svg = canvas(width, height)

  const labelW = 150
  const plotW = width - labelW - 70
  // 1.3 is SM-2's floor and easiness rarely climbs past 3.0, so the axis is
  // fixed to that range instead of to the data -- a subject at 2.4 should not
  // fill the chart just because it happens to be the lowest one today.
  const lo = 1.3
  const hi = 3.0
  const at = (ef) => labelW + ((Math.min(hi, Math.max(lo, ef)) - lo) / (hi - lo)) * plotW

  const start = at(2.5)
  svg.append(tag('line', {
    x1: start, y1: 6, x2: start, y2: height - 26, stroke: WARM,
    'stroke-width': 1, 'stroke-dasharray': '4 3',
  }))
  svg.append(text(start, height - 10, '2.5 — where every question starts',
    { 'text-anchor': 'middle', 'font-size': 10, fill: WARM }))

  data.subjects.forEach((subject, i) => {
    const y = i * rowH + 10
    svg.append(text(labelW - 8, y + 15, subject.category,
      { 'text-anchor': 'end', fill: INK, 'font-size': 12 }))
    const x = at(subject.ef)
    const below = subject.ef < 2.5
    svg.append(tag('rect', {
      x: below ? x : start, y, width: Math.max(Math.abs(x - start), 2), height: 20, rx: 3,
      fill: fillFor(subject.reliable, below ? BAD : GOOD),
    }))
    svg.append(text(width - 60, y + 15, subject.ef.toFixed(2),
      { fill: INK, 'font-size': 12 }))
  })

  frame.append(svg, legend(data.minSample, 'reviewed questions'))
  frame.append(table(
    ['Subject', 'Reviewed', 'Easiness', 'Attempts', 'Correct', 'Avg interval'],
    data.subjects.map((s) => [
      s.category, s.reviewed, s.ef.toFixed(2), s.attempts, s.correct,
      `${s.intervalDays} d`])))
  return frame
}

// -------------------------------------------------------- knowledge depth --

/** Per-cluster skill within each subject played, weakest cluster first.
 *
 * Every other panel compares *across* subjects; this is the only one that
 * looks inside one. Skill shares difficulty's 0-10 scale (see
 * web/api/adaptive.py's UserModel), so the axis is fixed the same way
 * Retention's is fixed to easiness -- a subject whose worst topic is a 6
 * should not fill the chart just because 6 is the lowest number on screen
 * today.
 */
function drawKnowledgeDepth(data) {
  const frame = document.createDocumentFragment()
  if (!data.hasData) return frame.append(emptyNote(data.evaluation)), frame

  if (!data.namedByAi) {
    const note = document.createElement('p')
    note.className = 'mb-3 text-xs text-text-muted'
    note.textContent = 'Topic names below are drawn from example answers, ' +
      'not generated — add a Gemini key in Settings for real topic names.'
    frame.append(note)
  }

  const width = 620
  const labelW = 190
  const rowH = 26
  const headerH = 22
  const totalRows = data.subjects.reduce((n, s) => n + s.clusters.length, 0)
  const height = data.subjects.length * headerH + totalRows * rowH + 10
  const svg = canvas(width, height)

  const plotW = width - labelW - 60
  const lo = 0, hi = 10
  const at = (skill) => labelW + (Math.min(hi, Math.max(lo, skill)) / hi) * plotW
  const colourFor = (skill) => skill < 4 ? BAD : skill >= 7 ? GOOD : WARM

  let y = 4
  for (const subject of data.subjects) {
    svg.append(text(4, y + 14, `${subject.subcategory} — average ${subject.average}`,
      { fill: INK, 'font-size': 12, 'font-weight': 'bold' }))
    y += headerH

    for (const cluster of subject.clusters) {
      const x = at(cluster.skill)
      svg.append(tag('rect', {
        x: labelW, y, width: Math.max(x - labelW, 2), height: rowH - 8, rx: 3,
        fill: colourFor(cluster.skill),
      }))
      svg.append(text(labelW - 8, y + (rowH - 8) / 2 + 4, cluster.label,
        { 'text-anchor': 'end', fill: MUTED, 'font-size': 11 }))
      svg.append(text(x + 6, y + (rowH - 8) / 2 + 4, cluster.skill.toFixed(1),
        { fill: INK, 'font-size': 11 }))
      y += rowH
    }
  }

  frame.append(svg)
  frame.append(table(
    ['Subject', 'Topic', 'Skill'],
    data.subjects.flatMap((s) => s.clusters.map((c) =>
      [s.subcategory, escapeHtmlLocal(c.label), c.skill.toFixed(1)]))))
  return frame
}

/** The one place this file puts free text (an AI-generated topic name, or a
 *  fallback drawn from real answerlines) into innerHTML rather than a text
 *  node -- table() below builds rows as HTML strings, same as every other
 *  panel's table, but those only ever carry category names and numbers this
 *  server already controls. A cluster label is the first value in this file
 *  that either came out of a model or is assembled from question data at
 *  request time, so it gets escaped on the way in rather than trusted. */
function escapeHtmlLocal(text) {
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
}

// -------------------------------------------------------------- progress --

/** One calendar month of accuracy and buzz point.
 *
 * Days with no play are drawn as gaps, not as zeroes: the API sends them with
 * a null accuracy for exactly this reason, and joining across them would draw
 * a fortnight away as a slide down to nothing.
 */
function drawProgress(data) {
  const frame = document.createDocumentFragment()
  if (!data.hasData) return frame.append(emptyNote(data.evaluation)), frame

  const width = 720
  const height = 240
  const svg = canvas(width, height)
  const left = 34
  const right = width - 40
  const bottom = height - 30
  const plotW = right - left
  const plotH = bottom - 18
  const step = plotW / Math.max(1, data.days.length - 1)

  for (const pct of [0, 50, 100]) {
    const y = bottom - (pct / 100) * plotH
    svg.append(tag('line', { x1: left, y1: y, x2: right, y2: y, stroke: GRID, opacity: 0.5 }))
    svg.append(text(left - 6, y + 4, `${pct}%`, { 'text-anchor': 'end', 'font-size': 10 }))
  }

  const played = data.days.filter((d) => d.played)

  // Accuracy, as a run of segments broken wherever a day was missed.
  let previous = null
  for (const day of data.days) {
    const x = left + (day.dayOfMonth - 1) * step
    if (day.accuracy === null) { previous = null; continue }
    const y = bottom - (day.accuracy / 100) * plotH
    if (previous) {
      svg.append(tag('line', {
        x1: previous.x, y1: previous.y, x2: x, y2: y, stroke: GOOD, 'stroke-width': 2,
      }))
    }
    // A day under the gate is drawn hollow: it happened, and its accuracy is
    // not steady enough to read a direction from.
    svg.append(tag('circle', {
      cx: x, cy: y, r: 3.5,
      fill: day.reliable ? GOOD : '#2c2321', stroke: GOOD, 'stroke-width': 1.5,
    }))
    previous = { x, y }
  }

  // Celerity on the same axis, scaled 0-1 to the same height. It shares the
  // frame on purpose: buzzing earlier while converting less is a shape only
  // visible when the two are drawn against each other.
  previous = null
  for (const day of data.days) {
    const x = left + (day.dayOfMonth - 1) * step
    if (day.celerity === null) { previous = null; continue }
    const y = bottom - day.celerity * plotH
    if (previous) {
      svg.append(tag('line', {
        x1: previous.x, y1: previous.y, x2: x, y2: y, stroke: WARM,
        'stroke-width': 1.5, 'stroke-dasharray': '3 2',
      }))
    }
    previous = { x, y }
  }

  for (const day of data.days) {
    if (day.dayOfMonth % 5 !== 0 && day.dayOfMonth !== 1) continue
    svg.append(text(left + (day.dayOfMonth - 1) * step, bottom + 16, day.dayOfMonth,
      { 'text-anchor': 'middle', 'font-size': 10 }))
  }

  svg.append(text(left, 12, 'Accuracy', { fill: GOOD, 'font-size': 10 }))
  svg.append(text(left + 60, 12, 'Buzz point (fraction unread)', { fill: WARM, 'font-size': 10 }))

  const note = document.createElement('p')
  note.className = 'mt-3 text-xs text-text-muted'
  note.textContent =
    `${data.monthLabel}: ${data.monthDaysPlayed} day${data.monthDaysPlayed === 1 ? '' : 's'} ` +
    `played, ${data.monthAnswers} answers. Hollow points are days under ` +
    `${data.minSample} answers — shown, but not compared.`

  const finding = document.createElement('p')
  finding.className = 'mt-2 text-sm leading-relaxed'
  finding.textContent = data.evaluation

  frame.append(svg, note, finding)
  if (played.length) {
    frame.append(table(
      ['Day', 'Answers', 'Correct', 'Negs', 'Accuracy', 'Buzz point', 'Points'],
      played.map((d) => [
        d.date, d.answers, d.correct, d.negs, `${d.accuracy}%`,
        d.celerity === null ? '—' : d.celerity.toFixed(3), d.points])))
  }
  return frame
}

// ----------------------------------------------------------------- panels --

// `what` is the one-line description of the stat itself; the finding comes
// from the server, because it is the part that depends on the player.
// `perSession` marks the panels a session filter can actually reach. The
// three built on `user_stats` can: every row of it carries the sitting it came
// from. Retention cannot, and not for want of a column -- it reads the review
// queue, and a question you are still relearning outlives the sitting that put
// it there. Offering it under a session filter and quietly answering from the
// whole account is the version of this that looks like it works.
const PANELS = [
  {
    key: 'buzzpoints', label: 'Where You Buzz', perSession: true,
    what: 'What a buzz is actually worth in each quarter of the tossup, in real points.',
    load: (category, session) => api.buzzpoints(category, session),
    draw: drawBuzzpoints,
    finding: (d) => d.evaluation,
  },
  {
    key: 'ceiling', label: 'Ceiling', perSession: true,
    what: 'How well you convert at each difficulty, and the tournaments those difficulties are.',
    load: (category, session) => api.ceiling(category, session),
    draw: drawCeiling,
    finding: (d) => d.evaluation,
  },
  {
    key: 'negs', label: 'Neg Autopsy', perSession: true,
    what: 'Whether your negs track when you buzz or what you buzzed on — the two want opposite fixes.',
    load: (category, session) => api.negAutopsy(category, session),
    draw: drawNegAutopsy,
    finding: (d) => d.evaluation,
  },
  {
    key: 'retention', label: 'Retention', perSession: false,
    what: 'Whether what you get right stays right, per subject, from the review schedule.',
    load: (category) => api.retention(category),
    draw: drawRetention,
    finding: (d) => d.summary,
  },
  {
    key: 'knowledgeDepth', label: 'Knowledge Depth', perSession: false,
    what: 'The recommender\'s own per-cluster skill model, made readable — where inside a subject you\'re actually thin.',
    load: (category) => api.knowledgeDepth(category),
    draw: drawKnowledgeDepth,
    finding: (d) => d.evaluation,
  },
]

// ------------------------------------------------------------------ wiring --

export function initProfile(el) {
  let current = PANELS[0].key
  let month = null
  // The saved Adaptive Learning sitting this page is scoped to, or null for
  // the whole account. Set only by the records page.
  let session = null

  const visiblePanels = () =>
    session ? PANELS.filter((p) => p.perSession) : PANELS

  function pickerButtons() {
    el.statPicker.innerHTML = ''
    for (const panel of visiblePanels()) {
      const button = document.createElement('button')
      button.textContent = panel.label
      button.className = 'rounded-full px-4 py-2 text-sm font-bold ' +
        (panel.key === current ? 'bg-[#efe0db] text-[#1d1816]' : 'bg-tertiary-dark')
      button.addEventListener('click', () => {
        current = panel.key
        pickerButtons()
        loadPanel()
      })
      el.statPicker.append(button)
    }
  }

  async function loadPanel() {
    const panel = PANELS.find((p) => p.key === current)
    const category = session ? '' : el.profileCategoryFilter.value
    el.statView.textContent = 'Loading…'
    el.statAboutTitle.textContent = panel.label
    el.statAboutWhat.textContent = panel.what
    el.statAboutFinding.textContent = ''
    try {
      const data = await panel.load(category, session?.sessionId)
      el.statView.textContent = ''
      el.statView.append(panel.draw(data))
      el.statAboutFinding.textContent = panel.finding(data)
    } catch (error) {
      el.statView.textContent = error.message
    }
  }

  async function loadProgress() {
    const category = el.profileCategoryFilter.value
    el.progressView.textContent = 'Loading…'
    try {
      const data = await api.progress(category, month)
      el.progressView.textContent = ''
      el.progressView.append(drawProgress(data))
      renderMonthNav(data)
    } catch (error) {
      el.progressView.textContent = error.message
    }
  }

  /** Month paging. Only months with play in them are offered -- stepping
   *  through empty months looking for data is not navigation. */
  function renderMonthNav(data) {
    el.progressNav.innerHTML = ''
    if (!data.months?.length) return
    const index = data.months.findIndex((m) => m.key === data.month)

    const step = (delta, label) => {
      const target = data.months[index + delta]
      const button = document.createElement('button')
      button.textContent = label
      button.disabled = !target
      button.className = 'rounded-full bg-tertiary-dark px-3 py-1 text-sm font-bold disabled:opacity-30'
      button.addEventListener('click', () => { month = target.key; loadProgress() })
      return button
    }

    const label = document.createElement('span')
    label.className = 'text-sm font-bold'
    label.textContent = data.monthLabel

    el.progressNav.append(step(-1, '← Earlier'), label, step(1, 'Later →'))
  }

  async function loadLifetime() {
    const category = session ? '' : el.profileCategoryFilter.value
    const { lifetime } = await api.stats(category, session?.sessionId)
    el.profileTossupsHeard.textContent = lifetime.tossups
    el.profilePoints.textContent = lifetime.points
    el.profilePowers.textContent = lifetime.powers
    el.profileTens.textContent = lifetime.tens
    el.profileNegs.textContent = lifetime.negs
    el.profileCelerity.textContent = lifetime.averageCelerity === null
      ? '0.000' : lifetime.averageCelerity.toFixed(3)
  }

  el.profileCategoryFilter.addEventListener('change', () => {
    // A category change resets the month: the month that had play in it for
    // Science may have none for Literature, and holding a stale key would
    // silently fall back while the nav still read like a choice.
    month = null
    loadLifetime()
    loadPanel()
    loadProgress()
  })

  /** What a session filter takes off the page, and why it says so.
   *
   *  Under a session filter the category picker is meaningless (the sitting
   *  already is one subject), Progress Over Time cannot be answered at all
   *  (`progress_daily` has no session column, because a day outlives the
   *  sitting), and Retention is answered from the review queue rather than
   *  from answers. Hiding them silently would leave the reader to work out
   *  why the page changed shape, so the banner says it. */
  function applyScope() {
    const scoped = Boolean(session)
    el.profileTitle.textContent = scoped ? 'One Saved Session' : 'Your Profile'
    el.profileCategoryLabel.classList.toggle('hidden', scoped)
    // Resetting the account's lifetime stats is not an action that makes
    // sense while looking at one saved sitting.
    el.resetStatsBtn.classList.toggle('hidden', scoped)
    el.progressSection.classList.toggle('hidden', scoped)
    el.profileSessionNotice.classList.toggle('hidden', !scoped)
    el.profileSessionNotice.classList.toggle('flex', scoped)

    if (scoped) {
      const played = session.endedAt ? new Date(session.endedAt).toLocaleString() : ''
      el.profileSessionText.textContent =
        `${session.category}${played ? `, ${played}` : ''} — ` +
        `${session.correctAnswers ?? 0} of ${session.questionsAnswered ?? 0} correct. ` +
        'Retention and Progress Over Time are not shown: they come from your ' +
        'review queue and your practice calendar, which outlive any one sitting.'
    }

    // A panel that a session cannot answer must not stay selected when one is
    // applied, or the picker would show no button pressed and load nothing.
    if (!visiblePanels().some((p) => p.key === current)) current = PANELS[0].key
  }

  el.profileExitSessionBtn.addEventListener('click', () => {
    session = null
    month = null
    applyScope()
    pickerButtons()
    loadLifetime()
    loadPanel()
    loadProgress()
  })

  /** Called every time the page is opened, so it never shows stale numbers
   *  from before the last session's answers. `scope` is a saved Adaptive
   *  Learning record when the records page opened it, and null otherwise. */
  return function showProfile(categories, scope = null) {
    session = scope
    if (session) month = null

    const chosen = el.profileCategoryFilter.value
    el.profileCategoryFilter.innerHTML = '<option value="">Every category</option>'
    for (const category of categories) {
      const option = document.createElement('option')
      option.value = category.category
      option.textContent = category.category
      el.profileCategoryFilter.append(option)
    }
    el.profileCategoryFilter.value = chosen

    applyScope()
    pickerButtons()
    loadLifetime()
    loadPanel()
    if (!session) loadProgress()
  }
}

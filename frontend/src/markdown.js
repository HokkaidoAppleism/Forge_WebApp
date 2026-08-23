/**
 * A deliberately small markdown renderer, for study guides only.
 *
 * The desktop vendors showdown for this. Here the markdown is not arbitrary:
 * it is what notebook.py's `build_note_sections` writes -- an H1 title, H2
 * answerline headings, `---` rules between sections, and whatever the note
 * itself contained (bold, italics, lists, paragraphs). That is a small enough
 * grammar to render directly.
 *
 * **The escaping happens first, and that is the whole security argument.**
 * Note content is written by the user and goes into innerHTML. Every `&`, `<`
 * and `>` is replaced before a single rule runs, so the only tags in the
 * output are the ones this file puts there -- a note containing `<script>`
 * renders as those literal characters. Sanitising afterwards would mean
 * parsing hostile HTML and hoping; this cannot produce hostile HTML to begin
 * with. It is also why no sanitiser dependency is needed.
 */

const escapeHtml = (text) => String(text ?? '').replace(
  /[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))

/** Inline runs: bold, italic, code. Escapes its input itself.
 *
 * Escaping happens here, per run of text, rather than once over the whole
 * source up front. The block matchers below (the blockquote marker chief
 * among them) key off literal `>`, `#`, `-` characters in the *raw* markdown
 * -- escaping the whole source first turns `>` into `&gt;` before those
 * regexes ever see it, and a blockquote silently stops being one. */
function inline(text) {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+?)\*(?!\*)/g, '$1<em>$2</em>')
    .replace(/`([^`\n]+?)`/g, '<code class="rounded bg-[#1d1816] px-1 py-0.5 text-xs">$1</code>')
}

export function renderMarkdown(source) {
  const lines = String(source ?? '').split('\n')
  const out = []
  let paragraph = []
  let list = null          // 'ul' | 'ol' | null

  const closeParagraph = () => {
    if (paragraph.length) {
      out.push(`<p>${inline(paragraph.join(' '))}</p>`)
      paragraph = []
    }
  }
  const closeList = () => {
    if (list) { out.push(`</${list}>`); list = null }
  }
  const closeAll = () => { closeParagraph(); closeList() }

  for (const raw of lines) {
    const line = raw.trim()

    if (!line) { closeAll(); continue }

    // A rule separates one answerline's section from the next, and carries the
    // visual separation in a guide -- so it is not decoration to skip.
    if (/^(---+|\*\*\*+|___+)$/.test(line)) { closeAll(); out.push('<hr>'); continue }

    const heading = line.match(/^(#{1,4})\s+(.*)$/)
    if (heading) {
      closeAll()
      out.push(`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`)
      continue
    }

    const bullet = line.match(/^[-*+]\s+(.*)$/)
    if (bullet) {
      closeParagraph()
      if (list !== 'ul') { closeList(); out.push('<ul>'); list = 'ul' }
      out.push(`<li>${inline(bullet[1])}</li>`)
      continue
    }

    const numbered = line.match(/^\d+[.)]\s+(.*)$/)
    if (numbered) {
      closeParagraph()
      if (list !== 'ol') { closeList(); out.push('<ol>'); list = 'ol' }
      out.push(`<li>${inline(numbered[1])}</li>`)
      continue
    }

    const quote = line.match(/^>\s?(.*)$/)
    if (quote) {
      closeAll()
      out.push(`<blockquote>${inline(quote[1])}</blockquote>`)
      continue
    }

    closeList()
    paragraph.push(line)
  }

  closeAll()
  return out.join('\n')
}

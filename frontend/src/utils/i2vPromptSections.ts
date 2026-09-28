/**
 * Locate the four regions of a MiniMax I2VA prompt document so the dialog
 * can show them as separate panels.
 *
 * This is a VIEW, not a representation change. The document stays the one
 * canonical value everywhere it already was — the form's `prompt`,
 * `form_state`, `i2v_videos.prompt_used`, the lint, and what Generate
 * sends. Nothing here reformats it: `replaceI2vSection` splices the one
 * located span and copies every other byte through, so editing one panel
 * cannot disturb another.
 *
 * The markers mirror `metascan/core/i2v_compiler.py`: the anchored
 * first-shot opener sentence (present on the single-take path and the
 * découpage-template path alike — a template's `look` sentence is spliced
 * in *before* it, which is why the header is derived rather than assumed
 * constant) and the three field labels.
 *
 * `parseI2vPrompt` validates its own spans: the separators between them
 * must be exactly the document's, the spans must be ordered and cover the
 * text to its end, and each label must occur once. Anything else — a
 * hand-mangled prompt, a clip from before the panels existed, a future
 * format change, a pasted field label — returns null, and the dialog falls
 * back to editing the document as one block. A parse that cannot prove
 * itself degrades the UI instead of corrupting text.
 */

/** The `_OPENING` sentence minus its `[Shot 1] ` prefix (i2v_compiler.py). */
const OPENER =
  'The subjects, composition, and setting shown in <Picture 1> are established ' +
  'at 0.00 seconds and keep their appearance, clothing, colors, and spatial ' +
  'relationships.'

const DESCRIPTION_LABEL = 'integrated_multimodal_description:'
const SOUNDSCAPE_LABEL = 'overall_soundscape:'
const MUSIC_LABEL = 'non_diegetic_music:'

/** The editable panels. `header` is read-only, so it is not a key here. */
export type I2vSectionKey = 'prompt' | 'soundscape' | 'music'

interface Span {
  start: number
  end: number
}

export interface I2vPromptSections {
  /** Read-only: the alignment line through the anchored opener sentence. */
  header: string
  prompt: string
  soundscape: string
  music: string
  spans: Record<I2vSectionKey, Span>
}

function onlyIndexOf(text: string, needle: string): number {
  const first = text.indexOf(needle)
  return first >= 0 && first === text.lastIndexOf(needle) ? first : -1
}

export function parseI2vPrompt(text: string): I2vPromptSections | null {
  const descIdx = onlyIndexOf(text, DESCRIPTION_LABEL)
  const openerIdx = onlyIndexOf(text, OPENER)
  const soundIdx = onlyIndexOf(text, `\n\n${SOUNDSCAPE_LABEL}`)
  const musicIdx = onlyIndexOf(text, `\n\n${MUSIC_LABEL}`)
  if (descIdx < 0 || openerIdx < descIdx || soundIdx < 0 || musicIdx < soundIdx) return null

  const headerEnd = openerIdx + OPENER.length
  if (headerEnd > soundIdx) return null

  // One space joins the opener to the first body sentence; a document whose
  // description ends at the opener has none.
  const afterHeader = text[headerEnd] === ' ' ? headerEnd + 1 : headerEnd
  const spans: Record<I2vSectionKey, Span> = {
    prompt: { start: afterHeader, end: soundIdx },
    soundscape: { start: labelBodyStart(text, soundIdx, SOUNDSCAPE_LABEL), end: musicIdx },
    music: { start: labelBodyStart(text, musicIdx, MUSIC_LABEL), end: text.length },
  }

  // The separators between the spans must be exactly the document's, and
  // the spans must run in order to the end of the text. Without this a
  // near-miss (a reordered or duplicated field) would parse into spans
  // that silently move text between panels.
  const ok =
    text.slice(0, headerEnd).includes(DESCRIPTION_LABEL) &&
    text.slice(headerEnd, afterHeader).trim() === '' &&
    spans.prompt.start <= spans.prompt.end &&
    text.slice(spans.prompt.end, spans.soundscape.start) ===
      `\n\n${SOUNDSCAPE_LABEL}${separator(text, soundIdx, SOUNDSCAPE_LABEL)}` &&
    spans.soundscape.start <= spans.soundscape.end &&
    text.slice(spans.soundscape.end, spans.music.start) ===
      `\n\n${MUSIC_LABEL}${separator(text, musicIdx, MUSIC_LABEL)}` &&
    spans.music.start <= spans.music.end
  if (!ok) return null

  return {
    header: text.slice(0, headerEnd),
    prompt: text.slice(spans.prompt.start, spans.prompt.end),
    soundscape: text.slice(spans.soundscape.start, spans.soundscape.end),
    music: text.slice(spans.music.start, spans.music.end),
    spans,
  }
}

/** `"overall_soundscape:"` is followed by one space in a written document. */
function separator(text: string, labelIdx: number, label: string): string {
  return text[labelIdx + 2 + label.length] === ' ' ? ' ' : ''
}

function labelBodyStart(text: string, labelIdx: number, label: string): number {
  return labelIdx + 2 + label.length + separator(text, labelIdx, label).length
}

/**
 * The document with one panel's text replaced. Every byte outside that
 * panel's span is copied through unchanged; an unparseable document is
 * returned as-is (the dialog is editing it as one block in that case).
 */
export function replaceI2vSection(text: string, key: I2vSectionKey, value: string): string {
  const parsed = parseI2vPrompt(text)
  if (!parsed) return text
  const { start, end } = parsed.spans[key]
  return text.slice(0, start) + value + text.slice(end)
}

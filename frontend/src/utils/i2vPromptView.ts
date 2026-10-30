/**
 * Which view the I2V prompt editor shows. Pure, so the rules are testable:
 * the view is a function of the text, the remembered choice and `locked`,
 * and never writes the prompt.
 */
import { parseI2vPrompt } from './i2vPromptSections'

export type I2vPromptView = 'raw' | 'structured'

export interface I2vPromptViewState {
  view: I2vPromptView
  structuredEnabled: boolean
  note: string | null
}

export const UNSTRUCTURED_NOTE =
  "This prompt can't be shown as structured: the H3 field labels were changed or removed. " +
  'It will still generate as written.'

/**
 * `locked` is set by the editor when the document stops parsing while
 * STRUCTURED is preferred; it keeps the view on RAW after the labels are
 * fixed, so a fix made while typing does not flip the view under the
 * cursor. Pressing STRUCTURED (or a clip load) clears it.
 */
export function promptViewState(
  text: string,
  preferred: I2vPromptView,
  locked: boolean,
): I2vPromptViewState {
  // An empty prompt has no structure to split, and nothing to warn about.
  if (!text) return { view: 'raw', structuredEnabled: false, note: null }
  if (!parseI2vPrompt(text)) {
    return { view: 'raw', structuredEnabled: false, note: UNSTRUCTURED_NOTE }
  }
  const view = preferred === 'structured' && !locked ? 'structured' : 'raw'
  return { view, structuredEnabled: true, note: null }
}

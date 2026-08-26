/**
 * Stackable LoRA list for a shot's image or video generation: a name field
 * backed by a datalist of the ComfyUI LoRA folder, a 72px strength number,
 * and a bare remove X. New rows start as a local draft and only enter the
 * committed list once they have a name.
 */
export interface LoraEntry {
  name: string;
  strength: number;
}

export interface LoraListEditorProps {
  /** Uppercase eyebrow label, e.g. "Image LoRAs" / "Video LoRAs". */
  label: string;
  entries?: LoraEntry[];
  /** Autocomplete options fetched once per page load from ComfyUI. */
  options?: string[];
  /** Fires with the complete next list on every commit. */
  onChange?: (entries: LoraEntry[]) => void;
  /** Unique datalist id when several editors share a page. */
  listId?: string;
}
export declare function LoraListEditor(props: LoraListEditorProps): JSX.Element;

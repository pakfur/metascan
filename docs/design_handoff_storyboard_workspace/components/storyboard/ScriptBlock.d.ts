import type { CSSProperties, ReactNode } from 'react';

/**
 * Monospace read-only pane for generated text: the shot script (a header plus
 * one selectable block per beat) or a plain prompt dump. Sits on
 * --surface-ground with a hairline border, pre-wrap, capped at 45vh.
 */
export interface ScriptBeatBlock {
  beatId: number;
  text: string;
}

export interface ScriptBlockProps {
  /** Shot-level preamble line(s), rendered in secondary text. */
  header?: ReactNode;
  /** One selectable block per beat; clicking one selects that beat. */
  beats?: ScriptBeatBlock[];
  /** Plain-text mode (image prompt, compiled video prompt) — renders a pre instead. */
  text?: string | null;
  selectedBeatId?: number | null;
  onSelectBeat?: (beatId: number) => void;
  className?: string;
  style?: CSSProperties;
}
export declare function ScriptBlock(props: ScriptBlockProps): JSX.Element;

import type { CSSProperties, MouseEventHandler } from 'react';

/**
 * A rendered clip in the shot's "Video takes" strip: 128x72 landscape thumb
 * with a play glyph over a 25% scrim on hover and a scrim delete button.
 * Clips are shot-scoped, so this strip lives on the shot pane, not on a beat.
 */
export interface VideoTakeTileProps {
  src: string;
  /** The product's tooltip format: "seed 481314106 · take 2". */
  title?: string;
  onPlay?: MouseEventHandler<HTMLElement>;
  /** Deleting a take is total (row, media record, file to trash) — always confirm first. */
  onDelete?: MouseEventHandler<HTMLButtonElement>;
  className?: string;
  style?: CSSProperties;
}
export declare function VideoTakeTile(props: VideoTakeTileProps): JSX.Element;

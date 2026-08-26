import type { CSSProperties, MouseEventHandler } from 'react';

/**
 * One generated variant in a beat's candidate row: a 96px square, 2px
 * transparent border that turns primary when it is the keeper, a primary
 * check disc top-left, and a scrim zoom button revealed on hover.
 */
export interface CandidateTileProps {
  src: string;
  /** This variant is the beat's keeper (selected_image_id). */
  selected?: boolean;
  /** The product's tooltip format: "seed 481314106 · variant 2". */
  title?: string;
  /** Click toggles keeper selection; clicking the current keeper clears it. */
  onClick?: MouseEventHandler<HTMLButtonElement>;
  /** Double-click opens the media viewer over the whole candidate row. */
  onDoubleClick?: MouseEventHandler<HTMLButtonElement>;
  /** Renders the hover zoom affordance. */
  onExpand?: MouseEventHandler<HTMLSpanElement>;
  className?: string;
  style?: CSSProperties;
}
export declare function CandidateTile(props: CandidateTileProps): JSX.Element;

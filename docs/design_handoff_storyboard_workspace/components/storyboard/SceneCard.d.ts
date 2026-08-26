import type { CSSProperties, MouseEventHandler, ReactNode } from 'react';

/**
 * A scene in the horizontal scene strip: 190px fixed card, name, subtitle,
 * optional italic setting clamped to two lines, and up to six 24px keeper
 * thumbnails. Corner actions (render / edit / delete) fade in on hover.
 */
export interface SceneCardProps {
  name: string;
  /** Falls back to location + time-of-day, then an em dash. */
  subtitle?: string;
  /** The scene's setting sentence, rendered italic and clamped to 2 lines. */
  setting?: string | null;
  /** Keeper thumbnail URLs for the scene's first six panels; null entries render the dashed empty box. */
  thumbs?: Array<string | null>;
  active?: boolean;
  /** IconButton variant="corner" elements. */
  actions?: ReactNode;
  onClick?: MouseEventHandler<HTMLDivElement>;
  className?: string;
  style?: CSSProperties;
}
export declare function SceneCard(props: SceneCardProps): JSX.Element;

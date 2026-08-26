import type { CSSProperties, MouseEventHandler, ReactNode } from 'react';

/**
 * A shot in the panel grid: a square keeper thumbnail with an 11px caption
 * beneath (shot size + subject names). Lives in a
 * repeat(auto-fill, minmax(150px, 1fr)) grid with a 14px gap.
 */
export interface PanelTileProps {
  /** Keeper thumbnail; omit for the dashed empty box. */
  src?: string | null;
  /** 45% opacity — used when no beat keeper has been picked yet. */
  dimmed?: boolean;
  /** Single ellipsized line, e.g. "MCU · Mara, Ines". */
  caption?: ReactNode;
  active?: boolean;
  /** Shows the lock glyph top-left when the first beat's prompt is locked. */
  locked?: boolean;
  /** Shows the clapperboard glyph bottom-left when rendered clips exist. */
  videoCount?: number;
  /** A JobBadge element for the current generation state. */
  job?: ReactNode;
  onClick?: MouseEventHandler<HTMLDivElement>;
  /** Double-click opens the media viewer over this shot's clips or candidates. */
  onDoubleClick?: MouseEventHandler<HTMLDivElement>;
  /** Renders the scrim delete button, revealed on hover. */
  onDelete?: MouseEventHandler<HTMLButtonElement>;
  className?: string;
  style?: CSSProperties;
  /** Replaces the caption row entirely (used by the inline add-panel form). */
  children?: ReactNode;
}
export declare function PanelTile(props: PanelTileProps): JSX.Element;

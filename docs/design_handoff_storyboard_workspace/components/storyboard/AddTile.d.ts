import type { CSSProperties, MouseEventHandler, ReactNode } from 'react';

/**
 * The trailing "+ Panel" / "+ Scene" tile that closes the panel grid and the
 * scene strip. Clicking the panel variant turns it into an inline one-field
 * form (pass that form as children); the scene variant opens a dialog.
 */
export interface AddTileProps {
  label?: string;
  /** panel: min-height 150px, fills a grid cell · scene: 190px wide, min-height 76px. */
  kind?: 'panel' | 'scene';
  onClick?: MouseEventHandler<HTMLDivElement>;
  className?: string;
  style?: CSSProperties;
  /** Replaces the plus + label with the inline add form. */
  children?: ReactNode;
}
export declare function AddTile(props: AddTileProps): JSX.Element;

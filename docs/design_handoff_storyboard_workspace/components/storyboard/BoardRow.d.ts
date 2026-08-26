import type { CSSProperties, ReactNode } from 'react';

/**
 * A storyboard in the landing list: name, a secondary meta line, and Open /
 * Delete actions. Bordered card on --surface-card, 8px radius, 12/16 padding,
 * stacked in a 720px-max column with an 8px gap.
 */
export interface BoardRowProps {
  name: ReactNode;
  /** The product's format: "16:9 · flux1 · updated 8/23/2025, 2:15:02 PM". */
  meta?: ReactNode;
  actions?: ReactNode;
  className?: string;
  style?: CSSProperties;
}
export declare function BoardRow(props: BoardRowProps): JSX.Element;

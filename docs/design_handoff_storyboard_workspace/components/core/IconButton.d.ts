import type { CSSProperties, MouseEventHandler, ReactNode } from 'react';

/**
 * Icon-only control. Metascan uses four distinct treatments: bordered 22px
 * squares in list rows, 18px circles in card corners, dark scrim circles over
 * media, and bare glyphs in dialog headers.
 */
export interface IconButtonProps {
  /** A literal glyph the product uses: up/down arrows, multiplication sign, pencil, play, plus. */
  glyph?: ReactNode;
  /** PrimeIcons class instead of a glyph, e.g. "pi-search-plus". */
  icon?: string;
  variant?: 'outline' | 'corner' | 'scrim' | 'bare';
  /** outline only: 24px instead of 22px. */
  size?: 'md' | 'lg';
  /** Hover turns danger-coloured. */
  destructive?: boolean;
  disabled?: boolean;
  title?: string;
  ariaLabel?: string;
  className?: string;
  style?: CSSProperties;
  onClick?: MouseEventHandler<HTMLButtonElement>;
}
export declare function IconButton(props: IconButtonProps): JSX.Element;

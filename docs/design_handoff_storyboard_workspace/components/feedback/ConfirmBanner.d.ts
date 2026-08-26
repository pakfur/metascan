import type { CSSProperties, ReactNode } from 'react';

/**
 * Inline destructive confirm, shown next to the control that triggered it
 * rather than in a modal. 12% danger fill, 40% danger border, 6px radius,
 * body-coloured text, with its buttons inline at the end of the sentence.
 */
export interface ConfirmBannerProps {
  message?: ReactNode;
  children?: ReactNode;
  /** sm 12px (panel pane) · lg 13px (inside a dialog). */
  size?: 'sm' | 'lg';
  actions?: ReactNode;
  className?: string;
  style?: CSSProperties;
}
export declare function ConfirmBanner(props: ConfirmBannerProps): JSX.Element;

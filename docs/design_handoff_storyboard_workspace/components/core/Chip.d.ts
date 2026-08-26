import type { CSSProperties, MouseEventHandler, ReactNode } from 'react';

/**
 * Pill-shaped status chip from the storyboard header: neutral progress,
 * primary (video target), danger (a failed stage, dismissible), warn
 * ("recompile suggested").
 */
export interface ChipProps {
  children?: ReactNode;
  tone?: 'neutral' | 'primary' | 'danger' | 'warn';
  /** PrimeIcons class, e.g. "pi-spin pi-spinner". */
  icon?: string;
  /** Renders the dismiss affordance used by danger chips in the header. */
  onDismiss?: MouseEventHandler<HTMLButtonElement>;
  title?: string;
  className?: string;
  style?: CSSProperties;
}
export declare function Chip(props: ChipProps): JSX.Element;

import type { CSSProperties, ReactNode } from 'react';

/**
 * Bottom-centre toast. Always dark slate (#1e293b on #f1f5f9 text) in BOTH
 * themes — it does not follow the surface tokens. Only the leading icon is
 * coloured by kind. Non-interactive (pointer-events:none) and self-dismissing.
 */
export interface ToastProps {
  message?: ReactNode;
  kind?: 'success' | 'warn' | 'info';
  /** false renders it inline for specimens instead of fixed to the viewport. */
  floating?: boolean;
  className?: string;
  style?: CSSProperties;
  children?: ReactNode;
}
export declare function Toast(props: ToastProps): JSX.Element;

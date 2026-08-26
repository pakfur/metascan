import type { CSSProperties, ReactNode } from 'react';

/**
 * Label + control wrapper. The label is Metascan's eyebrow treatment:
 * 11px / 600 / uppercase / 0.4px tracking / secondary text. Dialogs use the
 * plain 12px sentence-case variant instead.
 */
export interface FieldProps {
  label?: string;
  htmlFor?: string;
  /** 11px secondary helper under the control. */
  hint?: ReactNode;
  /** 13px danger-coloured message under the control. */
  error?: ReactNode;
  /** 12px sentence-case label (dialog forms) instead of the uppercase eyebrow. */
  plainLabel?: boolean;
  /** Trailing content on the label line: a status word, an inline link button. */
  aside?: ReactNode;
  /** Lay children out as a horizontal row of fields (align-items:flex-end, 10px gap). */
  row?: boolean;
  className?: string;
  style?: CSSProperties;
  children?: ReactNode;
}
export declare function Field(props: FieldProps): JSX.Element;

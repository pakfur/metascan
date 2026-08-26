import type { CSSProperties, InputHTMLAttributes, ReactNode } from 'react';

/** Native checkbox with its label, as used by the subject checklist and the compose stage picker. */
export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'size'> {
  label?: ReactNode;
  /** sm = 12px (subject checklist) · md = 13px (dialog stage picker). */
  size?: 'sm' | 'md';
  className?: string;
  style?: CSSProperties;
}
export declare function Checkbox(props: CheckboxProps): JSX.Element;

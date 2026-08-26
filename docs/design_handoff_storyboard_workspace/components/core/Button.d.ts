import type { CSSProperties, MouseEventHandler, ReactNode } from 'react';

/**
 * The Metascan button. Seven real appearances from the product: solid primary,
 * bordered secondary, outlined danger, solid danger (destructive confirm),
 * transparent quiet (header actions), dashed (add-a-row), underlined link.
 */
export interface ButtonProps {
  children?: ReactNode;
  /** Text label; equivalent to children, mirrors PrimeVue's label prop. */
  label?: string;
  variant?: 'primary' | 'secondary' | 'danger' | 'dangerSolid' | 'quiet' | 'dashed' | 'link';
  /** xs 3/10px·11px · sm 5/12px·12px · md 6/14px·13px · lg 8/20px·14px */
  size?: 'xs' | 'sm' | 'md' | 'lg';
  /** PrimeIcons class without the leading "pi", e.g. "pi-plus". */
  icon?: string;
  iconRight?: string;
  /** Toggle-on treatment: primary border + primary text + 12% primary fill. */
  active?: boolean;
  disabled?: boolean;
  type?: 'button' | 'submit' | 'reset';
  className?: string;
  style?: CSSProperties;
  title?: string;
  onClick?: MouseEventHandler<HTMLButtonElement>;
}
export declare function Button(props: ButtonProps): JSX.Element;

import type { SelectHTMLAttributes } from 'react';

/** Native select styled like the text input. Metascan never uses a custom dropdown here. */
export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  options?: Array<string | { value: string | number; label: string }>;
  /** Label of the empty option. The product uses an em dash in panes, "None" in dialogs. */
  placeholder?: string;
  includeEmpty?: boolean;
  variant?: 'panel' | 'dialog';
}
export declare function Select(props: SelectProps): JSX.Element;

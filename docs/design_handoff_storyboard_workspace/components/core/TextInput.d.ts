import type { InputHTMLAttributes } from 'react';

/**
 * Text/number input. Panel variant sits on --surface-ground; dialog variant
 * sits on --surface-card with 6/10 padding. Focus recolours the border to
 * --primary-color and removes the outline — there is no focus ring.
 */
export interface TextInputProps extends InputHTMLAttributes<HTMLInputElement> {
  variant?: 'panel' | 'dialog';
  mono?: boolean;
  readOnly?: boolean;
}
export declare function TextInput(props: TextInputProps): JSX.Element;

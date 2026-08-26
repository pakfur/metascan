import type { TextareaHTMLAttributes } from 'react';

/** Vertically resizable textarea. Use mono for prompts, outline JSON and compiled video prompts. */
export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  variant?: 'panel' | 'dialog';
  mono?: boolean;
}
export declare function Textarea(props: TextareaProps): JSX.Element;

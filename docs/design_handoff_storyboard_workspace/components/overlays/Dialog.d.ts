import type { CSSProperties, ReactNode } from 'react';

/**
 * Metascan's only modal shell: a 50%-black scrim with a centred 12px-radius
 * card on --surface-section, 22/28/24 padding and a 0 20px 60px shadow.
 * Clicking the scrim cancels. There is no close X except on the reference
 * picker, which uses a bare IconButton in its own header row.
 */
export interface DialogProps {
  title?: ReactNode;
  /** 13px body copy, 1.5 line-height. */
  message?: ReactNode;
  /** 12px secondary line under the message, e.g. an affected-image count. */
  meta?: ReactNode;
  /** sm 480px (confirms, create) · md 640px (compose story) · lg 720px (reference picker). */
  size?: 'sm' | 'md' | 'lg';
  /** Raise to z-index 950 when this dialog opens over another dialog. */
  nested?: boolean;
  /** Footer buttons, laid out flex-wrap with 10px gap. */
  actions?: ReactNode;
  /** Called on scrim click. */
  onDismiss?: () => void;
  className?: string;
  style?: CSSProperties;
  children?: ReactNode;
}
export declare function Dialog(props: DialogProps): JSX.Element;

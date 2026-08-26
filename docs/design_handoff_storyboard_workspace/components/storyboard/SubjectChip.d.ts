import type { CSSProperties, MouseEventHandler } from 'react';

/**
 * A cast member on a beat. Clickable: clicking a non-primary chip promotes it
 * to primary (first in subject_ids). The primary chip carries a star and
 * switches to primary border + primary text.
 */
export interface SubjectChipProps {
  name: string;
  primary?: boolean;
  title?: string;
  onClick?: MouseEventHandler<HTMLButtonElement>;
  className?: string;
  style?: CSSProperties;
}
export declare function SubjectChip(props: SubjectChipProps): JSX.Element;

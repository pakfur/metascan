import type { CSSProperties } from 'react';

/**
 * Generation-job state drawn over a thumbnail. Two layouts: 'overlay' is the
 * bottom strip on a 150px panel tile; 'fill' covers a 28px beat-pill thumb.
 * Both sit on a black scrim, and 'failed' switches to a 70%-danger fill with
 * a help cursor and the error in its title.
 */
export interface JobBadgeProps {
  state?: 'queued' | 'running' | 'failed' | null;
  /** running only: steps done. */
  value?: number | null;
  /** running only: total steps. */
  max?: number | null;
  /** failed only: the message shown on hover. */
  error?: string | null;
  layout?: 'overlay' | 'fill';
  className?: string;
  style?: CSSProperties;
}
export declare function JobBadge(props: JobBadgeProps): JSX.Element | null;

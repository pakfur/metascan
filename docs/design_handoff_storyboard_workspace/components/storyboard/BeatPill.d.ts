import type { CSSProperties, MouseEventHandler, ReactNode } from 'react';

/**
 * One beat inside a shot: a single dense 28px-thumb row carrying index,
 * image count, duration, camera motion, cut marker, the action text, and
 * dialog count, with reorder/delete icon buttons at the end.
 */
export interface BeatPillProps {
  /** 1-based display index. */
  index: number;
  action: string;
  /** Seconds; rendered as e.g. "2.5s". */
  duration?: number | null;
  /** Raw camera_motion value; underscores are replaced with spaces for display. */
  motion?: string | null;
  /** Renders "(cut)" in warn colour — a hard cut before this beat. */
  isCut?: boolean;
  imageCount?: number;
  dialogCount?: number;
  /** Keeper thumbnail URL. */
  thumb?: string | null;
  /** A JobBadge with layout="fill". */
  job?: ReactNode;
  selected?: boolean;
  onClick?: MouseEventHandler<HTMLDivElement>;
  /** Trailing IconButtons: move up, move down, delete. */
  actions?: ReactNode;
  className?: string;
  style?: CSSProperties;
}
export declare function BeatPill(props: BeatPillProps): JSX.Element;

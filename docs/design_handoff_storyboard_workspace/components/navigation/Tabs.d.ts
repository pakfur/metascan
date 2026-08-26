import type { CSSProperties } from 'react';

/**
 * Underline tabs, as used by the storyboard side panel (Edit / Preview).
 * Inactive labels are secondary text; the active one goes body-coloured with
 * a 2px primary underline that overlaps the container border.
 */
export interface TabsProps {
  tabs?: Array<string | { value: string; label: string }>;
  value?: string;
  onChange?: (value: string) => void;
  className?: string;
  style?: CSSProperties;
}
export declare function Tabs(props: TabsProps): JSX.Element;

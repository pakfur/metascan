Live ComfyUI job state over a panel or beat thumbnail. Image jobs are beat-scoped; video jobs are panel-scoped.

```jsx
<JobBadge state="queued" />
<JobBadge state="running" value={14} max={20} />
<JobBadge state="failed" error="preset 3: node 12 missing input" />
<JobBadge state="running" layout="fill" />
```

Metascan writes these states as an hourglass, a spinning PrimeIcons spinner, and a warning sign — deliberately terse, no progress bar. Only 'done' has no badge; a finished job just shows its image.

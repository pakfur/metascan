Status chip for in-flight work and stage failures — lives in the storyboard header row, never as a form control.

```jsx
<Chip tone="primary" title="Video model and mode driving the compiled video prompts">MiniMax H3 · ref2va</Chip>
<Chip>synthesizing 4/12</Chip>
<Chip tone="danger" onDismiss={clearError}>compile failed</Chip>
<Chip tone="warn">recompile suggested</Chip>
```

Chips are read-only status. A chip is never a filter, a tag, or a removable input value — use SubjectChip for that. Danger chips cap at 360px and ellipsize.

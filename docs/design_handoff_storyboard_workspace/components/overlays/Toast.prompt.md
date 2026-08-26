Transient confirmation for library-level actions (copied, added to folder, queued). One toast at a time, bottom-centre, fading up 8px over 0.2s.

```jsx
<Toast kind="success" message="Prompt copied" />
<Toast kind="warn" message="3 files skipped" />
<Toast kind="info" message="Embedding index rebuilt" />
```

Toasts never carry actions or a dismiss button. Errors that need a decision are not toasts — the storyboard puts them in a danger `Chip` in the header or a `ConfirmBanner` next to the control that failed.

Single-line input. Panel variant for the working panes, dialog variant inside modals.

```jsx
<TextInput defaultValue={panel.action} onChange={commitAction} />
<TextInput variant="dialog" placeholder="e.g. Coffee shop meet-cute" />
<TextInput type="number" step="0.5" min="0.5" defaultValue={2.5} />
<TextInput readOnly value="7.5" title="Derived from the sum of this shot's beat durations. Edit the beats to change it." />
```

Metascan commits text fields on `change` (blur / Enter), not on every keystroke, and keeps a local copy plus a last-synced snapshot so a background refresh never stomps an in-progress edit. Reproduce that shape in prototypes rather than a controlled value that patches per keystroke.

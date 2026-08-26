Metascan's button — every clickable action except icon-only controls (use IconButton) and in-tile affordances.

```jsx
<Button variant="primary" size="lg" onClick={create}>Create</Button>
<Button variant="secondary" size="lg">Cancel</Button>
<Button variant="danger" size="lg">Delete images permanently</Button>
<Button variant="quiet" icon="pi-sparkles">Compose</Button>
<Button variant="dashed" size="xs">+ line</Button>
<Button variant="link">Unlock</Button>
```

Sizes map to real product paddings: `xs` for in-panel utility buttons (Copy, Reroll, Compile), `sm` for panel-detail actions, `md` for list-row actions, `lg` for dialog footers. `active` gives the "Cut" toggle treatment. Hover is fixed: primary/dangerSolid fade to 90% opacity, everything else fills with `--surface-hover`. Disabled is `opacity: .5` plus `cursor: not-allowed` — never a greyed colour.

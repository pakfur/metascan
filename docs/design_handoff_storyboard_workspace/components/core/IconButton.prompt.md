Icon-only control for row affordances, card corners, and media overlays.

```jsx
<IconButton glyph="↑" title="Move beat up" />
<IconButton glyph="✕" destructive title="Delete beat" />
<IconButton variant="corner" glyph="✎" title="Edit scene" />
<IconButton variant="scrim" glyph="×" title="Delete panel" />
<IconButton variant="bare" glyph="×" title="Close" />
```

`corner` and `scrim` buttons live inside a `position:relative` tile and are revealed on tile hover (opacity 0 to 1, 0.15s) — the parent tile owns that reveal, not this component. Metascan writes these glyphs as literal unicode characters, not PrimeIcons.

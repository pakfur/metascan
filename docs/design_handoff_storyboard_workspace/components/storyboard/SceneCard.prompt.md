One card in the storyboard's horizontal scene strip. Selecting it loads that scene's panels into the grid below.

```jsx
<SceneCard
  name="Kitchen, dawn"
  subtitle="interior · dawn"
  setting="Cold light through slatted blinds, unwashed cups on the counter."
  thumbs={[a, b, c, null, null, null]}
  active={scene.id === selectedSceneId}
  actions={<>
    <IconButton variant="corner" glyph="▶" title="Render scene videos" style={{ fontSize: 8 }} />
    <IconButton variant="corner" glyph="✎" title="Edit scene" style={{ fontSize: 10 }} />
    <IconButton variant="corner" glyph="×" destructive title="Delete scene" />
  </>}
  onClick={() => selectScene(scene)}
/>
```

Selection is a primary border plus a 1px primary ring — the fill stays `--surface-card`. Hover fills with `--surface-hover`. The render action only appears when the board has both a video target and a video preset.

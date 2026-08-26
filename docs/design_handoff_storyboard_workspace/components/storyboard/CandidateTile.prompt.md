The keeper picker. Every rendered variant stays as a candidate; picking one makes it the keeper and hides the rest from the main library grid.

```jsx
<div className="ms-candidates-row">
  {beat.images.map((img, i) => (
    <CandidateTile
      key={img.id}
      src={thumbnailUrl(img.file_path)}
      selected={img.id === beat.selected_image_id}
      title={'seed ' + (img.seed ?? '—') + ' · variant ' + img.variant_index}
      onClick={() => toggleKeeper(img)}
      onExpand={() => openViewer(i)}
    />
  ))}
  {beat.images.length === 0 && <div className="ms-hint" style={{ padding: '8px 0' }}>No candidates yet.</div>}
</div>
```

Selection is a border only — no scale, no shadow, no overlay tint. The row scrolls horizontally; it never wraps.

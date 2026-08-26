Review strip for rendered clips on a shot. Unlike candidates there is no keeper — this is for watching takes and discarding the bad ones.

```jsx
<Field label="Video takes">
  <div className="ms-candidates-row">
    {panel.videos.map((v, i) => (
      <VideoTakeTile
        key={v.id}
        src={thumbnailUrl(v.file_path)}
        title={'seed ' + (v.seed ?? '—') + ' · take ' + (v.variant_index + 1)}
        onPlay={() => openViewer(i)}
        onDelete={() => confirmDeleteTake(v)}
      />
    ))}
  </div>
</Field>
```

The strip is hidden entirely when there are no takes — Metascan does not show an empty "Video takes" label.

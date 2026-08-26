The beat list inside the shot detail pane. A shot is the video-generation unit; a beat is its internal timeline unit and the still-image unit.

```jsx
<div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
  {panel.beats.map((b, i) => (
    <BeatPill
      key={b.id}
      index={i + 1}
      action={b.action}
      duration={b.duration_s}
      motion={b.camera_motion}
      isCut={!!b.is_cut}
      imageCount={b.images.length}
      dialogCount={b.dialog.length}
      thumb={keeperThumb(b)}
      selected={b.id === selectedBeatId}
      onClick={() => setSelectedBeatId(b.id)}
      actions={<>
        <IconButton glyph="↑" title="Move beat up" disabled={i === 0} />
        <IconButton glyph="↓" title="Move beat down" disabled={i === last} />
        <IconButton glyph="✕" destructive title="Delete beat" />
      </>}
    />
  ))}
</div>
```

Selected state is a primary border plus a 10% primary fill — stronger than a scene card, because the beat drives what the side panel is editing. The list header reads "BEATS — 7.5s" and appends a warn-coloured "exceeds H3 15s clip cap" when the sum runs over.

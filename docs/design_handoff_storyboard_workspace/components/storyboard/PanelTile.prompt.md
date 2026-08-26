The shot tile in the storyboard's panel grid. One tile per shot; click selects, double-click plays.

```jsx
<PanelTile
  src={keeperThumb}
  caption="MCU · Mara"
  active={panel.id === selectedPanelId}
  locked={panel.beats[0]?.prompt_locked === 1}
  videoCount={panel.videos.length}
  job={<JobBadge state="running" value={14} max={20} />}
  onClick={() => select(panel.id)}
  onDoubleClick={() => openViewer(panel)}
  onDelete={() => confirmDelete(panel)}
/>
```

Grid: `display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:14px; padding:16px 20px`. Metascan marks state with emoji glyphs here (lock, clapperboard) and unicode in the job badge — that is deliberate and load-bearing, not decoration; keep them.

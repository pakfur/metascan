Native select for every enumerated storyboard value: shot size, angle, lens, camera motion / amplitude / speed, aspect ratio, target model, video mode, video anchor, dialog speaker.

```jsx
<Select options={['ECU','CU','MCU','MS','MLS','WS','EWS']} value={beat.shot_size ?? ''} onChange={commitShotSize} />
<Select variant="dialog" placeholder="None" options={presets.map(p => ({ value: p.id, label: p.name + ' (' + p.kind + ')' }))} />
```

Option lists come from the product's own const arrays (`SHOT_SIZES`, `ANGLES`, `LENSES`, `CAMERA_MOTIONS`, `CAMERA_AMPLITUDES`, `CAMERA_SPEEDS`, `ASPECT_RATIOS`, `TARGET_MODELS`, `VIDEO_MODES`) — use those exact values, lowercase and underscored as written.

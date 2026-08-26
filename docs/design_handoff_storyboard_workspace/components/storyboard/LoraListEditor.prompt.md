Edits the LoRA stack injected into a shot's ComfyUI preset. Two instances per shot: image LoRAs and video LoRAs.

```jsx
<LoraListEditor
  label="Image LoRAs"
  entries={panel.image_loras}
  options={loraFiles}
  listId="lora-image"
  onChange={(entries) => patchPanel(panel.id, { image_loras: entries })}
/>
```

Rows are fully controlled by `entries` and commit on change, so a background refresh never clobbers text mid-edit. Strength defaults to 1.0 and steps by 0.05. A failed LoRA fetch degrades to free text — never block the field on it.

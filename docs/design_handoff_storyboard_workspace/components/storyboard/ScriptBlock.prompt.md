The Preview tab's read-only output: shot script, image prompt, compiled video prompt.

```jsx
<Field label="Shot script" aside={<Button size="xs" onClick={copy}>{copied ? 'Copied' : 'Copy'}</Button>}>
  <ScriptBlock
    header={scriptBlocks.header}
    beats={scriptBlocks.beats}
    selectedBeatId={selectedBeatId}
    onSelectBeat={setSelectedBeatId}
  />
</Field>

<Field label="Image prompt" aside={<Button size="xs">Copy</Button>}>
  <ScriptBlock text={beat.prompt} />
</Field>
```

Selecting a beat elsewhere scrolls its block into view and tints it 10% primary with a 2px primary left edge — the only left-accent border in the whole product, and it is a selection marker, not decoration. Copy buttons flip their own label to "Copied" for 1.5s; they never fire a toast.

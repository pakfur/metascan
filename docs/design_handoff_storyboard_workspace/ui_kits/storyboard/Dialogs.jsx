const { Button, IconButton, Chip, Field, TextInput, Select, Textarea, Checkbox, Dialog, ConfirmBanner } = window.MetascanDesignSystem_f2fbbd

function NewStoryboardDialog({ onClose, onCreated }) {
  const [name, setName] = React.useState('')
  const [video, setVideo] = React.useState('minimax')
  return (
    <Dialog
      title="New storyboard"
      onDismiss={onClose}
      actions={<>
        <Button variant="primary" size="lg" disabled={!name.trim()} onClick={() => onCreated(name.trim())}>Create</Button>
        <Button variant="secondary" size="lg" onClick={onClose}>Cancel</Button>
      </>}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <Field plainLabel label="Name" htmlFor="sb-name">
          <TextInput id="sb-name" variant="dialog" placeholder="e.g. Coffee shop meet-cute" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field row>
          <Field plainLabel label="Target model"><Select variant="dialog" includeEmpty={false} options={window.SB.TARGET_MODELS} defaultValue="flux1" /></Field>
          <Field plainLabel label="Aspect ratio"><Select variant="dialog" includeEmpty={false} options={window.SB.ASPECT_RATIOS} defaultValue="16:9" /></Field>
        </Field>
        <Field plainLabel label="Workflow preset" hint={<>None yet. <button type="button" className="ms-btn ms-btn--link" style={{ fontSize: 12 }}>Register one</button></>}>
          <Select variant="dialog" placeholder="None" options={[{ value: 2, label: 'flux1 stills (t2i)' }, { value: 3, label: 'flux1 ref (ref)' }]} defaultValue={2} />
        </Field>
        <Field plainLabel label="Video target (optional)">
          <Select variant="dialog" includeEmpty={false} value={video} onChange={(e) => setVideo(e.target.value)}
            options={[{ value: '', label: 'None — stills only' }, { value: 'minimax', label: 'MiniMax H3' }]} />
        </Field>
        {video ? (
          <Field row>
            <Field plainLabel label="Video mode"><Select variant="dialog" includeEmpty={false} options={window.SB.VIDEO_MODES} defaultValue="ref2va" /></Field>
            <Field plainLabel label="Video workflow preset"><Select variant="dialog" placeholder="None" options={[{ value: 4, label: 'h3 ref2v' }]} defaultValue={4} /></Field>
          </Field>
        ) : null}
        <Field plainLabel label="Batch size"><TextInput variant="dialog" type="number" min="1" max="16" defaultValue={4} /></Field>
        <Field plainLabel label="Style block (optional)">
          <Textarea variant="dialog" rows={3} placeholder="Shared style/quality tags applied to every panel prompt" defaultValue="muted palette, 35mm film grain, no text" />
        </Field>
        <Field plainLabel label="Negative prompt (optional)">
          <Textarea variant="dialog" rows={2} placeholder="Things to avoid" />
        </Field>
      </div>
    </Dialog>
  )
}

function ComposeDialog({ onClose }) {
  const SB = window.SB
  const [premise, setPremise] = React.useState(SB.tree.source_text)
  const [stages, setStages] = React.useState({ outline: false, scenes: true, shots: true, beats: true })
  const [confirming, setConfirming] = React.useState(false)
  const checked = SB.COMPOSE_STAGES.filter((s) => stages[s])
  return (
    <Dialog size="md" title="Compose story" onDismiss={onClose}
      actions={<Button variant="secondary" size="lg" onClick={onClose}>Close</Button>}>
      <p className="ms-hint" style={{ fontSize: 12, marginBottom: 14 }}>
        Write a premise, generate an outline, then build scenes, shots and beats from it.
      </p>
      <p className="ms-hint" style={{ fontSize: 12, marginBottom: 14 }}>
        Video: MiniMax H3 · ref2va (change in Settings)
      </p>
      <label className="ms-label ms-label--plain" style={{ display: 'block', margin: '14px 0 6px' }}>Premise</label>
      <Textarea variant="dialog" rows={5} value={premise} onChange={(e) => setPremise(e.target.value)} />
      <div className="ms-dialog__actions" style={{ marginTop: 14 }}>
        <Button variant="primary" size="lg" disabled={!premise.trim()}>Generate outline</Button>
      </div>
      <label className="ms-label ms-label--plain" style={{ display: 'block', margin: '14px 0 6px' }}>Outline</label>
      <Textarea variant="dialog" mono rows={8} defaultValue={SB.tree.outline} />
      <label className="ms-label ms-label--plain" style={{ display: 'block', margin: '14px 0 6px' }}>Stages to build</label>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16 }}>
        {SB.COMPOSE_STAGES.map((s) => (
          <Checkbox key={s} size="md" label={s[0].toUpperCase() + s.slice(1)} checked={stages[s]}
            onChange={() => setStages({ ...stages, [s]: !stages[s] })} />
        ))}
      </div>
      <div className="ms-dialog__actions" style={{ marginTop: 14 }}>
        <Button variant="primary" size="lg" disabled={checked.length === 0} onClick={() => setConfirming(true)}>Build checked stages</Button>
      </div>
      {confirming ? (
        <ConfirmBanner size="lg" message="This replaces existing content — continue?"
          actions={<>
            <Button variant="dangerSolid" size="lg" onClick={onClose}>Continue</Button>
            <Button variant="secondary" size="lg" onClick={() => setConfirming(false)}>Cancel</Button>
          </>} />
      ) : null}
    </Dialog>
  )
}

function DeleteImagesDialog({ title, message, imageCount, onPurge, onKeep, onCancel }) {
  return (
    <Dialog nested title={title} message={message}
      meta={imageCount === undefined ? undefined : imageCount + ' generated image' + (imageCount === 1 ? '' : 's') + ' affected.'}
      onDismiss={onCancel}
      actions={<>
        <Button variant="danger" size="lg" onClick={onPurge}>Delete images permanently</Button>
        <Button variant="primary" size="lg" onClick={onKeep}>Keep images in library</Button>
        <Button variant="secondary" size="lg" onClick={onCancel}>Cancel</Button>
      </>} />
  )
}

function SceneEditDialog({ scene, onClose }) {
  const s = scene ?? {}
  return (
    <Dialog size="md" title={scene ? 'Edit scene' : 'New scene'} onDismiss={onClose}
      actions={<>
        <Button variant="primary" size="lg" onClick={onClose}>Save</Button>
        <Button variant="secondary" size="lg" onClick={onClose}>Cancel</Button>
      </>}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <Field plainLabel label="Name"><TextInput variant="dialog" defaultValue={s.name ?? ''} placeholder="e.g. Kitchen, dawn" /></Field>
        <Field row>
          <Field plainLabel label="Location"><TextInput variant="dialog" defaultValue={s.location ?? ''} /></Field>
          <Field plainLabel label="Time of day"><TextInput variant="dialog" defaultValue={s.time_of_day ?? ''} /></Field>
        </Field>
        <Field plainLabel label="Setting"><Textarea variant="dialog" rows={3} defaultValue={s.setting ?? ''} /></Field>
        <Field row>
          <Field plainLabel label="Mood"><TextInput variant="dialog" defaultValue={s.mood ?? ''} /></Field>
          <Field plainLabel label="Lighting"><TextInput variant="dialog" defaultValue={s.lighting ?? ''} /></Field>
        </Field>
        <Field plainLabel label="Setting reference" hint="Pick an image from the library to describe this setting.">
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <div style={{ width: 72, height: 72, borderRadius: 6, overflow: 'hidden', background: 'var(--surface-ground)', border: '1px dashed var(--surface-border)' }} />
            <Button size="md">Choose reference…</Button>
            <Button size="md" variant="quiet">Describe with VLM</Button>
          </div>
        </Field>
      </div>
    </Dialog>
  )
}

Object.assign(window, { NewStoryboardDialog, ComposeDialog, DeleteImagesDialog, SceneEditDialog })

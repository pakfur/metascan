import React from 'react'
const cx = (...a) => a.filter(Boolean).join(' ')

export function ScriptBlock({ header, beats, text, selectedBeatId, onSelectBeat, className, style }) {
  if (text != null) {
    return (
      <pre className={cx('ms-script', className)} style={style}>
        {text}
      </pre>
    )
  }
  return (
    <div className={cx('ms-script', className)} style={style}>
      {header ? <div className="ms-script__header">{header}</div> : null}
      {!beats || beats.length === 0 ? (
        <div className="ms-script__beat">(no beats)</div>
      ) : (
        beats.map((b) => (
          <div
            key={b.beatId}
            className={cx('ms-script__beat', b.beatId === selectedBeatId && 'ms-script__beat--selected')}
            onClick={() => onSelectBeat && onSelectBeat(b.beatId)}
          >
            {b.text}
          </div>
        ))
      )}
    </div>
  )
}

import React from 'react'
const cx = (...a) => a.filter(Boolean).join(' ')

export function BoardRow({ name, meta, actions, className, style }) {
  return (
    <div className={cx('ms-board-row', className)} style={style}>
      <div>
        <div className="ms-board-row__name">{name}</div>
        {meta ? <div className="ms-board-row__meta">{meta}</div> : null}
      </div>
      {actions ? <div className="ms-board-row__actions">{actions}</div> : null}
    </div>
  )
}

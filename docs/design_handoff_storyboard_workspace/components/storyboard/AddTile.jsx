import React from 'react'
const cx = (...a) => a.filter(Boolean).join(' ')

export function AddTile({ label = 'Panel', kind = 'panel', onClick, className, style, children }) {
  return (
    <div className={cx('ms-add-tile', 'ms-add-tile--' + kind, className)} style={style} onClick={onClick}>
      {children ?? (
        <>
          <span className="ms-add-tile__plus">+</span>
          <span className="ms-add-tile__label">{label}</span>
        </>
      )}
    </div>
  )
}

import React from 'react'
const cx = (...a) => a.filter(Boolean).join(' ')

export function Dialog({ title, message, meta, size = 'sm', nested = false, actions, onDismiss, className, style, children }) {
  return (
    <div
      className={cx('ms-dialog-overlay', nested && 'ms-dialog-overlay--nested')}
      onClick={(e) => {
        if (e.target === e.currentTarget && onDismiss) onDismiss()
      }}
    >
      <div className={cx('ms-dialog', size !== 'sm' && 'ms-dialog--' + size, className)} style={style}>
        {title ? <h3 className="ms-dialog__title">{title}</h3> : null}
        {message ? <p className="ms-dialog__message">{message}</p> : null}
        {meta ? <p className="ms-dialog__meta">{meta}</p> : null}
        {children}
        {actions ? <div className="ms-dialog__actions">{actions}</div> : null}
      </div>
    </div>
  )
}

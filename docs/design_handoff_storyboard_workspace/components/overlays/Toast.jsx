import React from 'react'
const cx = (...a) => a.filter(Boolean).join(' ')

const ICONS = { success: 'pi-check', warn: 'pi-exclamation-circle', info: 'pi-info-circle' }

export function Toast({ message, kind = 'success', floating = true, className, style, children }) {
  return (
    <div className={cx('ms-toast', 'ms-toast--' + kind, !floating && 'ms-toast--static', className)} style={style}>
      <i className={cx('pi', ICONS[kind] ?? ICONS.success)} aria-hidden="true" />
      <span>{message ?? children}</span>
    </div>
  )
}

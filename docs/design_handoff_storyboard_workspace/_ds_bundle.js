/* @ds-bundle: {"format":4,"namespace":"MetascanDesignSystem_f2fbbd","components":[{"name":"Button","sourcePath":"components/core/Button.jsx"},{"name":"Checkbox","sourcePath":"components/core/Checkbox.jsx"},{"name":"Chip","sourcePath":"components/core/Chip.jsx"},{"name":"Field","sourcePath":"components/core/Field.jsx"},{"name":"IconButton","sourcePath":"components/core/IconButton.jsx"},{"name":"Select","sourcePath":"components/core/Select.jsx"},{"name":"TextInput","sourcePath":"components/core/TextInput.jsx"},{"name":"Textarea","sourcePath":"components/core/Textarea.jsx"},{"name":"ConfirmBanner","sourcePath":"components/feedback/ConfirmBanner.jsx"},{"name":"JobBadge","sourcePath":"components/feedback/JobBadge.jsx"},{"name":"Tabs","sourcePath":"components/navigation/Tabs.jsx"},{"name":"Dialog","sourcePath":"components/overlays/Dialog.jsx"},{"name":"Toast","sourcePath":"components/overlays/Toast.jsx"},{"name":"AddTile","sourcePath":"components/storyboard/AddTile.jsx"},{"name":"BeatPill","sourcePath":"components/storyboard/BeatPill.jsx"},{"name":"BoardRow","sourcePath":"components/storyboard/BoardRow.jsx"},{"name":"CandidateTile","sourcePath":"components/storyboard/CandidateTile.jsx"},{"name":"LoraListEditor","sourcePath":"components/storyboard/LoraListEditor.jsx"},{"name":"PanelTile","sourcePath":"components/storyboard/PanelTile.jsx"},{"name":"SceneCard","sourcePath":"components/storyboard/SceneCard.jsx"},{"name":"ScriptBlock","sourcePath":"components/storyboard/ScriptBlock.jsx"},{"name":"SubjectChip","sourcePath":"components/storyboard/SubjectChip.jsx"},{"name":"VideoTakeTile","sourcePath":"components/storyboard/VideoTakeTile.jsx"}],"sourceHashes":{"components/core/Button.jsx":"6d1bdcbd4e0c","components/core/Checkbox.jsx":"a7a969540d23","components/core/Chip.jsx":"1184dc086500","components/core/Field.jsx":"b9e06f69214d","components/core/IconButton.jsx":"2e572c7e7311","components/core/Select.jsx":"c4798f206e03","components/core/TextInput.jsx":"9333d6bb4fb3","components/core/Textarea.jsx":"e494758a65ab","components/feedback/ConfirmBanner.jsx":"b0a82b5daa84","components/feedback/JobBadge.jsx":"0ed6cd71af5a","components/navigation/Tabs.jsx":"fa586a9d0a8b","components/overlays/Dialog.jsx":"962d1d205d45","components/overlays/Toast.jsx":"5520569eaef0","components/storyboard/AddTile.jsx":"121982c31a01","components/storyboard/BeatPill.jsx":"df2dd98e3cee","components/storyboard/BoardRow.jsx":"caa235998ad2","components/storyboard/CandidateTile.jsx":"a833487ff960","components/storyboard/LoraListEditor.jsx":"3dc5f8e0e5b9","components/storyboard/PanelTile.jsx":"7a2e7d3188e0","components/storyboard/SceneCard.jsx":"618f862795a1","components/storyboard/ScriptBlock.jsx":"3c9115ba8fcf","components/storyboard/SubjectChip.jsx":"379d3146ae4d","components/storyboard/VideoTakeTile.jsx":"e74f6f671f41","redesign/RedesignPipeline.jsx":"7f964131d9f3","redesign/RedesignTimeline.jsx":"489203fd538e","redesign/RedesignWorkspace.jsx":"3758adf18c17","ui_kits/library/LibraryApp.jsx":"f2ade17e6c00","ui_kits/storyboard/BoardScreen.jsx":"624e6f267b09","ui_kits/storyboard/Dialogs.jsx":"093de88edd82","ui_kits/storyboard/LandingScreen.jsx":"3f7431dd254e","ui_kits/storyboard/MoreDialogs.jsx":"55182f3761b6","ui_kits/storyboard/ShotDetail.jsx":"c8038821b741","ui_kits/storyboard/SidePanel.jsx":"1ad70eeeae0e","ui_kits/storyboard/fixtures.js":"6bcf3ec73611"},"inlinedExternals":[],"unexposedExports":[]} */

(() => {

const __ds_ns = (window.MetascanDesignSystem_f2fbbd = window.MetascanDesignSystem_f2fbbd || {});

const __ds_scope = {};

(__ds_ns.__errors = __ds_ns.__errors || []);

// components/core/Button.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const cx = (...a) => a.filter(Boolean).join(' ');
const VARIANTS = ['primary', 'secondary', 'danger', 'dangerSolid', 'quiet', 'dashed', 'link'];
function Button({
  children,
  label,
  variant = 'secondary',
  size = 'md',
  icon,
  iconRight,
  active = false,
  disabled = false,
  type = 'button',
  className,
  style,
  onClick,
  title,
  ...rest
}) {
  const v = VARIANTS.includes(variant) ? variant : 'secondary';
  const isLink = v === 'link';
  return /*#__PURE__*/React.createElement("button", _extends({
    type: type,
    disabled: disabled,
    title: title,
    onClick: onClick,
    style: style,
    className: cx('ms-btn', 'ms-btn--' + v, !isLink && 'ms-btn--' + size, active && 'ms-btn__toggle-on', className)
  }, rest), icon ? /*#__PURE__*/React.createElement("span", {
    className: cx('pi', icon),
    "aria-hidden": "true"
  }) : null, label ?? children, iconRight ? /*#__PURE__*/React.createElement("span", {
    className: cx('pi', iconRight),
    "aria-hidden": "true"
  }) : null);
}
Object.assign(__ds_scope, { Button });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Button.jsx", error: String((e && e.message) || e) }); }

// components/core/Checkbox.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const cx = (...a) => a.filter(Boolean).join(' ');
function Checkbox({
  label,
  size = 'sm',
  className,
  style,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("label", {
    className: cx('ms-check', size === 'md' && 'ms-check--lg', className),
    style: style
  }, /*#__PURE__*/React.createElement("input", _extends({
    type: "checkbox"
  }, rest)), label);
}
Object.assign(__ds_scope, { Checkbox });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Checkbox.jsx", error: String((e && e.message) || e) }); }

// components/core/Chip.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const cx = (...a) => a.filter(Boolean).join(' ');
function Chip({
  children,
  tone = 'neutral',
  icon,
  onDismiss,
  title,
  className,
  style,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("span", _extends({
    title: title,
    style: style,
    className: cx('ms-chip', tone !== 'neutral' && 'ms-chip--' + tone, className)
  }, rest), icon ? /*#__PURE__*/React.createElement("span", {
    className: cx('pi', icon),
    "aria-hidden": "true"
  }) : null, /*#__PURE__*/React.createElement("span", {
    className: "ms-chip__text"
  }, children), onDismiss ? /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "ms-chip__dismiss",
    "aria-label": "Dismiss",
    onClick: onDismiss
  }, "\xD7") : null);
}
Object.assign(__ds_scope, { Chip });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Chip.jsx", error: String((e && e.message) || e) }); }

// components/core/Field.jsx
try { (() => {
const cx = (...a) => a.filter(Boolean).join(' ');
function Field({
  label,
  htmlFor,
  hint,
  error,
  plainLabel = false,
  aside,
  row = false,
  className,
  style,
  children
}) {
  const labelEl = label ? /*#__PURE__*/React.createElement("label", {
    className: cx('ms-label', plainLabel && 'ms-label--plain'),
    htmlFor: htmlFor
  }, label) : null;
  return /*#__PURE__*/React.createElement("div", {
    className: cx(row ? 'ms-field-row' : 'ms-field', className),
    style: style
  }, labelEl && aside ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 10
    }
  }, labelEl, aside) : labelEl, children, hint ? /*#__PURE__*/React.createElement("span", {
    className: "ms-hint"
  }, hint) : null, error ? /*#__PURE__*/React.createElement("span", {
    className: "ms-error"
  }, error) : null);
}
Object.assign(__ds_scope, { Field });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Field.jsx", error: String((e && e.message) || e) }); }

// components/core/IconButton.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const cx = (...a) => a.filter(Boolean).join(' ');
function IconButton({
  glyph,
  icon,
  variant = 'outline',
  size,
  destructive = false,
  disabled = false,
  title,
  ariaLabel,
  className,
  style,
  onClick,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("button", _extends({
    type: "button",
    disabled: disabled,
    title: title,
    "aria-label": ariaLabel ?? title,
    onClick: onClick,
    style: style,
    className: cx('ms-iconbtn', 'ms-iconbtn--' + variant, size === 'lg' && 'ms-iconbtn--lg', destructive && 'ms-iconbtn--destructive', className)
  }, rest), icon ? /*#__PURE__*/React.createElement("span", {
    className: cx('pi', icon),
    "aria-hidden": "true"
  }) : glyph);
}
Object.assign(__ds_scope, { IconButton });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/IconButton.jsx", error: String((e && e.message) || e) }); }

// components/core/Select.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const cx = (...a) => a.filter(Boolean).join(' ');
function Select({
  options = [],
  placeholder = '\u2014',
  includeEmpty = true,
  variant = 'panel',
  className,
  children,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("select", _extends({
    className: cx('ms-control', variant === 'dialog' && 'ms-control--dialog', className)
  }, rest), children ?? /*#__PURE__*/React.createElement(React.Fragment, null, includeEmpty ? /*#__PURE__*/React.createElement("option", {
    value: ""
  }, placeholder) : null, options.map(o => {
    const value = typeof o === 'object' ? o.value : o;
    const label = typeof o === 'object' ? o.label : o;
    return /*#__PURE__*/React.createElement("option", {
      key: String(value),
      value: value
    }, label);
  })));
}
Object.assign(__ds_scope, { Select });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Select.jsx", error: String((e && e.message) || e) }); }

// components/core/TextInput.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const cx = (...a) => a.filter(Boolean).join(' ');
function TextInput({
  variant = 'panel',
  mono = false,
  readOnly = false,
  className,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("input", _extends({
    readOnly: readOnly,
    tabIndex: readOnly ? -1 : undefined,
    className: cx('ms-control', variant === 'dialog' && 'ms-control--dialog', mono && 'ms-control--mono', readOnly && 'ms-control--readonly', className)
  }, rest));
}
Object.assign(__ds_scope, { TextInput });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/TextInput.jsx", error: String((e && e.message) || e) }); }

// components/core/Textarea.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const cx = (...a) => a.filter(Boolean).join(' ');
function Textarea({
  variant = 'panel',
  mono = false,
  rows = 3,
  className,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("textarea", _extends({
    rows: rows,
    className: cx('ms-control', variant === 'dialog' && 'ms-control--dialog', mono && 'ms-control--mono', className)
  }, rest));
}
Object.assign(__ds_scope, { Textarea });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Textarea.jsx", error: String((e && e.message) || e) }); }

// components/feedback/ConfirmBanner.jsx
try { (() => {
const cx = (...a) => a.filter(Boolean).join(' ');
function ConfirmBanner({
  children,
  message,
  size = 'sm',
  actions,
  className,
  style
}) {
  return /*#__PURE__*/React.createElement("p", {
    className: cx('ms-confirm', size === 'lg' && 'ms-confirm--lg', className),
    style: style
  }, message ?? children, actions ? /*#__PURE__*/React.createElement("span", {
    className: "ms-confirm__actions"
  }, actions) : null);
}
Object.assign(__ds_scope, { ConfirmBanner });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/ConfirmBanner.jsx", error: String((e && e.message) || e) }); }

// components/feedback/JobBadge.jsx
try { (() => {
const cx = (...a) => a.filter(Boolean).join(' ');
function JobBadge({
  state,
  value,
  max,
  error,
  layout = 'overlay',
  className,
  style
}) {
  if (!state) return null;
  const base = layout === 'fill' ? 'ms-job-fill' : 'ms-job-overlay';
  const failed = state === 'failed';
  let body = null;
  if (state === 'queued') body = layout === 'fill' ? '\u23F3' : '\u23F3 queued';else if (state === 'running') body = /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("span", {
    className: "pi pi-spin pi-spinner ms-spin",
    "aria-hidden": "true"
  }), value != null && max != null ? /*#__PURE__*/React.createElement("span", null, value + '/' + max) : null);else if (failed) body = '\u26A0';
  return /*#__PURE__*/React.createElement("span", {
    className: cx(base, failed && base + '--failed', className),
    style: style,
    title: failed ? error ?? 'Generation failed' : state === 'queued' ? 'Queued' : 'Generating'
  }, body);
}
Object.assign(__ds_scope, { JobBadge });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/JobBadge.jsx", error: String((e && e.message) || e) }); }

// components/navigation/Tabs.jsx
try { (() => {
const cx = (...a) => a.filter(Boolean).join(' ');
function Tabs({
  tabs = [],
  value,
  onChange,
  className,
  style
}) {
  return /*#__PURE__*/React.createElement("nav", {
    className: cx('ms-tabs', className),
    style: style
  }, tabs.map(t => {
    const key = typeof t === 'object' ? t.value : t;
    const label = typeof t === 'object' ? t.label : t;
    return /*#__PURE__*/React.createElement("button", {
      key: String(key),
      type: "button",
      className: cx('ms-tab', key === value && 'ms-tab--active'),
      onClick: () => onChange && onChange(key)
    }, label);
  }));
}
Object.assign(__ds_scope, { Tabs });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/navigation/Tabs.jsx", error: String((e && e.message) || e) }); }

// components/overlays/Dialog.jsx
try { (() => {
const cx = (...a) => a.filter(Boolean).join(' ');
function Dialog({
  title,
  message,
  meta,
  size = 'sm',
  nested = false,
  actions,
  onDismiss,
  className,
  style,
  children
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: cx('ms-dialog-overlay', nested && 'ms-dialog-overlay--nested'),
    onClick: e => {
      if (e.target === e.currentTarget && onDismiss) onDismiss();
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: cx('ms-dialog', size !== 'sm' && 'ms-dialog--' + size, className),
    style: style
  }, title ? /*#__PURE__*/React.createElement("h3", {
    className: "ms-dialog__title"
  }, title) : null, message ? /*#__PURE__*/React.createElement("p", {
    className: "ms-dialog__message"
  }, message) : null, meta ? /*#__PURE__*/React.createElement("p", {
    className: "ms-dialog__meta"
  }, meta) : null, children, actions ? /*#__PURE__*/React.createElement("div", {
    className: "ms-dialog__actions"
  }, actions) : null));
}
Object.assign(__ds_scope, { Dialog });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/overlays/Dialog.jsx", error: String((e && e.message) || e) }); }

// components/overlays/Toast.jsx
try { (() => {
const cx = (...a) => a.filter(Boolean).join(' ');
const ICONS = {
  success: 'pi-check',
  warn: 'pi-exclamation-circle',
  info: 'pi-info-circle'
};
function Toast({
  message,
  kind = 'success',
  floating = true,
  className,
  style,
  children
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: cx('ms-toast', 'ms-toast--' + kind, !floating && 'ms-toast--static', className),
    style: style
  }, /*#__PURE__*/React.createElement("i", {
    className: cx('pi', ICONS[kind] ?? ICONS.success),
    "aria-hidden": "true"
  }), /*#__PURE__*/React.createElement("span", null, message ?? children));
}
Object.assign(__ds_scope, { Toast });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/overlays/Toast.jsx", error: String((e && e.message) || e) }); }

// components/storyboard/AddTile.jsx
try { (() => {
const cx = (...a) => a.filter(Boolean).join(' ');
function AddTile({
  label = 'Panel',
  kind = 'panel',
  onClick,
  className,
  style,
  children
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: cx('ms-add-tile', 'ms-add-tile--' + kind, className),
    style: style,
    onClick: onClick
  }, children ?? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("span", {
    className: "ms-add-tile__plus"
  }, "+"), /*#__PURE__*/React.createElement("span", {
    className: "ms-add-tile__label"
  }, label)));
}
Object.assign(__ds_scope, { AddTile });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/storyboard/AddTile.jsx", error: String((e && e.message) || e) }); }

// components/storyboard/BeatPill.jsx
try { (() => {
const cx = (...a) => a.filter(Boolean).join(' ');
function BeatPill({
  index,
  action,
  duration,
  motion,
  isCut = false,
  imageCount = 0,
  dialogCount = 0,
  thumb,
  job,
  selected = false,
  onClick,
  actions,
  className,
  style
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: cx('ms-beat-pill', selected && 'ms-beat-pill--selected', className),
    style: style,
    onClick: onClick
  }, /*#__PURE__*/React.createElement("div", {
    className: "ms-beat-pill__thumb"
  }, thumb ? /*#__PURE__*/React.createElement("img", {
    src: thumb,
    alt: ""
  }) : /*#__PURE__*/React.createElement("div", {
    className: "ms-thumb-empty"
  }), job), /*#__PURE__*/React.createElement("span", {
    className: "ms-beat-pill__index"
  }, '#' + index), imageCount > 0 ? /*#__PURE__*/React.createElement("span", {
    className: "ms-beat-pill__meta",
    title: "Generated images"
  }, '\uD83D\uDDBC' + imageCount) : null, duration != null ? /*#__PURE__*/React.createElement("span", {
    className: "ms-beat-pill__meta"
  }, duration + 's') : null, motion ? /*#__PURE__*/React.createElement("span", {
    className: "ms-beat-pill__motion"
  }, String(motion).replace(/_/g, ' ')) : null, isCut ? /*#__PURE__*/React.createElement("span", {
    className: "ms-beat-pill__cut"
  }, "(cut)") : null, /*#__PURE__*/React.createElement("span", {
    className: "ms-beat-pill__action"
  }, action), dialogCount > 0 ? /*#__PURE__*/React.createElement("span", {
    className: "ms-beat-pill__meta",
    title: "Dialog lines"
  }, '\uD83D\uDCAC' + dialogCount) : null, actions);
}
Object.assign(__ds_scope, { BeatPill });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/storyboard/BeatPill.jsx", error: String((e && e.message) || e) }); }

// components/storyboard/BoardRow.jsx
try { (() => {
const cx = (...a) => a.filter(Boolean).join(' ');
function BoardRow({
  name,
  meta,
  actions,
  className,
  style
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: cx('ms-board-row', className),
    style: style
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "ms-board-row__name"
  }, name), meta ? /*#__PURE__*/React.createElement("div", {
    className: "ms-board-row__meta"
  }, meta) : null), actions ? /*#__PURE__*/React.createElement("div", {
    className: "ms-board-row__actions"
  }, actions) : null);
}
Object.assign(__ds_scope, { BoardRow });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/storyboard/BoardRow.jsx", error: String((e && e.message) || e) }); }

// components/storyboard/CandidateTile.jsx
try { (() => {
const cx = (...a) => a.filter(Boolean).join(' ');
function CandidateTile({
  src,
  selected = false,
  title,
  onClick,
  onDoubleClick,
  onExpand,
  className,
  style
}) {
  return /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: cx('ms-candidate', selected && 'ms-candidate--selected', className),
    style: style,
    title: title,
    onClick: onClick,
    onDoubleClick: onDoubleClick
  }, /*#__PURE__*/React.createElement("img", {
    src: src,
    alt: ""
  }), selected ? /*#__PURE__*/React.createElement("span", {
    className: "ms-candidate__check"
  }, '\u2713') : null, onExpand ? /*#__PURE__*/React.createElement("span", {
    className: "ms-candidate__expand",
    title: "View full size",
    onClick: e => {
      e.stopPropagation();
      onExpand(e);
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "pi pi-search-plus",
    "aria-hidden": "true"
  })) : null);
}
Object.assign(__ds_scope, { CandidateTile });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/storyboard/CandidateTile.jsx", error: String((e && e.message) || e) }); }

// components/storyboard/LoraListEditor.jsx
try { (() => {
function LoraListEditor({
  label,
  entries = [],
  options = [],
  onChange,
  listId = 'ms-lora-options'
}) {
  const [drafting, setDrafting] = React.useState(false);
  const emit = next => {
    if (onChange) onChange(next);
  };
  return /*#__PURE__*/React.createElement("div", {
    className: "ms-lora"
  }, /*#__PURE__*/React.createElement("label", {
    className: "ms-label"
  }, label), entries.map((entry, i) => /*#__PURE__*/React.createElement("div", {
    className: "ms-lora__row",
    key: i + '-' + entry.name
  }, /*#__PURE__*/React.createElement("input", {
    className: "ms-control ms-lora__name",
    type: "text",
    list: listId,
    defaultValue: entry.name,
    onChange: e => {
      const name = e.target.value.trim();
      if (!name) return;
      emit(entries.map((en, j) => j === i ? {
        ...en,
        name
      } : en));
    }
  }), /*#__PURE__*/React.createElement("input", {
    className: "ms-control ms-lora__strength",
    type: "number",
    step: "0.05",
    title: "Strength",
    defaultValue: entry.strength,
    onChange: e => {
      const strength = Number(e.target.value);
      if (!Number.isFinite(strength)) return;
      emit(entries.map((en, j) => j === i ? {
        ...en,
        strength
      } : en));
    }
  }), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "ms-lora__remove",
    title: "Remove",
    onClick: () => emit(entries.filter((_, j) => j !== i))
  }, '\u2715'))), drafting ? /*#__PURE__*/React.createElement("div", {
    className: "ms-lora__row"
  }, /*#__PURE__*/React.createElement("input", {
    className: "ms-control ms-lora__name",
    type: "text",
    list: listId,
    placeholder: "lora file\u2026",
    autoFocus: true,
    onChange: e => {
      const name = e.target.value.trim();
      if (!name) return;
      setDrafting(false);
      emit([...entries, {
        name,
        strength: 1.0
      }]);
    },
    onKeyDown: e => {
      if (e.key === 'Escape') setDrafting(false);
    }
  }), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "ms-lora__remove",
    title: "Cancel",
    onClick: () => setDrafting(false)
  }, '\u2715')) : /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "ms-btn ms-btn--dashed ms-btn--sm",
    style: {
      alignSelf: 'flex-start'
    },
    onClick: () => setDrafting(true)
  }, "+ Add LoRA"), /*#__PURE__*/React.createElement("datalist", {
    id: listId
  }, options.map(o => /*#__PURE__*/React.createElement("option", {
    key: o,
    value: o
  }))));
}
Object.assign(__ds_scope, { LoraListEditor });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/storyboard/LoraListEditor.jsx", error: String((e && e.message) || e) }); }

// components/storyboard/PanelTile.jsx
try { (() => {
const cx = (...a) => a.filter(Boolean).join(' ');
function PanelTile({
  src,
  dimmed = false,
  caption,
  active = false,
  locked = false,
  videoCount = 0,
  job,
  onClick,
  onDoubleClick,
  onDelete,
  className,
  style,
  children
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: cx('ms-panel-tile', active && 'ms-panel-tile--active', className),
    style: style,
    onClick: onClick,
    onDoubleClick: onDoubleClick
  }, onDelete ? /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "ms-iconbtn ms-iconbtn--scrim ms-panel-tile__delete",
    title: "Delete panel",
    onClick: e => {
      e.stopPropagation();
      onDelete(e);
    }
  }, "\xD7") : null, /*#__PURE__*/React.createElement("div", {
    className: "ms-panel-tile__thumb"
  }, src ? /*#__PURE__*/React.createElement("img", {
    src: src,
    alt: "",
    className: dimmed ? 'ms-dimmed' : undefined
  }) : /*#__PURE__*/React.createElement("div", {
    className: "ms-panel-tile__thumb-empty"
  }), locked ? /*#__PURE__*/React.createElement("span", {
    className: "ms-panel-tile__lock",
    title: "Prompt locked"
  }, '\uD83D\uDD12') : null, videoCount > 0 ? /*#__PURE__*/React.createElement("span", {
    className: "ms-panel-tile__video",
    title: videoCount + ' video take(s) — double-click to play'
  }, '\uD83C\uDFAC') : null, job), children ?? /*#__PURE__*/React.createElement("div", {
    className: "ms-panel-tile__caption"
  }, caption));
}
Object.assign(__ds_scope, { PanelTile });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/storyboard/PanelTile.jsx", error: String((e && e.message) || e) }); }

// components/storyboard/SceneCard.jsx
try { (() => {
const cx = (...a) => a.filter(Boolean).join(' ');
function SceneCard({
  name,
  subtitle,
  setting,
  thumbs = [],
  active = false,
  actions,
  onClick,
  className,
  style
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: cx('ms-scene-card', active && 'ms-scene-card--active', className),
    style: style,
    onClick: onClick
  }, actions ? /*#__PURE__*/React.createElement("div", {
    className: "ms-scene-card__corner"
  }, actions) : null, /*#__PURE__*/React.createElement("div", {
    className: "ms-scene-card__name"
  }, name), /*#__PURE__*/React.createElement("div", {
    className: "ms-scene-card__subtitle"
  }, subtitle || '\u2014'), setting ? /*#__PURE__*/React.createElement("div", {
    className: "ms-scene-card__setting",
    title: setting
  }, setting) : null, /*#__PURE__*/React.createElement("div", {
    className: "ms-scene-card__thumbs"
  }, thumbs.slice(0, 6).map((src, i) => /*#__PURE__*/React.createElement("div", {
    className: "ms-thumb-24",
    key: i
  }, src ? /*#__PURE__*/React.createElement("img", {
    src: src,
    alt: ""
  }) : /*#__PURE__*/React.createElement("div", {
    className: "ms-thumb-empty"
  })))));
}
Object.assign(__ds_scope, { SceneCard });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/storyboard/SceneCard.jsx", error: String((e && e.message) || e) }); }

// components/storyboard/ScriptBlock.jsx
try { (() => {
const cx = (...a) => a.filter(Boolean).join(' ');
function ScriptBlock({
  header,
  beats,
  text,
  selectedBeatId,
  onSelectBeat,
  className,
  style
}) {
  if (text != null) {
    return /*#__PURE__*/React.createElement("pre", {
      className: cx('ms-script', className),
      style: style
    }, text);
  }
  return /*#__PURE__*/React.createElement("div", {
    className: cx('ms-script', className),
    style: style
  }, header ? /*#__PURE__*/React.createElement("div", {
    className: "ms-script__header"
  }, header) : null, !beats || beats.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "ms-script__beat"
  }, "(no beats)") : beats.map(b => /*#__PURE__*/React.createElement("div", {
    key: b.beatId,
    className: cx('ms-script__beat', b.beatId === selectedBeatId && 'ms-script__beat--selected'),
    onClick: () => onSelectBeat && onSelectBeat(b.beatId)
  }, b.text)));
}
Object.assign(__ds_scope, { ScriptBlock });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/storyboard/ScriptBlock.jsx", error: String((e && e.message) || e) }); }

// components/storyboard/SubjectChip.jsx
try { (() => {
const cx = (...a) => a.filter(Boolean).join(' ');
function SubjectChip({
  name,
  primary = false,
  title,
  onClick,
  className,
  style
}) {
  return /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: cx('ms-subject-chip', primary && 'ms-subject-chip--primary', className),
    style: style,
    title: title ?? (primary ? 'Primary subject' : 'Click to make primary'),
    onClick: onClick
  }, primary ? /*#__PURE__*/React.createElement("span", {
    className: "ms-subject-chip__star"
  }, '\u2605') : null, name);
}
Object.assign(__ds_scope, { SubjectChip });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/storyboard/SubjectChip.jsx", error: String((e && e.message) || e) }); }

// components/storyboard/VideoTakeTile.jsx
try { (() => {
const cx = (...a) => a.filter(Boolean).join(' ');
function VideoTakeTile({
  src,
  title,
  onPlay,
  onDelete,
  className,
  style
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: cx('ms-take', className),
    style: style,
    title: title,
    onDoubleClick: onPlay
  }, /*#__PURE__*/React.createElement("img", {
    src: src,
    alt: ""
  }), /*#__PURE__*/React.createElement("span", {
    className: "ms-take__play",
    title: "Play",
    onClick: e => {
      e.stopPropagation();
      if (onPlay) onPlay(e);
    }
  }, '\u25B6'), onDelete ? /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "ms-iconbtn ms-iconbtn--scrim ms-take__delete",
    title: "Delete take",
    onClick: e => {
      e.stopPropagation();
      onDelete(e);
    }
  }, "\xD7") : null);
}
Object.assign(__ds_scope, { VideoTakeTile });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/storyboard/VideoTakeTile.jsx", error: String((e && e.message) || e) }); }

// redesign/RedesignPipeline.jsx
try { (() => {
const NS = window.MetascanDesignSystem_f2fbbd;
const {
  Button,
  IconButton,
  Chip,
  Field,
  TextInput,
  Select,
  Textarea,
  Checkbox,
  SubjectChip,
  CandidateTile,
  VideoTakeTile,
  JobBadge,
  ScriptBlock
} = NS;
const CAP = 15;
const keeperOf = b => b.images.find(i => i.id === b.selected_image_id) || b.images[0] || null;
const shotSecs = p => p.beats.reduce((s, b) => s + b.duration_s, 0);
const STAGES = [{
  key: 'outline',
  label: 'Outline',
  action: 'Generate outline'
}, {
  key: 'scenes',
  label: 'Scenes',
  action: 'Build scenes'
}, {
  key: 'shots',
  label: 'Shots',
  action: 'Build shots'
}, {
  key: 'beats',
  label: 'Beats',
  action: 'Build beats'
}, {
  key: 'prompts',
  label: 'Prompts',
  action: 'Synthesize'
}, {
  key: 'stills',
  label: 'Stills',
  action: 'Generate all'
}, {
  key: 'video',
  label: 'Video',
  action: 'Compile + render'
}];
function StageBar({
  active,
  onSelect,
  counts
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'stretch',
      gap: 0,
      borderBottom: '1px solid var(--surface-border)',
      background: 'var(--surface-section)',
      flexShrink: 0
    }
  }, STAGES.map((s, i) => {
    const c = counts[s.key];
    const on = active === s.key;
    const dot = c.done === 0 ? 'var(--text-color-secondary)' : c.done < c.total ? 'var(--warn)' : 'var(--success)';
    return /*#__PURE__*/React.createElement("button", {
      key: s.key,
      type: "button",
      onClick: () => onSelect(s.key),
      style: {
        flex: 1,
        minWidth: 0,
        display: 'flex',
        flexDirection: 'column',
        gap: 3,
        alignItems: 'flex-start',
        padding: '9px 14px',
        cursor: 'pointer',
        fontFamily: 'inherit',
        textAlign: 'left',
        border: 'none',
        borderLeft: i === 0 ? 'none' : '1px solid var(--surface-border)',
        borderBottom: '2px solid ' + (on ? 'var(--primary-color)' : 'transparent'),
        marginBottom: -1,
        background: on ? 'var(--primary-tint-10)' : 'transparent',
        color: 'var(--text-color)'
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        fontSize: 12,
        fontWeight: 600
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        width: 7,
        height: 7,
        borderRadius: '50%',
        background: dot,
        flexShrink: 0
      }
    }), s.label), /*#__PURE__*/React.createElement("span", {
      style: {
        fontSize: 11,
        color: 'var(--text-color-secondary)',
        fontVariantNumeric: 'tabular-nums'
      }
    }, c.total === 0 ? 'not started' : c.done + '/' + c.total + ' ' + c.unit));
  }));
}
function Th({
  children,
  w
}) {
  return /*#__PURE__*/React.createElement("th", {
    style: {
      width: w,
      textAlign: 'left',
      padding: '6px 8px',
      fontSize: 10,
      fontWeight: 600,
      textTransform: 'uppercase',
      letterSpacing: '.4px',
      color: 'var(--text-color-secondary)',
      borderBottom: '1px solid var(--surface-border)',
      position: 'sticky',
      top: 0,
      background: 'var(--surface-ground)'
    }
  }, children);
}
function Td({
  children,
  style
}) {
  return /*#__PURE__*/React.createElement("td", {
    style: {
      padding: '4px 8px',
      borderBottom: '1px solid var(--surface-border)',
      fontSize: 12,
      verticalAlign: 'middle',
      ...style
    }
  }, children);
}
function BeatsTable({
  tree,
  onPatchBeat,
  jobs
}) {
  const SB = window.SB;
  const name = id => (tree.subjects.find(s => s.id === id) || {}).name || '#' + id;
  const rows = tree.scenes.flatMap(s => s.panels.flatMap((p, pi) => p.beats.map((b, bi) => ({
    s,
    p,
    pi,
    b,
    bi
  }))));
  return /*#__PURE__*/React.createElement("table", {
    style: {
      width: '100%',
      borderCollapse: 'collapse'
    }
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement(Th, {
    w: "34"
  }), /*#__PURE__*/React.createElement(Th, {
    w: "190"
  }, "Scene \xB7 shot \xB7 beat"), /*#__PURE__*/React.createElement(Th, null, "Action"), /*#__PURE__*/React.createElement(Th, {
    w: "88"
  }, "Size"), /*#__PURE__*/React.createElement(Th, {
    w: "88"
  }, "Angle"), /*#__PURE__*/React.createElement(Th, {
    w: "88"
  }, "Lens"), /*#__PURE__*/React.createElement(Th, {
    w: "150"
  }, "Move"), /*#__PURE__*/React.createElement(Th, {
    w: "84"
  }, "Duration"), /*#__PURE__*/React.createElement(Th, {
    w: "60"
  }, "Cut"), /*#__PURE__*/React.createElement(Th, {
    w: "140"
  }, "Cast"))), /*#__PURE__*/React.createElement("tbody", null, rows.map(({
    s,
    p,
    pi,
    b,
    bi
  }) => {
    const k = keeperOf(b);
    const job = jobs[b.id];
    const over = shotSecs(p) > CAP;
    return /*#__PURE__*/React.createElement("tr", {
      key: b.id,
      style: {
        background: bi === 0 ? 'var(--surface-card)' : 'transparent'
      }
    }, /*#__PURE__*/React.createElement(Td, null, /*#__PURE__*/React.createElement("span", {
      style: {
        position: 'relative',
        display: 'block',
        width: 26,
        height: 26,
        borderRadius: 4,
        overflow: 'hidden',
        background: 'var(--surface-ground)'
      }
    }, k ? /*#__PURE__*/React.createElement("img", {
      src: k.file_path,
      alt: "",
      style: {
        width: '100%',
        height: '100%',
        objectFit: 'cover',
        display: 'block'
      }
    }) : /*#__PURE__*/React.createElement("span", {
      className: "ms-thumb-empty"
    }), job ? /*#__PURE__*/React.createElement(JobBadge, {
      state: job.state,
      layout: "fill",
      error: job.error
    }) : null)), /*#__PURE__*/React.createElement(Td, null, /*#__PURE__*/React.createElement("span", {
      style: {
        color: 'var(--text-color-secondary)'
      }
    }, bi === 0 ? s.name + ' · ' : ''), /*#__PURE__*/React.createElement("span", {
      style: {
        fontWeight: bi === 0 ? 600 : 400
      }
    }, bi === 0 ? 'shot ' + (pi + 1) : ''), /*#__PURE__*/React.createElement("span", {
      style: {
        color: 'var(--text-color-secondary)'
      }
    }, bi === 0 ? ' · ' : '', "beat ", bi + 1)), /*#__PURE__*/React.createElement(Td, null, /*#__PURE__*/React.createElement(TextInput, {
      value: b.action,
      onChange: e => onPatchBeat(b.id, {
        action: e.target.value
      })
    })), /*#__PURE__*/React.createElement(Td, null, /*#__PURE__*/React.createElement(Select, {
      options: SB.SHOT_SIZES,
      value: b.shot_size ?? '',
      onChange: e => onPatchBeat(b.id, {
        shot_size: e.target.value || null
      })
    })), /*#__PURE__*/React.createElement(Td, null, /*#__PURE__*/React.createElement(Select, {
      options: SB.ANGLES,
      value: b.angle ?? '',
      onChange: e => onPatchBeat(b.id, {
        angle: e.target.value || null
      })
    })), /*#__PURE__*/React.createElement(Td, null, /*#__PURE__*/React.createElement(Select, {
      options: SB.LENSES,
      value: b.lens ?? '',
      onChange: e => onPatchBeat(b.id, {
        lens: e.target.value || null
      })
    })), /*#__PURE__*/React.createElement(Td, null, /*#__PURE__*/React.createElement(Select, {
      options: SB.CAMERA_MOTIONS,
      value: b.camera_motion ?? '',
      onChange: e => onPatchBeat(b.id, {
        camera_motion: e.target.value || null
      })
    })), /*#__PURE__*/React.createElement(Td, null, /*#__PURE__*/React.createElement("span", {
      style: {
        display: 'flex',
        alignItems: 'center',
        gap: 4
      }
    }, /*#__PURE__*/React.createElement(TextInput, {
      type: "number",
      step: "0.5",
      min: "0.5",
      value: b.duration_s,
      onChange: e => onPatchBeat(b.id, {
        duration_s: Number(e.target.value)
      })
    }), bi === 0 && over ? /*#__PURE__*/React.createElement("span", {
      title: "Shot exceeds the H3 15s clip cap",
      style: {
        color: 'var(--warn)'
      }
    }, "\u26A0") : null)), /*#__PURE__*/React.createElement(Td, null, /*#__PURE__*/React.createElement(Button, {
      size: "xs",
      active: !!b.is_cut,
      onClick: () => onPatchBeat(b.id, {
        is_cut: b.is_cut ? 0 : 1
      })
    }, "Cut")), /*#__PURE__*/React.createElement(Td, null, /*#__PURE__*/React.createElement("span", {
      style: {
        display: 'flex',
        flexWrap: 'wrap',
        gap: 4
      }
    }, b.subject_ids.length ? b.subject_ids.map((sid, i) => /*#__PURE__*/React.createElement(SubjectChip, {
      key: sid,
      name: name(sid),
      primary: i === 0,
      onClick: () => onPatchBeat(b.id, {
        subject_ids: [sid].concat(b.subject_ids.filter(x => x !== sid))
      })
    })) : /*#__PURE__*/React.createElement("span", {
      className: "ms-hint"
    }, "\u2014"))));
  })));
}
function PromptsView({
  tree,
  onPatchBeat
}) {
  const rows = tree.scenes.flatMap(s => s.panels.flatMap((p, pi) => p.beats.map((b, bi) => ({
    s,
    p,
    pi,
    b,
    bi
  }))));
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 10
    }
  }, rows.map(({
    s,
    pi,
    b,
    bi
  }) => {
    const status = b.prompt_locked === 1 ? '🔒 edited' : b.prompt_source === 'llm' ? 'synthesized' : b.prompt_source === 'brief' ? 'brief fallback' : 'not synthesized';
    return /*#__PURE__*/React.createElement("div", {
      key: b.id,
      style: {
        display: 'flex',
        gap: 14,
        padding: 12,
        border: '1px solid var(--surface-border)',
        borderRadius: 8,
        background: 'var(--surface-card)'
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        flex: '0 0 230px',
        minWidth: 0
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 11,
        color: 'var(--text-color-secondary)'
      }
    }, s.name, " \xB7 shot ", pi + 1, " \xB7 beat ", bi + 1), /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 12,
        marginTop: 2
      }
    }, b.action), /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 11,
        color: 'var(--text-color-secondary)',
        marginTop: 6
      }
    }, (b.shot_size || '—') + ' · ' + (b.angle || '—') + ' · ' + (b.lens || '—')), /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'flex',
        gap: 6,
        marginTop: 8
      }
    }, /*#__PURE__*/React.createElement(Button, {
      size: "xs"
    }, "Re-synth"), b.prompt_locked === 1 ? /*#__PURE__*/React.createElement(Button, {
      variant: "link",
      onClick: () => onPatchBeat(b.id, {
        prompt_locked: 0
      })
    }, "Unlock") : null)), /*#__PURE__*/React.createElement(Field, {
      label: "Prompt",
      aside: /*#__PURE__*/React.createElement("span", {
        className: "ms-hint"
      }, status),
      style: {
        flex: 1,
        minWidth: 0
      }
    }, /*#__PURE__*/React.createElement(Textarea, {
      rows: 3,
      mono: true,
      value: b.prompt ?? '',
      placeholder: "No prompt synthesized yet.",
      onChange: e => onPatchBeat(b.id, {
        prompt: e.target.value,
        prompt_source: 'user',
        prompt_locked: 1
      })
    })));
  }));
}
function StillsView({
  tree,
  onPatchBeat
}) {
  const rows = tree.scenes.flatMap(s => s.panels.flatMap((p, pi) => p.beats.map((b, bi) => ({
    s,
    p,
    pi,
    b,
    bi
  }))));
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 10
    }
  }, rows.map(({
    s,
    pi,
    b,
    bi
  }) => /*#__PURE__*/React.createElement("div", {
    key: b.id,
    style: {
      display: 'flex',
      gap: 14,
      alignItems: 'flex-start',
      padding: 12,
      border: '1px solid var(--surface-border)',
      borderRadius: 8,
      background: 'var(--surface-card)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: '0 0 230px',
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-color-secondary)'
    }
  }, s.name, " \xB7 shot ", pi + 1, " \xB7 beat ", bi + 1), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      marginTop: 2
    }
  }, b.action), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 6,
      marginTop: 8
    }
  }, /*#__PURE__*/React.createElement(Button, {
    size: "xs"
  }, "Reroll"), /*#__PURE__*/React.createElement("span", {
    className: "ms-hint",
    style: {
      alignSelf: 'center'
    }
  }, b.images.length ? b.images.length + ' candidates · ' + (b.selected_image_id ? 'keeper picked' : 'no keeper') : 'none yet'))), /*#__PURE__*/React.createElement("div", {
    className: "ms-candidates-row",
    style: {
      flex: 1,
      minWidth: 0
    }
  }, b.images.map(img => /*#__PURE__*/React.createElement(CandidateTile, {
    key: img.id,
    src: img.file_path,
    selected: img.id === b.selected_image_id,
    title: 'seed ' + (img.seed ?? '—') + ' · variant ' + img.variant_index,
    onClick: () => onPatchBeat(b.id, {
      selected_image_id: b.selected_image_id === img.id ? null : img.id
    }),
    onExpand: () => {}
  })), b.images.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "ms-hint",
    style: {
      padding: '8px 0',
      fontSize: 12
    }
  }, "No candidates yet.") : null))));
}
function VideoView({
  tree,
  onPatchPanel
}) {
  const rows = tree.scenes.flatMap(s => s.panels.map((p, pi) => ({
    s,
    p,
    pi
  })));
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 10
    }
  }, rows.map(({
    s,
    p,
    pi
  }) => {
    const secs = shotSecs(p);
    return /*#__PURE__*/React.createElement("div", {
      key: p.id,
      style: {
        display: 'flex',
        gap: 14,
        alignItems: 'flex-start',
        padding: 12,
        border: '1px solid var(--surface-border)',
        borderRadius: 8,
        background: 'var(--surface-card)'
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        flex: '0 0 210px',
        minWidth: 0
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 11,
        color: 'var(--text-color-secondary)'
      }
    }, s.name), /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 13,
        fontWeight: 600
      }
    }, "Shot ", pi + 1), /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 11,
        color: secs > CAP ? 'var(--warn)' : 'var(--text-color-secondary)',
        fontVariantNumeric: 'tabular-nums'
      }
    }, secs.toFixed(1), "s / ", CAP, "s"), /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'flex',
        gap: 6,
        marginTop: 8,
        flexWrap: 'wrap'
      }
    }, /*#__PURE__*/React.createElement(Button, {
      size: "xs"
    }, "Compile"), /*#__PURE__*/React.createElement(Button, {
      size: "xs",
      disabled: !p.video_prompt
    }, "Render"))), /*#__PURE__*/React.createElement(Field, {
      label: "Compiled video prompt",
      style: {
        flex: 1,
        minWidth: 0
      },
      aside: /*#__PURE__*/React.createElement("span", {
        className: "ms-hint"
      }, p.video_prompt_source ?? '—')
    }, /*#__PURE__*/React.createElement(Textarea, {
      rows: 4,
      mono: true,
      value: p.video_prompt ?? '',
      placeholder: "No video prompt compiled yet.",
      onChange: e => onPatchPanel(p.id, {
        video_prompt: e.target.value,
        video_prompt_source: 'user'
      })
    }), p.video_prompt_warnings.length ? /*#__PURE__*/React.createElement("ul", {
      style: {
        margin: '4px 0 0',
        padding: '0 0 0 16px',
        listStyle: 'disc',
        color: 'var(--warn)',
        fontSize: 11,
        lineHeight: 1.5
      }
    }, p.video_prompt_warnings.map((w, i) => /*#__PURE__*/React.createElement("li", {
      key: i
    }, w))) : null), /*#__PURE__*/React.createElement(Field, {
      label: 'Takes (' + p.videos.length + ')',
      style: {
        flex: '0 0 288px'
      }
    }, p.videos.length ? /*#__PURE__*/React.createElement("div", {
      className: "ms-candidates-row"
    }, p.videos.map(v => /*#__PURE__*/React.createElement(VideoTakeTile, {
      key: v.id,
      src: v.file_path,
      title: 'seed ' + v.seed + ' · take ' + (v.variant_index + 1),
      onDelete: () => {}
    }))) : /*#__PURE__*/React.createElement("p", {
      className: "ms-hint",
      style: {
        fontSize: 12
      }
    }, "Not rendered yet.")));
  }));
}
function OutlineView({
  tree
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 16,
      alignItems: 'flex-start',
      maxWidth: 1100
    }
  }, /*#__PURE__*/React.createElement(Field, {
    label: "Premise",
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement(Textarea, {
    rows: 8,
    defaultValue: tree.source_text
  }), /*#__PURE__*/React.createElement(Button, {
    variant: "primary",
    size: "lg",
    style: {
      alignSelf: 'flex-start',
      marginTop: 10
    }
  }, "Generate outline")), /*#__PURE__*/React.createElement(Field, {
    label: "Outline",
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement(Textarea, {
    rows: 12,
    mono: true,
    defaultValue: tree.outline
  })));
}
function ScenesView({
  tree
}) {
  const {
    SceneCard,
    AddTile
  } = NS;
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexWrap: 'wrap',
      gap: 12
    }
  }, tree.scenes.map(s => /*#__PURE__*/React.createElement(SceneCard, {
    key: s.id,
    name: s.name,
    subtitle: s.subtitle,
    setting: s.setting,
    thumbs: s.panels.slice(0, 6).map(p => {
      const k = p.beats[0] && keeperOf(p.beats[0]);
      return k ? k.file_path : null;
    }),
    actions: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(IconButton, {
      variant: "corner",
      glyph: "\u270E",
      title: "Edit scene",
      style: {
        fontSize: 10
      }
    }), /*#__PURE__*/React.createElement(IconButton, {
      variant: "corner",
      glyph: "\xD7",
      destructive: true,
      title: "Delete scene"
    }))
  })), /*#__PURE__*/React.createElement(AddTile, {
    kind: "scene",
    label: "Scene"
  }));
}
function ShotsView({
  tree,
  jobs
}) {
  const {
    PanelTile
  } = NS;
  return /*#__PURE__*/React.createElement("div", null, tree.scenes.map(s => /*#__PURE__*/React.createElement("section", {
    key: s.id,
    style: {
      marginBottom: 22
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'baseline',
      gap: 8,
      marginBottom: 8
    }
  }, /*#__PURE__*/React.createElement("h4", {
    style: {
      margin: 0,
      fontSize: 13,
      fontWeight: 600
    }
  }, s.name), /*#__PURE__*/React.createElement("span", {
    className: "ms-hint"
  }, s.subtitle || '—', " \xB7 ", s.panels.length, " shots")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))',
      gap: 14
    }
  }, s.panels.map(p => {
    const k = p.beats[0] && keeperOf(p.beats[0]);
    const job = p.beats.map(b => jobs[b.id]).find(Boolean);
    return /*#__PURE__*/React.createElement(PanelTile, {
      key: p.id,
      src: k ? k.file_path : null,
      caption: p.action,
      videoCount: p.videos.length,
      locked: p.video_prompt_locked === 1,
      job: job ? /*#__PURE__*/React.createElement(JobBadge, {
        state: job.state,
        error: job.error
      }) : null,
      onDelete: () => {}
    });
  }), /*#__PURE__*/React.createElement(NS.AddTile, {
    kind: "panel",
    label: "Shot"
  })))));
}
function RedesignPipeline() {
  const SB = window.SB;
  const [tree, setTree] = React.useState(SB.tree);
  const [stage, setStage] = React.useState('beats');
  const jobs = {
    105: {
      state: 'running'
    },
    202: {
      state: 'queued'
    },
    301: {
      state: 'failed',
      error: 'preset 3: node 12 missing input'
    }
  };
  const panels = tree.scenes.flatMap(s => s.panels);
  const beats = panels.flatMap(p => p.beats);
  const mutate = fn => setTree(t => {
    const n = JSON.parse(JSON.stringify(t));
    fn(n);
    return n;
  });
  const patchBeat = (id, body) => mutate(t => {
    const b = t.scenes.flatMap(s => s.panels).flatMap(p => p.beats).find(x => x.id === id);
    if (b) Object.assign(b, body);
  });
  const patchPanel = (id, body) => mutate(t => {
    const p = t.scenes.flatMap(s => s.panels).find(x => x.id === id);
    if (p) Object.assign(p, body);
  });
  const counts = {
    outline: {
      done: tree.outline ? 1 : 0,
      total: 1,
      unit: 'written'
    },
    scenes: {
      done: tree.scenes.length,
      total: tree.scenes.length,
      unit: 'scenes'
    },
    shots: {
      done: panels.length,
      total: panels.length,
      unit: 'shots'
    },
    beats: {
      done: beats.length,
      total: beats.length,
      unit: 'beats'
    },
    prompts: {
      done: beats.filter(b => b.prompt).length,
      total: beats.length,
      unit: 'synthesized'
    },
    stills: {
      done: beats.filter(b => b.selected_image_id).length,
      total: beats.length,
      unit: 'keepers'
    },
    video: {
      done: panels.filter(p => p.videos.length).length,
      total: panels.length,
      unit: 'rendered'
    }
  };
  const current = STAGES.find(s => s.key === stage);
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      minHeight: 0
    }
  }, /*#__PURE__*/React.createElement("header", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 12,
      padding: '10px 20px',
      borderBottom: '1px solid var(--surface-border)',
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement("a", {
    href: "#",
    onClick: e => e.preventDefault(),
    style: {
      color: 'var(--text-color-secondary)',
      fontSize: 13
    }
  }, "\u2190 Library"), /*#__PURE__*/React.createElement("h2", {
    style: {
      margin: 0,
      fontSize: 16,
      fontWeight: 600
    }
  }, tree.name), /*#__PURE__*/React.createElement(Chip, {
    tone: "primary"
  }, "MiniMax H3 \xB7 ", tree.video_mode), /*#__PURE__*/React.createElement("div", {
    style: {
      marginLeft: 'auto',
      display: 'flex',
      alignItems: 'center',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement(Button, {
    variant: "quiet",
    icon: "pi-file-import"
  }, "Import text"), /*#__PURE__*/React.createElement(IconButton, {
    variant: "outline",
    size: "lg",
    icon: "pi-cog",
    title: "Storyboard settings"
  }))), /*#__PURE__*/React.createElement(StageBar, {
    active: stage,
    onSelect: setStage,
    counts: counts
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 12,
      padding: '10px 20px',
      borderBottom: '1px solid var(--surface-border)',
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement("h3", {
    style: {
      margin: 0,
      fontSize: 14,
      fontWeight: 600
    }
  }, current.label), /*#__PURE__*/React.createElement("span", {
    className: "ms-hint"
  }, stage === 'beats' ? 'Every beat on the board — edit framing, timing and cast in place.' : stage === 'prompts' ? 'Still prompts, per beat. Locked prompts survive re-synthesis.' : stage === 'stills' ? 'Pick one keeper per beat. Non-keepers stay hidden in the library.' : stage === 'video' ? 'One compiled prompt and one clip per shot.' : stage === 'shots' ? 'Shots grouped by scene.' : stage === 'scenes' ? 'Scenes in order, with their settings.' : 'Write a premise, then let the model draft the outline.'), /*#__PURE__*/React.createElement("div", {
    style: {
      marginLeft: 'auto',
      display: 'flex',
      gap: 8
    }
  }, /*#__PURE__*/React.createElement(Button, {
    size: "md"
  }, "Re-run this stage"), /*#__PURE__*/React.createElement(Button, {
    variant: "primary",
    size: "md",
    icon: "pi-play"
  }, current.action))), /*#__PURE__*/React.createElement("main", {
    style: {
      flex: 1,
      minHeight: 0,
      overflow: 'auto',
      padding: '14px 20px 40px'
    }
  }, stage === 'outline' ? /*#__PURE__*/React.createElement(OutlineView, {
    tree: tree
  }) : stage === 'scenes' ? /*#__PURE__*/React.createElement(ScenesView, {
    tree: tree
  }) : stage === 'shots' ? /*#__PURE__*/React.createElement(ShotsView, {
    tree: tree,
    jobs: jobs
  }) : stage === 'beats' ? /*#__PURE__*/React.createElement(BeatsTable, {
    tree: tree,
    jobs: jobs,
    onPatchBeat: patchBeat
  }) : stage === 'prompts' ? /*#__PURE__*/React.createElement(PromptsView, {
    tree: tree,
    onPatchBeat: patchBeat
  }) : stage === 'stills' ? /*#__PURE__*/React.createElement(StillsView, {
    tree: tree,
    onPatchBeat: patchBeat
  }) : /*#__PURE__*/React.createElement(VideoView, {
    tree: tree,
    onPatchPanel: patchPanel
  })));
}
Object.assign(window, {
  RedesignPipeline
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "redesign/RedesignPipeline.jsx", error: String((e && e.message) || e) }); }

// redesign/RedesignTimeline.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const NS = window.MetascanDesignSystem_f2fbbd;
const {
  Button,
  IconButton,
  Chip,
  Field,
  TextInput,
  Select,
  Textarea,
  Checkbox,
  SubjectChip,
  CandidateTile,
  VideoTakeTile,
  JobBadge,
  LoraListEditor
} = NS;
const CAP = 15;
const keeperOf = b => b.images.find(i => i.id === b.selected_image_id) || b.images[0] || null;
const shotSecs = p => p.beats.reduce((s, b) => s + b.duration_s, 0);
function BeatSegment({
  beat,
  pct,
  selected,
  onClick,
  job
}) {
  const k = keeperOf(beat);
  return /*#__PURE__*/React.createElement("button", {
    type: "button",
    onClick: onClick,
    title: beat.action,
    style: {
      position: 'relative',
      width: pct + '%',
      minWidth: 34,
      height: 62,
      padding: 0,
      cursor: 'pointer',
      overflow: 'hidden',
      flexShrink: 0,
      border: '2px solid ' + (selected ? 'var(--primary-color)' : 'transparent'),
      borderRadius: 6,
      background: k ? 'var(--surface-ground)' : 'var(--surface-card)',
      borderLeft: beat.is_cut ? '3px solid var(--warn)' : undefined
    }
  }, k ? /*#__PURE__*/React.createElement("img", {
    src: k.file_path,
    alt: "",
    style: {
      width: '100%',
      height: '100%',
      objectFit: 'cover',
      display: 'block',
      opacity: beat.selected_image_id ? 1 : 0.45
    }
  }) : /*#__PURE__*/React.createElement("span", {
    style: {
      position: 'absolute',
      inset: 3,
      border: '1px dashed var(--surface-border)',
      borderRadius: 3
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      position: 'absolute',
      bottom: 0,
      left: 0,
      right: 0,
      padding: '2px 4px',
      background: 'linear-gradient(transparent, rgba(0,0,0,0.7))',
      color: '#fff',
      fontSize: 10,
      textAlign: 'left',
      fontVariantNumeric: 'tabular-nums'
    }
  }, beat.duration_s.toFixed(1), "s"), job ? /*#__PURE__*/React.createElement(JobBadge, {
    state: job.state,
    layout: "fill",
    error: job.error
  }) : null);
}
function ShotCard({
  panel,
  index,
  selectedBeatId,
  onSelectBeat,
  onSelectShot,
  active,
  jobs
}) {
  const secs = shotSecs(panel);
  const over = secs > CAP;
  const width = Math.max(96, Math.min(1, secs / CAP) * 240);
  return /*#__PURE__*/React.createElement("div", {
    onClick: onSelectShot,
    style: {
      flexShrink: 0,
      width: width + 40,
      padding: '6px 6px 5px',
      borderRadius: 8,
      cursor: 'pointer',
      border: '1px solid ' + (active ? 'var(--primary-color)' : 'var(--surface-border)'),
      boxShadow: active ? 'var(--ring-selected)' : 'none',
      background: 'var(--surface-card)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      marginBottom: 5
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 11,
      fontWeight: 600,
      color: 'var(--text-color-secondary)'
    }
  }, index + 1), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 11,
      color: over ? 'var(--warn)' : 'var(--text-color-secondary)',
      fontVariantNumeric: 'tabular-nums'
    }
  }, secs.toFixed(1), "s"), panel.videos.length ? /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 10
    },
    title: panel.videos.length + ' takes'
  }, "\uD83C\uDFAC", panel.videos.length) : null, panel.video_prompt_locked === 1 ? /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 10
    },
    title: "Video prompt locked"
  }, "\uD83D\uDD12") : null, /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1,
      minWidth: 0,
      fontSize: 11,
      color: 'var(--text-color)',
      overflow: 'hidden',
      textOverflow: 'ellipsis',
      whiteSpace: 'nowrap'
    }
  }, panel.action)), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 2
    }
  }, panel.beats.map(b => /*#__PURE__*/React.createElement(BeatSegment, {
    key: b.id,
    beat: b,
    pct: b.duration_s / Math.max(secs, 0.1) * 100,
    selected: b.id === selectedBeatId,
    job: jobs[b.id],
    onClick: e => {
      e.stopPropagation();
      onSelectBeat(panel.id, b.id);
    }
  }))), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 4,
      height: 3,
      borderRadius: 2,
      background: 'var(--surface-hover)',
      overflow: 'hidden'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: Math.min(100, secs / CAP * 100) + '%',
      height: '100%',
      background: over ? 'var(--warn)' : 'var(--primary-color)'
    }
  })));
}
function SceneRail({
  scene,
  ...rest
}) {
  const secs = scene.panels.reduce((s, p) => s + shotSecs(p), 0);
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 12,
      padding: '10px 20px',
      borderBottom: '1px solid var(--surface-border)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: '0 0 150px',
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 13,
      fontWeight: 600,
      color: 'var(--text-color)'
    }
  }, scene.name), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-color-secondary)'
    }
  }, scene.subtitle || '—'), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-color-secondary)',
      marginTop: 2,
      fontVariantNumeric: 'tabular-nums'
    }
  }, scene.panels.length, " shots \xB7 ", secs.toFixed(1), "s"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 4,
      marginTop: 6
    }
  }, /*#__PURE__*/React.createElement(IconButton, {
    variant: "corner",
    glyph: "\u25B6",
    title: "Render scene videos",
    style: {
      fontSize: 8
    }
  }), /*#__PURE__*/React.createElement(IconButton, {
    variant: "corner",
    glyph: "\u270E",
    title: "Edit scene",
    style: {
      fontSize: 10
    }
  }), /*#__PURE__*/React.createElement(IconButton, {
    variant: "corner",
    glyph: "\xD7",
    destructive: true,
    title: "Delete scene"
  }))), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 0,
      display: 'flex',
      gap: 8,
      overflowX: 'auto',
      paddingBottom: 4,
      alignItems: 'flex-start'
    }
  }, scene.panels.map((p, i) => /*#__PURE__*/React.createElement(ShotCard, _extends({
    key: p.id,
    panel: p,
    index: i
  }, rest, {
    active: rest.selectedPanelId === p.id
  }))), /*#__PURE__*/React.createElement("button", {
    type: "button",
    style: {
      flexShrink: 0,
      width: 66,
      height: 96,
      borderRadius: 8,
      border: '1px dashed var(--surface-border)',
      background: 'none',
      color: 'var(--text-color-secondary)',
      cursor: 'pointer',
      fontSize: 11,
      fontFamily: 'inherit'
    }
  }, "+ Shot")));
}
function Group({
  title,
  children,
  meta
}) {
  return /*#__PURE__*/React.createElement("section", {
    style: {
      borderTop: '1px solid var(--surface-border)',
      padding: '12px 0 2px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'baseline',
      gap: 8,
      marginBottom: 8
    }
  }, /*#__PURE__*/React.createElement("h5", {
    style: {
      margin: 0,
      fontSize: 11,
      fontWeight: 600,
      textTransform: 'uppercase',
      letterSpacing: '.4px',
      color: 'var(--text-color)'
    }
  }, title), meta ? /*#__PURE__*/React.createElement("span", {
    className: "ms-hint"
  }, meta) : null), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 10
    }
  }, children));
}
function Inspector({
  tree,
  panel,
  beat,
  onPatchBeat,
  onPatchPanel
}) {
  const SB = window.SB;
  if (!beat) return /*#__PURE__*/React.createElement("p", {
    className: "ms-hint",
    style: {
      padding: 14,
      fontSize: 12
    }
  }, "Select a beat on the timeline.");
  const name = id => (tree.subjects.find(s => s.id === id) || {}).name || '#' + id;
  const secs = shotSecs(panel);
  return /*#__PURE__*/React.createElement("div", {
    style: {
      padding: '12px 14px 24px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      marginBottom: 4
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 14,
      fontWeight: 600
    }
  }, "Beat ", panel.beats.indexOf(beat) + 1), /*#__PURE__*/React.createElement("span", {
    className: "ms-hint"
  }, "of shot ", (panel.sort_order ?? 0) + 1, " \xB7 ", secs.toFixed(1), "s"), /*#__PURE__*/React.createElement("span", {
    style: {
      marginLeft: 'auto',
      display: 'flex',
      gap: 4
    }
  }, /*#__PURE__*/React.createElement(IconButton, {
    glyph: "\u2191",
    title: "Move beat up"
  }), /*#__PURE__*/React.createElement(IconButton, {
    glyph: "\u2193",
    title: "Move beat down"
  }), /*#__PURE__*/React.createElement(IconButton, {
    glyph: "\u2715",
    destructive: true,
    title: "Delete beat"
  }))), /*#__PURE__*/React.createElement(Group, {
    title: "Action & cast"
  }, /*#__PURE__*/React.createElement(Textarea, {
    rows: 2,
    value: beat.action,
    onChange: e => onPatchBeat({
      action: e.target.value
    })
  }), beat.subject_ids.length ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexWrap: 'wrap',
      gap: 6
    }
  }, beat.subject_ids.map((sid, i) => /*#__PURE__*/React.createElement(SubjectChip, {
    key: sid,
    name: name(sid),
    primary: i === 0,
    onClick: () => onPatchBeat({
      subject_ids: [sid].concat(beat.subject_ids.filter(x => x !== sid))
    })
  }))) : null, /*#__PURE__*/React.createElement("div", {
    className: "ms-checklist"
  }, tree.subjects.map(s => /*#__PURE__*/React.createElement(Checkbox, {
    key: s.id,
    label: s.name,
    checked: beat.subject_ids.includes(s.id),
    onChange: () => onPatchBeat({
      subject_ids: beat.subject_ids.includes(s.id) ? beat.subject_ids.filter(x => x !== s.id) : beat.subject_ids.concat(s.id)
    })
  })))), /*#__PURE__*/React.createElement(Group, {
    title: "Camera",
    meta: "framing, lens and move together"
  }, /*#__PURE__*/React.createElement(Field, {
    row: true
  }, /*#__PURE__*/React.createElement(Field, {
    label: "Shot size"
  }, /*#__PURE__*/React.createElement(Select, {
    options: SB.SHOT_SIZES,
    value: beat.shot_size ?? '',
    onChange: e => onPatchBeat({
      shot_size: e.target.value || null
    })
  })), /*#__PURE__*/React.createElement(Field, {
    label: "Angle"
  }, /*#__PURE__*/React.createElement(Select, {
    options: SB.ANGLES,
    value: beat.angle ?? '',
    onChange: e => onPatchBeat({
      angle: e.target.value || null
    })
  })), /*#__PURE__*/React.createElement(Field, {
    label: "Lens"
  }, /*#__PURE__*/React.createElement(Select, {
    options: SB.LENSES,
    value: beat.lens ?? '',
    onChange: e => onPatchBeat({
      lens: e.target.value || null
    })
  }))), /*#__PURE__*/React.createElement(Field, {
    label: "Move"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement(Select, {
    style: {
      flex: 2
    },
    options: SB.CAMERA_MOTIONS,
    value: beat.camera_motion ?? '',
    onChange: e => onPatchBeat({
      camera_motion: e.target.value || null
    })
  }), /*#__PURE__*/React.createElement(Select, {
    style: {
      flex: 1
    },
    includeEmpty: false,
    options: SB.CAMERA_AMPLITUDES,
    value: beat.camera_amplitude ?? 'small',
    onChange: e => onPatchBeat({
      camera_amplitude: e.target.value
    })
  }), /*#__PURE__*/React.createElement(Select, {
    style: {
      flex: 1
    },
    includeEmpty: false,
    options: SB.CAMERA_SPEEDS,
    value: beat.camera_speed ?? 'slow',
    onChange: e => onPatchBeat({
      camera_speed: e.target.value
    })
  }))), /*#__PURE__*/React.createElement(Field, {
    row: true
  }, /*#__PURE__*/React.createElement(Field, {
    label: "Duration (s)",
    style: {
      flex: '0 0 110px'
    }
  }, /*#__PURE__*/React.createElement(TextInput, {
    type: "number",
    step: "0.5",
    min: "0.5",
    value: beat.duration_s,
    onChange: e => onPatchBeat({
      duration_s: Number(e.target.value)
    })
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("label", {
    className: "ms-label"
  }, "Shot budget"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      height: 27
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      height: 4,
      borderRadius: 2,
      background: 'var(--surface-hover)',
      overflow: 'hidden'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: Math.min(100, secs / CAP * 100) + '%',
      height: '100%',
      background: secs > CAP ? 'var(--warn)' : 'var(--primary-color)'
    }
  })), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 11,
      color: secs > CAP ? 'var(--warn)' : 'var(--text-color-secondary)',
      fontVariantNumeric: 'tabular-nums'
    }
  }, secs.toFixed(1), "/", CAP, "s"))), /*#__PURE__*/React.createElement(Button, {
    size: "md",
    active: !!beat.is_cut,
    title: "Toggle hard cut before this beat",
    onClick: () => onPatchBeat({
      is_cut: beat.is_cut ? 0 : 1
    })
  }, "Cut"))), /*#__PURE__*/React.createElement(Group, {
    title: "Still",
    meta: beat.prompt_locked === 1 ? '🔒 edited' : beat.prompt_source === 'llm' ? 'synthesized' : '—'
  }, /*#__PURE__*/React.createElement(Textarea, {
    rows: 4,
    value: beat.prompt ?? '',
    placeholder: "No prompt synthesized yet.",
    onChange: e => onPatchBeat({
      prompt: e.target.value,
      prompt_source: 'user',
      prompt_locked: 1
    })
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8
    }
  }, /*#__PURE__*/React.createElement(Button, {
    size: "xs"
  }, "Re-synth prompt"), /*#__PURE__*/React.createElement(Button, {
    size: "xs"
  }, "Reroll"), beat.prompt_locked === 1 ? /*#__PURE__*/React.createElement(Button, {
    variant: "link",
    onClick: () => onPatchBeat({
      prompt_locked: 0
    })
  }, "Unlock") : null), /*#__PURE__*/React.createElement("div", {
    className: "ms-candidates-row"
  }, beat.images.map(img => /*#__PURE__*/React.createElement(CandidateTile, {
    key: img.id,
    src: img.file_path,
    selected: img.id === beat.selected_image_id,
    title: 'seed ' + (img.seed ?? '—') + ' · variant ' + img.variant_index,
    onClick: () => onPatchBeat({
      selected_image_id: beat.selected_image_id === img.id ? null : img.id
    }),
    onExpand: () => {}
  })), beat.images.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "ms-hint",
    style: {
      padding: '8px 0',
      fontSize: 12
    }
  }, "No candidates yet.") : null)), /*#__PURE__*/React.createElement(Group, {
    title: "Sound & dialog"
  }, /*#__PURE__*/React.createElement(Textarea, {
    rows: 2,
    value: beat.sound ?? '',
    placeholder: "ambient, effects, music",
    onChange: e => onPatchBeat({
      sound: e.target.value
    })
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 8,
      paddingLeft: 8,
      borderLeft: '2px solid var(--surface-border)'
    }
  }, beat.dialog.map((l, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 4
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement(Select, {
    style: {
      flex: '0 0 110px'
    },
    includeEmpty: false,
    value: l.subject_id ?? '',
    options: [{
      value: '',
      label: 'other voice'
    }].concat(tree.subjects.map(s => ({
      value: s.id,
      label: s.name
    }))),
    onChange: e => onPatchBeat({
      dialog: beat.dialog.map((x, j) => j === i ? {
        ...x,
        subject_id: e.target.value === '' ? null : Number(e.target.value)
      } : x)
    })
  }), /*#__PURE__*/React.createElement(TextInput, {
    placeholder: "delivery",
    defaultValue: l.delivery ?? ''
  }), /*#__PURE__*/React.createElement(IconButton, {
    size: "lg",
    glyph: "\u2715",
    title: "Remove line",
    onClick: () => onPatchBeat({
      dialog: beat.dialog.filter((_, j) => j !== i)
    })
  })), /*#__PURE__*/React.createElement(Textarea, {
    rows: 2,
    placeholder: "spoken line",
    defaultValue: l.text
  }))), /*#__PURE__*/React.createElement(Button, {
    variant: "dashed",
    size: "xs",
    style: {
      alignSelf: 'flex-start'
    },
    onClick: () => onPatchBeat({
      dialog: beat.dialog.concat({
        subject_id: null,
        voice: null,
        delivery: null,
        language: 'English',
        text: ''
      })
    })
  }, "+ line"))), /*#__PURE__*/React.createElement(Group, {
    title: 'Shot ' + ((panel.sort_order ?? 0) + 1),
    meta: "applies to every beat above"
  }, /*#__PURE__*/React.createElement(Field, {
    label: "Shot action"
  }, /*#__PURE__*/React.createElement(TextInput, {
    value: panel.action,
    onChange: e => onPatchPanel({
      action: e.target.value
    })
  })), /*#__PURE__*/React.createElement(LoraListEditor, {
    label: "Image LoRAs",
    entries: panel.image_loras,
    options: SB.loraOptions,
    listId: "tl-img",
    onChange: entries => onPatchPanel({
      image_loras: entries
    })
  }), /*#__PURE__*/React.createElement(LoraListEditor, {
    label: "Video LoRAs",
    entries: panel.video_loras,
    options: SB.loraOptions,
    listId: "tl-vid",
    onChange: entries => onPatchPanel({
      video_loras: entries
    })
  }), /*#__PURE__*/React.createElement(Field, {
    label: "Video prompt",
    aside: /*#__PURE__*/React.createElement("span", {
      className: "ms-hint"
    }, panel.video_prompt_source ?? '—')
  }, /*#__PURE__*/React.createElement(Textarea, {
    rows: 5,
    mono: true,
    placeholder: "No video prompt compiled yet.",
    value: panel.video_prompt ?? '',
    onChange: e => onPatchPanel({
      video_prompt: e.target.value,
      video_prompt_source: 'user'
    })
  })), panel.video_prompt_warnings.length ? /*#__PURE__*/React.createElement("ul", {
    style: {
      margin: 0,
      padding: '0 0 0 16px',
      listStyle: 'disc',
      color: 'var(--warn)',
      fontSize: 11,
      lineHeight: 1.5
    }
  }, panel.video_prompt_warnings.map((w, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, w))) : null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8,
      flexWrap: 'wrap'
    }
  }, /*#__PURE__*/React.createElement(Button, {
    size: "xs"
  }, "Compile"), /*#__PURE__*/React.createElement(Button, {
    size: "xs",
    disabled: !panel.video_prompt
  }, "Render video"), /*#__PURE__*/React.createElement(Button, {
    size: "xs"
  }, "Copy")), panel.videos.length ? /*#__PURE__*/React.createElement(Field, {
    label: "Takes"
  }, /*#__PURE__*/React.createElement("div", {
    className: "ms-candidates-row"
  }, panel.videos.map(v => /*#__PURE__*/React.createElement(VideoTakeTile, {
    key: v.id,
    src: v.file_path,
    title: 'seed ' + v.seed + ' · take ' + (v.variant_index + 1),
    onDelete: () => {}
  })))) : null));
}
function RedesignTimeline() {
  const SB = window.SB;
  const [tree, setTree] = React.useState(SB.tree);
  const [panelId, setPanelId] = React.useState(11);
  const [beatId, setBeatId] = React.useState(101);
  const jobs = {
    105: {
      state: 'running'
    },
    202: {
      state: 'queued'
    },
    301: {
      state: 'failed',
      error: 'preset 3: node 12 missing input'
    }
  };
  const panels = tree.scenes.flatMap(s => s.panels);
  const panel = panels.find(p => p.id === panelId);
  const beat = panel && panel.beats.find(b => b.id === beatId);
  const total = panels.reduce((s, p) => s + shotSecs(p), 0);
  const mutate = fn => setTree(t => {
    const n = JSON.parse(JSON.stringify(t));
    fn(n);
    return n;
  });
  const patchPanel = body => mutate(t => {
    const p = t.scenes.flatMap(s => s.panels).find(x => x.id === panelId);
    if (p) Object.assign(p, body);
  });
  const patchBeat = body => mutate(t => {
    const b = t.scenes.flatMap(s => s.panels).flatMap(p => p.beats).find(x => x.id === beatId);
    if (b) Object.assign(b, body);
  });
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      minHeight: 0
    }
  }, /*#__PURE__*/React.createElement("header", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 12,
      padding: '10px 20px',
      borderBottom: '1px solid var(--surface-border)',
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement("a", {
    href: "#",
    onClick: e => e.preventDefault(),
    style: {
      color: 'var(--text-color-secondary)',
      fontSize: 13
    }
  }, "\u2190 Library"), /*#__PURE__*/React.createElement("h2", {
    style: {
      margin: 0,
      fontSize: 16,
      fontWeight: 600
    }
  }, tree.name), /*#__PURE__*/React.createElement("span", {
    className: "ms-hint",
    style: {
      fontVariantNumeric: 'tabular-nums'
    }
  }, tree.scenes.length, " scenes \xB7 ", panels.length, " shots \xB7 ", total.toFixed(1), "s"), /*#__PURE__*/React.createElement(Chip, {
    tone: "primary"
  }, "MiniMax H3 \xB7 ", tree.video_mode), /*#__PURE__*/React.createElement("div", {
    style: {
      marginLeft: 'auto',
      display: 'flex',
      alignItems: 'center',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement(Button, {
    variant: "quiet",
    icon: "pi-sparkles"
  }, "Compose"), /*#__PURE__*/React.createElement(Button, {
    variant: "primary",
    icon: "pi-play"
  }, "Generate all"), /*#__PURE__*/React.createElement(IconButton, {
    variant: "outline",
    size: "lg",
    glyph: "\u22EF",
    title: "Import text, compile, render, cancel"
  }), /*#__PURE__*/React.createElement(IconButton, {
    variant: "outline",
    size: "lg",
    icon: "pi-cog",
    title: "Storyboard settings"
  }))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flex: 1,
      minHeight: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 0,
      overflowY: 'auto'
    }
  }, tree.scenes.map(s => /*#__PURE__*/React.createElement(SceneRail, {
    key: s.id,
    scene: s,
    selectedPanelId: panelId,
    selectedBeatId: beatId,
    jobs: jobs,
    onSelectShot: () => {
      setPanelId(s.panels[0] ? s.panels[0].id : null);
    },
    onSelectBeat: (pid, bid) => {
      setPanelId(pid);
      setBeatId(bid);
    }
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      padding: '12px 20px'
    }
  }, /*#__PURE__*/React.createElement(Button, {
    variant: "dashed",
    size: "md"
  }, "+ Scene"))), /*#__PURE__*/React.createElement("aside", {
    style: {
      flex: '0 0 400px',
      minHeight: 0,
      overflowY: 'auto',
      borderLeft: '1px solid var(--surface-border)',
      background: 'var(--surface-card)'
    }
  }, /*#__PURE__*/React.createElement(Inspector, {
    tree: tree,
    panel: panel,
    beat: beat,
    onPatchBeat: patchBeat,
    onPatchPanel: patchPanel
  }))));
}
Object.assign(window, {
  RedesignTimeline
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "redesign/RedesignTimeline.jsx", error: String((e && e.message) || e) }); }

// redesign/RedesignWorkspace.jsx
try { (() => {
const NS = window.MetascanDesignSystem_f2fbbd;
const {
  Button,
  IconButton,
  Chip,
  Field,
  TextInput,
  Select,
  Textarea,
  Checkbox,
  SubjectChip,
  CandidateTile,
  VideoTakeTile,
  JobBadge,
  LoraListEditor,
  ScriptBlock,
  Dialog
} = NS;
const CAP = 15;
const keeperOf = b => b.images.find(i => i.id === b.selected_image_id) || b.images[0] || null;
const shotSecs = p => p.beats.reduce((s, b) => s + b.duration_s, 0);
function OutlineRail({
  tree,
  panelId,
  onSelect,
  jobs
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      padding: '10px 8px 24px'
    }
  }, tree.scenes.map(s => /*#__PURE__*/React.createElement("div", {
    key: s.id,
    style: {
      marginBottom: 10
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      padding: '5px 6px'
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 10,
      color: 'var(--text-color-secondary)'
    }
  }, "\u25BC"), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 12,
      fontWeight: 600,
      flex: 1,
      minWidth: 0,
      overflow: 'hidden',
      textOverflow: 'ellipsis',
      whiteSpace: 'nowrap'
    }
  }, s.name), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 11,
      color: 'var(--text-color-secondary)',
      fontVariantNumeric: 'tabular-nums'
    }
  }, s.panels.length), /*#__PURE__*/React.createElement(IconButton, {
    variant: "corner",
    glyph: "\u270E",
    title: "Edit scene",
    style: {
      fontSize: 10
    }
  })), s.panels.map((p, i) => {
    const k = p.beats[0] && keeperOf(p.beats[0]);
    const active = p.id === panelId;
    const job = p.beats.map(b => jobs[b.id]).find(Boolean);
    return /*#__PURE__*/React.createElement("button", {
      key: p.id,
      type: "button",
      onClick: () => onSelect(p.id),
      style: {
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        width: '100%',
        padding: '5px 6px',
        marginBottom: 1,
        textAlign: 'left',
        cursor: 'pointer',
        fontFamily: 'inherit',
        border: '1px solid ' + (active ? 'var(--primary-color)' : 'transparent'),
        borderRadius: 6,
        background: active ? 'var(--primary-tint-10)' : 'transparent',
        color: 'var(--text-color)'
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        position: 'relative',
        flexShrink: 0,
        width: 34,
        height: 34,
        borderRadius: 4,
        overflow: 'hidden',
        background: 'var(--surface-ground)'
      }
    }, k ? /*#__PURE__*/React.createElement("img", {
      src: k.file_path,
      alt: "",
      style: {
        width: '100%',
        height: '100%',
        objectFit: 'cover',
        display: 'block'
      }
    }) : /*#__PURE__*/React.createElement("span", {
      className: "ms-thumb-empty"
    }), job ? /*#__PURE__*/React.createElement(JobBadge, {
      state: job.state,
      layout: "fill",
      error: job.error
    }) : null), /*#__PURE__*/React.createElement("span", {
      style: {
        flex: 1,
        minWidth: 0
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        display: 'block',
        fontSize: 12,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap'
      }
    }, i + 1, ". ", p.action), /*#__PURE__*/React.createElement("span", {
      style: {
        display: 'block',
        fontSize: 10,
        color: 'var(--text-color-secondary)',
        fontVariantNumeric: 'tabular-nums'
      }
    }, p.beats.length, " beats \xB7 ", shotSecs(p).toFixed(1), "s", p.videos.length ? ' · 🎬' + p.videos.length : '')));
  }), /*#__PURE__*/React.createElement(Button, {
    variant: "dashed",
    size: "xs",
    style: {
      margin: '4px 0 0 6px'
    }
  }, "+ Shot"))), /*#__PURE__*/React.createElement(Button, {
    variant: "dashed",
    size: "xs",
    style: {
      marginLeft: 6
    }
  }, "+ Scene"));
}

// Lifted from direction A: duration is spatial. Segment widths track each
// beat's share of the shot, the trailing gap is unused clip budget, and the
// whole strip is the shot's 15s cap at 1:1 scale.
function PacingStrip({
  panel,
  selectedBeatId,
  onSelectBeat,
  jobs
}) {
  const secs = shotSecs(panel);
  const over = secs > CAP;
  const scale = over ? secs : CAP;
  // Measured strip width, so a segment can decide whether its duration label
  // fits before rendering it — a 0.5s beat is only ~30px wide.
  const boxRef = React.useRef(null);
  const [stripW, setStripW] = React.useState(1040);
  React.useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const measure = () => setStripW(el.clientWidth || 1040);
    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, []);
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    ref: boxRef,
    style: {
      display: 'flex',
      gap: 2,
      height: 48
    }
  }, panel.beats.map((b, i) => {
    const k = keeperOf(b);
    const on = b.id === selectedBeatId;
    return /*#__PURE__*/React.createElement("button", {
      key: b.id,
      type: "button",
      onClick: () => onSelectBeat(b.id),
      title: b.action,
      style: {
        position: 'relative',
        width: b.duration_s / scale * 100 + '%',
        minWidth: 30,
        padding: 0,
        overflow: 'hidden',
        cursor: 'pointer',
        flexShrink: 0,
        border: '2px solid ' + (on ? 'var(--primary-color)' : 'transparent'),
        borderRadius: 5,
        borderLeft: b.is_cut ? '3px solid var(--warn)' : undefined,
        background: 'var(--surface-ground)'
      }
    }, k ? /*#__PURE__*/React.createElement("img", {
      src: k.file_path,
      alt: "",
      style: {
        width: '100%',
        height: '100%',
        objectFit: 'cover',
        display: 'block',
        opacity: b.selected_image_id ? 1 : 0.45
      }
    }) : /*#__PURE__*/React.createElement("span", {
      style: {
        position: 'absolute',
        inset: 2,
        border: '1px dashed var(--surface-border)',
        borderRadius: 3
      }
    }), /*#__PURE__*/React.createElement("span", {
      style: {
        position: 'absolute',
        inset: 0,
        display: 'flex',
        alignItems: 'flex-end',
        justifyContent: 'space-between',
        gap: 6,
        padding: '2px 4px',
        background: 'linear-gradient(transparent, rgba(0,0,0,0.7))',
        color: '#fff',
        fontSize: 10,
        fontVariantNumeric: 'tabular-nums',
        whiteSpace: 'nowrap',
        overflow: 'hidden'
      }
    }, /*#__PURE__*/React.createElement("span", null, i + 1), b.duration_s / scale * stripW >= 44 ? /*#__PURE__*/React.createElement("span", null, b.duration_s.toFixed(1), "s") : null), jobs[b.id] ? /*#__PURE__*/React.createElement(JobBadge, {
      state: jobs[b.id].state,
      layout: "fill",
      error: jobs[b.id].error
    }) : null);
  }), !over && secs < CAP ? /*#__PURE__*/React.createElement("div", {
    title: (CAP - secs).toFixed(1) + 's of clip budget unused',
    style: {
      width: (CAP - secs) / scale * 100 + '%',
      borderRadius: 5,
      border: '1px dashed var(--surface-border)'
    }
  }) : null), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      marginTop: 4
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      height: 3,
      borderRadius: 2,
      background: 'var(--surface-hover)',
      overflow: 'hidden'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: Math.min(100, secs / CAP * 100) + '%',
      height: '100%',
      background: over ? 'var(--warn)' : 'var(--primary-color)'
    }
  })), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 11,
      color: over ? 'var(--warn)' : 'var(--text-color-secondary)',
      fontVariantNumeric: 'tabular-nums'
    }
  }, secs.toFixed(1), "s / ", CAP, "s", over ? ' — exceeds H3 clip cap' : '')));
}

// The compiled video prompt is reference output, not a field you type in —
// so it lives behind a button, with the room to actually read it.
function VideoPromptDialog({
  panel,
  index,
  onClose,
  onPatch
}) {
  const [copied, setCopied] = React.useState(false);
  const copy = () => {
    if (navigator.clipboard && panel.video_prompt) navigator.clipboard.writeText(panel.video_prompt);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  const words = panel.video_prompt ? panel.video_prompt.trim().split(/\s+/).length : 0;
  return /*#__PURE__*/React.createElement(Dialog, {
    size: "md",
    onDismiss: onClose,
    actions: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Button, {
      variant: "primary",
      size: "lg"
    }, "Compile"), /*#__PURE__*/React.createElement(Button, {
      variant: "secondary",
      size: "lg",
      disabled: !panel.video_prompt,
      onClick: copy
    }, copied ? 'Copied' : 'Copy'), /*#__PURE__*/React.createElement(Button, {
      variant: "secondary",
      size: "lg",
      disabled: !panel.video_prompt
    }, "Render video"), /*#__PURE__*/React.createElement(Button, {
      variant: "secondary",
      size: "lg",
      onClick: onClose
    }, "Close"))
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      marginBottom: 10
    }
  }, /*#__PURE__*/React.createElement("h3", {
    className: "ms-dialog__title",
    style: {
      margin: 0
    }
  }, "Video prompt \u2014 shot ", index + 1), /*#__PURE__*/React.createElement(Chip, {
    tone: "primary"
  }, "MiniMax H3 \xB7 ", window.SB.tree.video_mode), /*#__PURE__*/React.createElement("span", {
    className: "ms-hint",
    style: {
      marginLeft: 'auto',
      fontVariantNumeric: 'tabular-nums'
    }
  }, panel.video_prompt_source ?? 'not compiled', words ? ' · ' + words + ' words' : ''), panel.video_prompt_locked === 1 ? /*#__PURE__*/React.createElement(Button, {
    variant: "link",
    onClick: () => onPatch({
      video_prompt_locked: 0
    })
  }, "\uD83D\uDD12 Unlock") : null), panel.video_prompt_warnings.length ? /*#__PURE__*/React.createElement("ul", {
    style: {
      margin: '0 0 10px',
      padding: '8px 10px 8px 26px',
      listStyle: 'disc',
      color: 'var(--warn)',
      fontSize: 12,
      lineHeight: 1.5,
      background: 'var(--warn-tint-14)',
      borderRadius: 6
    }
  }, panel.video_prompt_warnings.map((w, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, w))) : null, panel.video_prompt ? /*#__PURE__*/React.createElement(ScriptBlock, {
    text: panel.video_prompt,
    style: {
      maxHeight: '52vh',
      fontSize: 13,
      lineHeight: 1.6,
      padding: 14
    }
  }) : /*#__PURE__*/React.createElement("p", {
    className: "ms-hint",
    style: {
      fontSize: 13,
      padding: '24px 0',
      textAlign: 'center'
    }
  }, "No video prompt compiled yet \u2014 Compile builds it from this shot's beats."), /*#__PURE__*/React.createElement("p", {
    className: "ms-hint",
    style: {
      fontSize: 11,
      marginTop: 10
    }
  }, "Compiled from the beats below. Edit a beat's framing, cast or dialog and recompile \u2014 hand-editing this text locks it against the next compile pass."));
}

// The still prompt gets the same treatment as the video prompt when you want
// room — but this one IS a field, so the dialog is editable.
function BeatPromptDialog({
  beat,
  index,
  onClose,
  onPatch
}) {
  const [draft, setDraft] = React.useState(beat.prompt ?? '');
  const [copied, setCopied] = React.useState(false);
  const words = draft.trim() ? draft.trim().split(/\s+/).length : 0;
  const dirty = draft !== (beat.prompt ?? '');
  const status = beat.prompt_locked === 1 ? '🔒 edited' : beat.prompt_source === 'llm' ? 'synthesized' : beat.prompt_source === 'brief' ? 'brief fallback' : 'not synthesized';
  const commit = () => {
    if (dirty) onPatch({
      prompt: draft,
      prompt_source: 'user',
      prompt_locked: 1
    });
    onClose();
  };
  const copy = () => {
    if (navigator.clipboard && draft) navigator.clipboard.writeText(draft);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return /*#__PURE__*/React.createElement(Dialog, {
    size: "md",
    onDismiss: onClose,
    actions: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Button, {
      variant: "primary",
      size: "lg",
      onClick: commit
    }, dirty ? 'Save prompt' : 'Done'), /*#__PURE__*/React.createElement(Button, {
      variant: "secondary",
      size: "lg",
      disabled: !draft,
      onClick: copy
    }, copied ? 'Copied' : 'Copy'), /*#__PURE__*/React.createElement(Button, {
      variant: "secondary",
      size: "lg"
    }, "Re-synth"), /*#__PURE__*/React.createElement(Button, {
      variant: "secondary",
      size: "lg",
      onClick: onClose
    }, "Cancel"))
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      marginBottom: 8
    }
  }, /*#__PURE__*/React.createElement("h3", {
    className: "ms-dialog__title",
    style: {
      margin: 0
    }
  }, "Prompt \u2014 beat ", index + 1), /*#__PURE__*/React.createElement("span", {
    className: "ms-hint"
  }, status), /*#__PURE__*/React.createElement("span", {
    className: "ms-hint",
    style: {
      marginLeft: 'auto',
      fontVariantNumeric: 'tabular-nums'
    }
  }, words, " words"), beat.prompt_locked === 1 ? /*#__PURE__*/React.createElement(Button, {
    variant: "link",
    onClick: () => onPatch({
      prompt_locked: 0
    })
  }, "Unlock") : null), /*#__PURE__*/React.createElement("p", {
    className: "ms-hint",
    style: {
      fontSize: 12,
      marginBottom: 8
    }
  }, (beat.shot_size || '—') + ' · ' + (beat.angle || '—') + ' · ' + (beat.lens || '—') + (beat.camera_motion ? ' · ' + beat.camera_motion.replace(/_/g, ' ') : ''), " \u2014 ", beat.action), /*#__PURE__*/React.createElement(Textarea, {
    mono: true,
    rows: 16,
    value: draft,
    placeholder: "No prompt synthesized yet.",
    style: {
      fontSize: 13,
      lineHeight: 1.6,
      padding: 14
    },
    onChange: e => setDraft(e.target.value)
  }), /*#__PURE__*/React.createElement("p", {
    className: "ms-hint",
    style: {
      fontSize: 11,
      marginTop: 10
    }
  }, "Saving marks the prompt user-edited and locks it, so the next Synthesize pass leaves it alone."));
}
function BeatCard({
  beat,
  index,
  tree,
  jobs,
  onPatch,
  onRemove,
  canUp,
  canDown,
  onMove,
  selected,
  onSelect,
  cardRef
}) {
  const SB = window.SB;
  const name = id => (tree.subjects.find(s => s.id === id) || {}).name || '#' + id;
  const job = jobs[beat.id];
  const status = beat.prompt_locked === 1 ? '🔒 edited' : beat.prompt_source === 'llm' ? 'synthesized' : beat.prompt_source === 'brief' ? 'brief fallback' : '—';
  const [promptOpen, setPromptOpen] = React.useState(false);
  return /*#__PURE__*/React.createElement("article", {
    ref: cardRef,
    onClick: onSelect,
    style: {
      border: '1px solid ' + (selected ? 'var(--primary-color)' : 'var(--surface-border)'),
      boxShadow: selected ? 'var(--ring-selected)' : 'none',
      borderRadius: 8,
      background: 'var(--surface-card)',
      padding: '12px 14px 14px',
      display: 'flex',
      flexDirection: 'column',
      gap: 12
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 12,
      fontWeight: 600,
      color: selected ? 'var(--primary-color)' : 'var(--text-color-secondary)'
    }
  }, "BEAT ", index + 1), beat.is_cut ? /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 11,
      fontWeight: 600,
      color: 'var(--warn)'
    }
  }, "hard cut") : null, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 11,
      color: 'var(--text-color-secondary)',
      fontVariantNumeric: 'tabular-nums'
    }
  }, beat.duration_s.toFixed(1), "s"), job ? /*#__PURE__*/React.createElement(Chip, null, job.state) : null, /*#__PURE__*/React.createElement("span", {
    style: {
      marginLeft: 'auto',
      display: 'flex',
      gap: 4
    }
  }, /*#__PURE__*/React.createElement(IconButton, {
    glyph: "\u2191",
    title: "Move beat up",
    disabled: !canUp,
    onClick: e => {
      e.stopPropagation();
      onMove(-1);
    }
  }), /*#__PURE__*/React.createElement(IconButton, {
    glyph: "\u2193",
    title: "Move beat down",
    disabled: !canDown,
    onClick: e => {
      e.stopPropagation();
      onMove(1);
    }
  }), /*#__PURE__*/React.createElement(IconButton, {
    glyph: "\u2715",
    destructive: true,
    title: "Delete beat",
    onClick: e => {
      e.stopPropagation();
      onRemove();
    }
  }))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 14,
      alignItems: 'flex-start'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 0,
      display: 'flex',
      flexDirection: 'column',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement(Field, {
    label: "Action"
  }, /*#__PURE__*/React.createElement(Textarea, {
    rows: 2,
    value: beat.action,
    onChange: e => onPatch({
      action: e.target.value
    })
  })), /*#__PURE__*/React.createElement(Field, {
    row: true
  }, /*#__PURE__*/React.createElement(Field, {
    label: "Shot size"
  }, /*#__PURE__*/React.createElement(Select, {
    options: SB.SHOT_SIZES,
    value: beat.shot_size ?? '',
    onChange: e => onPatch({
      shot_size: e.target.value || null
    })
  })), /*#__PURE__*/React.createElement(Field, {
    label: "Angle"
  }, /*#__PURE__*/React.createElement(Select, {
    options: SB.ANGLES,
    value: beat.angle ?? '',
    onChange: e => onPatch({
      angle: e.target.value || null
    })
  })), /*#__PURE__*/React.createElement(Field, {
    label: "Lens"
  }, /*#__PURE__*/React.createElement(Select, {
    options: SB.LENSES,
    value: beat.lens ?? '',
    onChange: e => onPatch({
      lens: e.target.value || null
    })
  })), /*#__PURE__*/React.createElement(Field, {
    label: "Dur (s)",
    style: {
      flex: '0 0 84px'
    }
  }, /*#__PURE__*/React.createElement(TextInput, {
    type: "number",
    step: "0.5",
    min: "0.5",
    value: beat.duration_s,
    onChange: e => onPatch({
      duration_s: Number(e.target.value)
    })
  })), /*#__PURE__*/React.createElement(Button, {
    size: "md",
    active: !!beat.is_cut,
    onClick: () => onPatch({
      is_cut: beat.is_cut ? 0 : 1
    }),
    title: "Toggle hard cut before this beat"
  }, "Cut")), /*#__PURE__*/React.createElement(Field, {
    label: "Camera move"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement(Select, {
    style: {
      flex: 2
    },
    options: SB.CAMERA_MOTIONS,
    value: beat.camera_motion ?? '',
    onChange: e => onPatch({
      camera_motion: e.target.value || null
    })
  }), /*#__PURE__*/React.createElement(Select, {
    style: {
      flex: 1
    },
    includeEmpty: false,
    options: SB.CAMERA_AMPLITUDES,
    value: beat.camera_amplitude ?? 'small',
    onChange: e => onPatch({
      camera_amplitude: e.target.value
    })
  }), /*#__PURE__*/React.createElement(Select, {
    style: {
      flex: 1
    },
    includeEmpty: false,
    options: SB.CAMERA_SPEEDS,
    value: beat.camera_speed ?? 'slow',
    onChange: e => onPatch({
      camera_speed: e.target.value
    })
  }))), /*#__PURE__*/React.createElement(Field, {
    label: "Cast"
  }, beat.subject_ids.length ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexWrap: 'wrap',
      gap: 6,
      marginBottom: 2
    }
  }, beat.subject_ids.map((sid, i) => /*#__PURE__*/React.createElement(SubjectChip, {
    key: sid,
    name: name(sid),
    primary: i === 0,
    onClick: () => onPatch({
      subject_ids: [sid].concat(beat.subject_ids.filter(x => x !== sid))
    })
  }))) : null, /*#__PURE__*/React.createElement("div", {
    className: "ms-checklist"
  }, tree.subjects.map(s => /*#__PURE__*/React.createElement(Checkbox, {
    key: s.id,
    label: s.name,
    checked: beat.subject_ids.includes(s.id),
    onChange: () => onPatch({
      subject_ids: beat.subject_ids.includes(s.id) ? beat.subject_ids.filter(x => x !== s.id) : beat.subject_ids.concat(s.id)
    })
  }))))), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: '0 0 340px',
      display: 'flex',
      flexDirection: 'column',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement(Field, {
    label: "Prompt",
    aside: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("span", {
      className: "ms-hint",
      style: {
        flex: 1
      }
    }, status), beat.prompt_locked === 1 ? /*#__PURE__*/React.createElement(Button, {
      variant: "link",
      onClick: () => onPatch({
        prompt_locked: 0
      })
    }, "Unlock") : null, /*#__PURE__*/React.createElement(IconButton, {
      icon: "pi-window-maximize",
      title: "Open prompt in a larger editor",
      onClick: e => {
        e.stopPropagation();
        setPromptOpen(true);
      }
    }))
  }, /*#__PURE__*/React.createElement(Textarea, {
    rows: 6,
    value: beat.prompt ?? '',
    placeholder: "No prompt synthesized yet.",
    onChange: e => onPatch({
      prompt: e.target.value,
      prompt_source: 'user',
      prompt_locked: 1
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "ms-field"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("label", {
    className: "ms-label",
    style: {
      flex: 1
    }
  }, "Candidates"), /*#__PURE__*/React.createElement(Button, {
    size: "xs"
  }, "Re-synth"), /*#__PURE__*/React.createElement(Button, {
    size: "xs"
  }, "Reroll")), /*#__PURE__*/React.createElement("div", {
    className: "ms-candidates-row"
  }, beat.images.map(img => /*#__PURE__*/React.createElement(CandidateTile, {
    key: img.id,
    src: img.file_path,
    selected: img.id === beat.selected_image_id,
    title: 'seed ' + (img.seed ?? '—') + ' · variant ' + img.variant_index,
    onClick: () => onPatch({
      selected_image_id: beat.selected_image_id === img.id ? null : img.id
    }),
    onExpand: () => {}
  })), beat.images.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "ms-hint",
    style: {
      padding: '8px 0',
      fontSize: 12
    }
  }, "No candidates yet.") : null)), /*#__PURE__*/React.createElement(Field, {
    label: "Sound"
  }, /*#__PURE__*/React.createElement(Textarea, {
    rows: 2,
    value: beat.sound ?? '',
    placeholder: "ambient, effects, music",
    onChange: e => onPatch({
      sound: e.target.value
    })
  })), /*#__PURE__*/React.createElement(Field, {
    label: "Dialog"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 6,
      paddingLeft: 8,
      borderLeft: '2px solid var(--surface-border)'
    }
  }, beat.dialog.map((l, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 4
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement(Select, {
    style: {
      flex: 1
    },
    includeEmpty: false,
    value: l.subject_id ?? '',
    options: [{
      value: '',
      label: 'other voice'
    }].concat(tree.subjects.map(s => ({
      value: s.id,
      label: s.name
    }))),
    onChange: e => onPatch({
      dialog: beat.dialog.map((x, j) => j === i ? {
        ...x,
        subject_id: e.target.value === '' ? null : Number(e.target.value)
      } : x)
    })
  }), /*#__PURE__*/React.createElement(TextInput, {
    style: {
      flex: '0 0 84px'
    },
    placeholder: "delivery",
    defaultValue: l.delivery ?? ''
  }), /*#__PURE__*/React.createElement(IconButton, {
    size: "lg",
    glyph: "\u2715",
    title: "Remove line",
    onClick: () => onPatch({
      dialog: beat.dialog.filter((_, j) => j !== i)
    })
  })), /*#__PURE__*/React.createElement(Textarea, {
    rows: 2,
    placeholder: "spoken line",
    defaultValue: l.text
  }))), /*#__PURE__*/React.createElement(Button, {
    variant: "dashed",
    size: "xs",
    style: {
      alignSelf: 'flex-start'
    },
    onClick: () => onPatch({
      dialog: beat.dialog.concat({
        subject_id: null,
        voice: null,
        delivery: null,
        language: 'English',
        text: ''
      })
    })
  }, "+ line"))))), promptOpen ? /*#__PURE__*/React.createElement(BeatPromptDialog, {
    beat: beat,
    index: index,
    onClose: () => setPromptOpen(false),
    onPatch: onPatch
  }) : null);
}
function ShotHeader({
  panel,
  index,
  sceneName,
  onPatch,
  selectedBeatId,
  onSelectBeat,
  jobs,
  onOpenPrompt
}) {
  const SB = window.SB;
  const warn = panel.video_prompt_warnings.length;
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 12,
      paddingBottom: 16,
      borderBottom: '1px solid var(--surface-border)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'baseline',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "ms-hint"
  }, sceneName, " \u203A"), /*#__PURE__*/React.createElement("h3", {
    style: {
      margin: 0,
      fontSize: 18,
      fontWeight: 600
    }
  }, "Shot ", index + 1), /*#__PURE__*/React.createElement("span", {
    className: "ms-hint"
  }, panel.beats.length, " beats"), /*#__PURE__*/React.createElement("div", {
    style: {
      marginLeft: 'auto',
      display: 'flex',
      gap: 8
    }
  }, /*#__PURE__*/React.createElement(Button, {
    size: "sm"
  }, "Reroll shot"), /*#__PURE__*/React.createElement(Button, {
    size: "sm"
  }, "Re-synth shot"), /*#__PURE__*/React.createElement(Button, {
    size: "sm"
  }, "Re-beat shot"))), /*#__PURE__*/React.createElement(PacingStrip, {
    panel: panel,
    selectedBeatId: selectedBeatId,
    onSelectBeat: onSelectBeat,
    jobs: jobs
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 16,
      alignItems: 'flex-end'
    }
  }, /*#__PURE__*/React.createElement(Field, {
    label: "Shot action",
    style: {
      flex: 1,
      minWidth: 220
    }
  }, /*#__PURE__*/React.createElement(TextInput, {
    value: panel.action,
    onChange: e => onPatch({
      action: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement(Button, {
    size: "sm",
    icon: "pi-file",
    onClick: onOpenPrompt
  }, "Video prompt"), /*#__PURE__*/React.createElement("span", {
    className: "ms-hint"
  }, panel.video_prompt_source ?? 'not compiled'), panel.video_prompt_locked === 1 ? /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 11
    },
    title: "Locked against the next compile"
  }, "\uD83D\uDD12") : null, warn ? /*#__PURE__*/React.createElement(Chip, {
    tone: "warn"
  }, warn, " lint ", warn === 1 ? 'warning' : 'warnings') : null, /*#__PURE__*/React.createElement(Button, {
    size: "sm"
  }, "Compile"), /*#__PURE__*/React.createElement(Button, {
    size: "sm",
    variant: "primary",
    disabled: !panel.video_prompt,
    icon: "pi-play"
  }, "Render video"))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 16,
      alignItems: 'flex-start'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 236
    }
  }, /*#__PURE__*/React.createElement(LoraListEditor, {
    label: "Image LoRAs",
    entries: panel.image_loras,
    options: SB.loraOptions,
    listId: "ws-img",
    onChange: entries => onPatch({
      image_loras: entries
    })
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 236
    }
  }, /*#__PURE__*/React.createElement(LoraListEditor, {
    label: "Video LoRAs",
    entries: panel.video_loras,
    options: SB.loraOptions,
    listId: "ws-vid",
    onChange: entries => onPatch({
      video_loras: entries
    })
  })), /*#__PURE__*/React.createElement(Field, {
    label: 'Takes (' + panel.videos.length + ')',
    style: {
      flex: '0 0 374px'
    },
    aside: panel.videos.length ? /*#__PURE__*/React.createElement("span", {
      className: "ms-hint"
    }, "newest last \xB7 double-click to play") : null
  }, panel.videos.length ? /*#__PURE__*/React.createElement("div", {
    className: "ms-candidates-row"
  }, panel.videos.map(v => /*#__PURE__*/React.createElement(VideoTakeTile, {
    key: v.id,
    src: v.file_path,
    style: {
      width: 176,
      height: 99
    },
    title: 'seed ' + v.seed + ' · take ' + (v.variant_index + 1),
    onDelete: () => {}
  }))) : /*#__PURE__*/React.createElement("div", {
    style: {
      height: 99,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      border: '1px dashed var(--surface-border)',
      borderRadius: 6,
      color: 'var(--text-color-secondary)',
      fontSize: 12
    }
  }, "No takes rendered yet."))));
}
function RedesignWorkspace() {
  const SB = window.SB;
  const [tree, setTree] = React.useState(SB.tree);
  const [panelId, setPanelId] = React.useState(11);
  const [beatId, setBeatId] = React.useState(101);
  const [promptOpen, setPromptOpen] = React.useState(false);
  const scrollRef = React.useRef(null);
  const cardRefs = React.useRef({});
  const jobs = {
    105: {
      state: 'running'
    },
    202: {
      state: 'queued'
    },
    301: {
      state: 'failed',
      error: 'preset 3: node 12 missing input'
    }
  };
  const scene = tree.scenes.find(s => s.panels.some(p => p.id === panelId));
  const panel = scene && scene.panels.find(p => p.id === panelId);
  const index = scene ? scene.panels.indexOf(panel) : 0;
  const mutate = fn => setTree(t => {
    const n = JSON.parse(JSON.stringify(t));
    fn(n);
    return n;
  });
  const patchPanel = body => mutate(t => {
    const p = t.scenes.flatMap(s => s.panels).find(x => x.id === panelId);
    if (p) Object.assign(p, body);
  });
  const patchBeat = (id, body) => mutate(t => {
    const b = t.scenes.flatMap(s => s.panels).flatMap(p => p.beats).find(x => x.id === id);
    if (b) Object.assign(b, body);
  });
  const moveBeat = (i, dir) => mutate(t => {
    const p = t.scenes.flatMap(s => s.panels).find(x => x.id === panelId);
    const j = i + dir;
    if (!p || j < 0 || j >= p.beats.length) return;
    const tmp = p.beats[i];
    p.beats[i] = p.beats[j];
    p.beats[j] = tmp;
  });
  const removeBeat = id => mutate(t => {
    const p = t.scenes.flatMap(s => s.panels).find(x => x.id === panelId);
    if (p) p.beats = p.beats.filter(b => b.id !== id);
  });
  const addBeat = () => {
    const id = Date.now();
    mutate(t => {
      const p = t.scenes.flatMap(s => s.panels).find(x => x.id === panelId);
      if (p) p.beats.push({
        id,
        duration_s: 2,
        action: 'new beat',
        shot_size: null,
        angle: null,
        lens: null,
        subject_ids: [],
        camera_motion: null,
        camera_amplitude: null,
        camera_speed: null,
        is_cut: 0,
        dialog: [],
        sound: null,
        prompt: null,
        prompt_locked: 0,
        prompt_source: null,
        selected_image_id: null,
        images: []
      });
    });
    setBeatId(id);
  };
  const selectShot = id => {
    setPanelId(id);
    const p = tree.scenes.flatMap(s => s.panels).find(x => x.id === id);
    setBeatId(p && p.beats[0] ? p.beats[0].id : null);
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  };
  // Selecting from the pacing strip brings that beat's card up without
  // scrollIntoView: the scroll container owns the offset maths.
  const selectBeat = id => {
    setBeatId(id);
    const el = cardRefs.current[id];
    const box = scrollRef.current;
    if (el && box) box.scrollTop = Math.max(0, el.offsetTop - box.offsetTop - 12);
  };
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      minHeight: 0
    }
  }, /*#__PURE__*/React.createElement("header", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 12,
      padding: '10px 20px',
      borderBottom: '1px solid var(--surface-border)',
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement("a", {
    href: "#",
    onClick: e => e.preventDefault(),
    style: {
      color: 'var(--text-color-secondary)',
      fontSize: 13
    }
  }, "\u2190 Library"), /*#__PURE__*/React.createElement("h2", {
    style: {
      margin: 0,
      fontSize: 16,
      fontWeight: 600
    }
  }, tree.name), /*#__PURE__*/React.createElement(Chip, {
    tone: "primary"
  }, "MiniMax H3 \xB7 ", tree.video_mode), /*#__PURE__*/React.createElement("div", {
    style: {
      marginLeft: 'auto',
      display: 'flex',
      alignItems: 'center',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement(Button, {
    variant: "quiet",
    icon: "pi-sparkles"
  }, "Compose"), /*#__PURE__*/React.createElement(Button, {
    variant: "primary",
    icon: "pi-play"
  }, "Generate all"), /*#__PURE__*/React.createElement(IconButton, {
    variant: "outline",
    size: "lg",
    glyph: "\u22EF",
    title: "Import text, compile, render, cancel"
  }), /*#__PURE__*/React.createElement(IconButton, {
    variant: "outline",
    size: "lg",
    icon: "pi-cog",
    title: "Storyboard settings"
  }))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flex: 1,
      minHeight: 0
    }
  }, /*#__PURE__*/React.createElement("aside", {
    style: {
      flex: '0 0 270px',
      minHeight: 0,
      overflowY: 'auto',
      borderRight: '1px solid var(--surface-border)'
    }
  }, /*#__PURE__*/React.createElement(OutlineRail, {
    tree: tree,
    panelId: panelId,
    onSelect: selectShot,
    jobs: jobs
  })), /*#__PURE__*/React.createElement("main", {
    ref: scrollRef,
    style: {
      flex: 1,
      minWidth: 0,
      minHeight: 0,
      overflowY: 'auto',
      padding: '18px 24px 40px'
    }
  }, panel ? /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: 1040,
      display: 'flex',
      flexDirection: 'column',
      gap: 16
    }
  }, /*#__PURE__*/React.createElement(ShotHeader, {
    panel: panel,
    index: index,
    sceneName: scene.name,
    onPatch: patchPanel,
    selectedBeatId: beatId,
    onSelectBeat: selectBeat,
    jobs: jobs,
    onOpenPrompt: () => setPromptOpen(true)
  }), panel.beats.map((b, i) => /*#__PURE__*/React.createElement(BeatCard, {
    key: b.id,
    beat: b,
    index: i,
    tree: tree,
    jobs: jobs,
    cardRef: el => {
      cardRefs.current[b.id] = el;
    },
    selected: b.id === beatId,
    onSelect: () => setBeatId(b.id),
    onPatch: body => patchBeat(b.id, body),
    onRemove: () => removeBeat(b.id),
    canUp: i > 0,
    canDown: i < panel.beats.length - 1,
    onMove: d => moveBeat(i, d)
  })), /*#__PURE__*/React.createElement(Button, {
    variant: "dashed",
    size: "md",
    style: {
      alignSelf: 'flex-start'
    },
    onClick: addBeat
  }, "+ Beat")) : /*#__PURE__*/React.createElement("p", {
    className: "ms-hint",
    style: {
      fontSize: 14
    }
  }, "Select a shot from the outline."))), promptOpen && panel ? /*#__PURE__*/React.createElement(VideoPromptDialog, {
    panel: panel,
    index: index,
    onClose: () => setPromptOpen(false),
    onPatch: patchPanel
  }) : null);
}
Object.assign(window, {
  RedesignWorkspace
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "redesign/RedesignWorkspace.jsx", error: String((e && e.message) || e) }); }

// ui_kits/library/LibraryApp.jsx
try { (() => {
const {
  Button,
  IconButton,
  TextInput,
  Toast
} = window.MetascanDesignSystem_f2fbbd;
const F = n => '../../assets/placeholders/frame-' + String(n).padStart(2, '0') + '.png';
const MEDIA = Array.from({
  length: 18
}, (_, i) => ({
  path: F(i + 1),
  name: '1414-A full body shot, portrait shot of a villager ' + (i + 1) + '.jpg',
  favorite: i === 6,
  video: i === 11 || i === 12,
  score: null
}));
const FILTERS = [{
  label: 'Camera Make',
  items: [['Apple', 1]]
}, {
  label: 'Camera Model',
  items: [['iPhone 14 Pro', 2], ['DMC-GX85', 1], ['X100V', 1]]
}, {
  label: 'Has Location',
  items: [['yes', 1]]
}, {
  label: 'Model',
  items: [['realisticstockphoto_v20', 45]]
}, {
  label: 'LoRA',
  items: [['dmd2_sdxl_4step_lora_fp16', 45], ['sts_age_slider_v1_initial_rel…', 45]]
}, {
  label: 'Tags',
  items: [['ancient roman architecture', 43], ['ancient rome', 39], ['modern day', 18], ['full body', 16], ['medieval setting', 16], ['beach', 12], ['close', 12], ['triumphal arch', 11], ['lodgepole pine', 10], ['medium', 10], ['ogre', 10], ['basilisk', 9], ['castle', 9], ['fantasy setting', 9]]
}];
const TAGS = [['beach', 'prompt'], ['fantasy setting', 'prompt'], ['full body', 'clip'], ['ogre', 'clip'], ['orc', 'vlm'], ['portrait villager', 'both']];
const STRIPE = {
  prompt: '#4f8bff',
  clip: '#5cd0ff',
  vlm: '#ffd24a',
  both: 'linear-gradient(to bottom,#4f8bff 0 50%,#5cd0ff 50% 100%)'
};
function SectionHeader({
  label,
  count,
  expanded,
  onToggle,
  onClear
}) {
  return /*#__PURE__*/React.createElement("button", {
    onClick: onToggle,
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      width: '100%',
      padding: '6px 4px',
      background: 'none',
      border: 'none',
      cursor: 'pointer',
      color: 'var(--text-color)',
      fontSize: 13,
      fontWeight: 600,
      textAlign: 'left',
      fontFamily: 'inherit'
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 10,
      width: 12
    }
  }, expanded ? '▼' : '▶'), /*#__PURE__*/React.createElement("span", null, label), count != null ? /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-color-secondary)',
      fontWeight: 400
    }
  }, "(", count, ")") : null, onClear ? /*#__PURE__*/React.createElement("span", {
    onClick: e => {
      e.stopPropagation();
      onClear();
    },
    title: "Clear",
    style: {
      marginLeft: 'auto',
      color: 'var(--text-color-secondary)',
      fontSize: 16,
      lineHeight: 1,
      padding: '0 4px'
    }
  }, "\xD7") : null);
}
function SearchSection({
  query,
  setQuery,
  threshold,
  setThreshold
}) {
  const [expanded, setExpanded] = React.useState(true);
  return /*#__PURE__*/React.createElement("div", {
    style: {
      borderBottom: '1px solid var(--surface-border)',
      paddingBottom: 4
    }
  }, /*#__PURE__*/React.createElement(SectionHeader, {
    label: "Search",
    expanded: expanded,
    onToggle: () => setExpanded(!expanded),
    onClear: query ? () => setQuery('') : null
  }), expanded ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 6,
      padding: 4
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement("i", {
    className: "pi pi-search",
    style: {
      color: 'var(--text-color-secondary)',
      fontSize: 13,
      width: 16,
      flexShrink: 0
    }
  }), /*#__PURE__*/React.createElement(TextInput, {
    variant: "dialog",
    placeholder: "Search by content...",
    value: query,
    onChange: e => setQuery(e.target.value)
  }), /*#__PURE__*/React.createElement(IconButton, {
    variant: "bare",
    icon: "pi-search",
    title: "Run content search",
    style: {
      fontSize: 13
    }
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement("i", {
    className: "pi pi-tags",
    style: {
      color: 'var(--text-color-secondary)',
      fontSize: 13,
      width: 16,
      flexShrink: 0
    }
  }), /*#__PURE__*/React.createElement(TextInput, {
    variant: "dialog",
    placeholder: "tag AND tag..."
  }), /*#__PURE__*/React.createElement(IconButton, {
    variant: "bare",
    icon: "pi-search",
    title: "Run tag search",
    style: {
      fontSize: 13
    }
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      fontSize: 12,
      color: 'var(--text-color-secondary)'
    }
  }, /*#__PURE__*/React.createElement("span", null, "Threshold"), /*#__PURE__*/React.createElement("input", {
    type: "range",
    min: "0",
    max: "0.4",
    step: "0.01",
    value: threshold,
    onChange: e => setThreshold(Number(e.target.value)),
    style: {
      flex: 1,
      accentColor: 'var(--primary-color)'
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      fontVariantNumeric: 'tabular-nums',
      width: 34,
      textAlign: 'right'
    }
  }, threshold.toFixed(2))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      fontSize: 12,
      color: 'var(--text-color-secondary)'
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      width: 8,
      height: 8,
      borderRadius: '50%',
      background: '#22c55e',
      flexShrink: 0
    }
  }), /*#__PURE__*/React.createElement("span", null, "Model ready"))) : null);
}
function FilterSection({
  label,
  items,
  selected,
  onToggle
}) {
  const [expanded, setExpanded] = React.useState(items.length > 0 && label === 'Tags');
  const shown = items.slice(0, 20);
  return /*#__PURE__*/React.createElement("div", {
    style: {
      borderBottom: '1px solid var(--surface-border)',
      paddingBottom: 4
    }
  }, /*#__PURE__*/React.createElement(SectionHeader, {
    label: label,
    count: items.length,
    expanded: expanded,
    onToggle: () => setExpanded(!expanded)
  }), expanded ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 1,
      maxHeight: 300,
      overflowY: 'auto',
      paddingLeft: 4
    }
  }, shown.map(([key, count]) => {
    const checked = selected.includes(key);
    return /*#__PURE__*/React.createElement("label", {
      key: key,
      style: {
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        padding: '3px 4px',
        borderRadius: 4,
        cursor: 'pointer',
        fontSize: 12,
        color: 'var(--text-color)',
        background: checked ? 'color-mix(in srgb, var(--primary-color) 15%, transparent)' : 'transparent'
      }
    }, /*#__PURE__*/React.createElement("input", {
      type: "checkbox",
      checked: checked,
      onChange: () => onToggle(key),
      style: {
        margin: 0,
        accentColor: 'var(--primary-color)'
      }
    }), /*#__PURE__*/React.createElement("span", {
      style: {
        flex: 1,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap'
      },
      title: key
    }, key), /*#__PURE__*/React.createElement("span", {
      style: {
        color: 'var(--text-color-secondary)',
        fontSize: 11,
        flexShrink: 0
      }
    }, count));
  })) : null);
}
function FilterPanel({
  query,
  setQuery,
  threshold,
  setThreshold,
  selected,
  onToggle
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      padding: '10px 8px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 16,
      fontWeight: 700,
      color: 'var(--primary-color)',
      padding: '2px 4px 12px'
    }
  }, "Metascan"), /*#__PURE__*/React.createElement("div", {
    style: {
      borderBottom: '1px solid var(--surface-border)',
      paddingBottom: 8,
      marginBottom: 4
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      padding: '4px'
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 11,
      fontWeight: 600,
      letterSpacing: '.4px',
      color: 'var(--text-color-secondary)'
    }
  }, "\u25BC FOLDERS"), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 11,
      color: 'var(--text-color-secondary)'
    }
  }, "0"), /*#__PURE__*/React.createElement(IconButton, {
    variant: "bare",
    glyph: "+",
    title: "New folder",
    style: {
      marginLeft: 'auto',
      fontSize: 15
    }
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      padding: '5px 8px',
      borderRadius: 4,
      background: 'color-mix(in srgb, var(--primary-color) 15%, transparent)',
      fontSize: 13
    }
  }, /*#__PURE__*/React.createElement("i", {
    className: "pi pi-images",
    style: {
      fontSize: 12
    }
  }), "Library", /*#__PURE__*/React.createElement("span", {
    style: {
      marginLeft: 'auto',
      fontSize: 11,
      color: 'var(--text-color-secondary)'
    }
  }, "601")), /*#__PURE__*/React.createElement("p", {
    style: {
      fontSize: 11,
      color: 'var(--text-color-secondary)',
      padding: '8px 4px 0',
      margin: 0,
      lineHeight: 1.5
    }
  }, "No folders yet. Drop images into a new folder or use the + button.")), /*#__PURE__*/React.createElement("div", {
    style: {
      borderBottom: '1px solid var(--surface-border)',
      paddingBottom: 6,
      marginBottom: 4
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      padding: '4px'
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 11,
      fontWeight: 600,
      letterSpacing: '.4px',
      color: 'var(--text-color-secondary)'
    }
  }, "\u25BC SMART FOLDERS"), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 11,
      color: 'var(--text-color-secondary)'
    }
  }, "2"), /*#__PURE__*/React.createElement(IconButton, {
    variant: "bare",
    glyph: "+",
    title: "New smart folder",
    style: {
      marginLeft: 'auto',
      fontSize: 15
    }
  })), [['Water', 4], ['Outside', 3]].map(([n, c]) => /*#__PURE__*/React.createElement("div", {
    key: n,
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      padding: '4px 8px',
      fontSize: 13
    }
  }, /*#__PURE__*/React.createElement("i", {
    className: "pi pi-bolt",
    style: {
      fontSize: 11,
      color: '#a855f7'
    }
  }), n, /*#__PURE__*/React.createElement("span", {
    style: {
      marginLeft: 'auto',
      fontSize: 11,
      color: 'var(--text-color-secondary)'
    }
  }, c)))), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 13,
      fontWeight: 700,
      padding: '8px 4px 4px'
    }
  }, "Filters"), /*#__PURE__*/React.createElement(SearchSection, {
    query: query,
    setQuery: setQuery,
    threshold: threshold,
    setThreshold: setThreshold
  }), FILTERS.map(f => /*#__PURE__*/React.createElement(FilterSection, {
    key: f.label,
    label: f.label,
    items: f.items,
    selected: selected,
    onToggle: onToggle
  })));
}
function ViewMenubar({
  view,
  setView,
  size,
  setSize,
  count,
  hidden,
  setHidden
}) {
  const items = [['Home', 'pi-home'], ['Video', 'pi-video'], ['Images', 'pi-image'], ['Favorites', 'pi-heart']];
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      padding: '6px 12px',
      borderBottom: '1px solid var(--surface-border)',
      background: 'var(--surface-section)',
      flexShrink: 0,
      gap: 4
    }
  }, items.map(([label, icon]) => {
    const active = view === label;
    return /*#__PURE__*/React.createElement("button", {
      key: label,
      onClick: () => setView(label),
      style: {
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '8px 12px',
        borderRadius: 6,
        border: 'none',
        cursor: 'pointer',
        fontFamily: 'inherit',
        fontSize: 14,
        background: active ? 'var(--primary-color)' : 'transparent',
        color: active ? '#fff' : 'var(--text-color)'
      }
    }, /*#__PURE__*/React.createElement("i", {
      className: 'pi ' + icon
    }), label);
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 16,
      paddingLeft: 24
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 2,
      background: 'var(--surface-ground)',
      borderRadius: 6,
      overflow: 'hidden'
    }
  }, ['small', 'medium', 'large'].map(s => /*#__PURE__*/React.createElement("button", {
    key: s,
    onClick: () => setSize(s),
    style: {
      padding: '4px 12px',
      border: 'none',
      cursor: 'pointer',
      fontSize: 13,
      fontWeight: 600,
      fontFamily: 'inherit',
      background: size === s ? 'var(--primary-color)' : 'transparent',
      color: size === s ? '#fff' : 'var(--text-color)'
    }
  }, s[0].toUpperCase()))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 12
    }
  }, /*#__PURE__*/React.createElement("select", {
    className: "ms-control ms-control--dialog",
    style: {
      width: 'auto',
      padding: '4px 8px',
      fontSize: 13
    }
  }, /*#__PURE__*/React.createElement("option", null, "Date Added"), /*#__PURE__*/React.createElement("option", null, "Date Modified"), /*#__PURE__*/React.createElement("option", null, "Name")), /*#__PURE__*/React.createElement("button", {
    onClick: () => setHidden(!hidden),
    title: "Show hidden media",
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      padding: '4px 10px',
      border: '1px solid ' + (hidden ? 'var(--primary-color)' : 'var(--surface-border)'),
      borderRadius: 6,
      background: hidden ? 'var(--primary-color)' : 'var(--surface-card)',
      color: hidden ? '#fff' : 'var(--text-color-secondary)',
      cursor: 'pointer',
      fontSize: 13,
      fontFamily: 'inherit',
      whiteSpace: 'nowrap'
    }
  }, /*#__PURE__*/React.createElement("i", {
    className: "pi pi-eye-slash"
  }), "Show hidden"), /*#__PURE__*/React.createElement(Button, {
    size: "md",
    icon: "pi-play"
  }, "Slideshow"), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      color: 'var(--text-color-secondary)',
      whiteSpace: 'nowrap'
    }
  }, count, " items"))));
}
function ThumbnailCard({
  item,
  size,
  selected,
  onClick
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("div", {
    onClick: onClick,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      position: 'relative',
      width: size,
      height: size,
      borderRadius: 6,
      overflow: 'hidden',
      background: 'var(--surface-card)',
      border: '2px solid ' + (selected ? 'var(--primary-color)' : hover ? 'color-mix(in srgb, var(--primary-color) 50%, transparent)' : 'transparent'),
      cursor: 'pointer',
      transition: 'border-color .15s'
    }
  }, /*#__PURE__*/React.createElement("img", {
    src: item.path,
    alt: "",
    style: {
      width: '100%',
      height: '100%',
      objectFit: 'cover',
      display: 'block'
    }
  }), /*#__PURE__*/React.createElement("button", {
    title: "Toggle favorite",
    style: {
      position: 'absolute',
      top: 4,
      right: 4,
      background: 'rgba(0,0,0,0.5)',
      border: 'none',
      color: item.favorite ? '#fbbf24' : '#ccc',
      fontSize: 16,
      cursor: 'pointer',
      padding: '2px 4px',
      borderRadius: 4,
      lineHeight: 1,
      opacity: item.favorite || hover ? 1 : 0,
      transition: 'opacity .15s'
    }
  }, item.favorite ? '★' : '☆'), item.video ? /*#__PURE__*/React.createElement("span", {
    style: {
      position: 'absolute',
      top: 4,
      left: 4,
      background: 'rgba(0,0,0,0.6)',
      color: '#fff',
      fontSize: 12,
      padding: '2px 6px',
      borderRadius: 4
    }
  }, "\u25B6") : null, /*#__PURE__*/React.createElement("div", {
    title: item.name,
    style: {
      position: 'absolute',
      bottom: 0,
      left: 0,
      right: 0,
      padding: '4px 6px',
      background: 'linear-gradient(transparent, rgba(0,0,0,0.7))',
      color: '#fff',
      fontSize: 11,
      whiteSpace: 'nowrap',
      overflow: 'hidden',
      textOverflow: 'ellipsis',
      opacity: hover ? 1 : 0,
      transition: 'opacity .15s'
    }
  }, item.name));
}
function MetaSection({
  title,
  open = true,
  children
}) {
  return /*#__PURE__*/React.createElement("details", {
    open: open,
    style: {
      border: '1px solid var(--surface-border)',
      borderRadius: 6,
      overflow: 'hidden',
      flex: '0 0 auto'
    }
  }, /*#__PURE__*/React.createElement("summary", {
    style: {
      padding: '6px 10px',
      fontSize: 13,
      fontWeight: 600,
      color: 'var(--text-color)',
      cursor: 'pointer',
      background: 'var(--surface-section)',
      listStyle: 'none',
      display: 'flex',
      alignItems: 'center',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 9
    }
  }, open ? '▼' : '▶'), title), /*#__PURE__*/React.createElement("div", {
    style: {
      padding: '6px 10px',
      display: 'flex',
      flexDirection: 'column',
      gap: 4,
      maxHeight: 320,
      overflowY: 'auto'
    }
  }, children));
}
function MetaField({
  label,
  value
}) {
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 10,
      fontWeight: 600,
      letterSpacing: '.4px',
      textTransform: 'uppercase',
      color: 'var(--text-color-secondary)'
    }
  }, label), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-color)',
      wordBreak: 'break-word'
    }
  }, value));
}
function MetadataPanel({
  item,
  onCopy
}) {
  if (!item) {
    return /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        color: 'var(--text-color-secondary)',
        fontSize: 14,
        padding: 12
      }
    }, "Select a file to view its details");
  }
  return /*#__PURE__*/React.createElement("div", {
    style: {
      padding: 12,
      display: 'flex',
      flexDirection: 'column',
      gap: 8,
      height: '100%',
      overflowY: 'auto',
      boxSizing: 'border-box'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center',
      marginBottom: 4
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontWeight: 700,
      fontSize: 15
    }
  }, "Details"), /*#__PURE__*/React.createElement("button", {
    onClick: onCopy,
    title: "Copy all as JSON",
    style: {
      fontSize: 12,
      padding: '3px 8px',
      border: '1px solid var(--surface-border)',
      borderRadius: 4,
      background: 'var(--surface-card)',
      color: 'var(--text-color)',
      cursor: 'pointer',
      fontFamily: 'inherit'
    }
  }, "Copy All")), /*#__PURE__*/React.createElement(MetaSection, {
    title: 'Tags (' + TAGS.length + ')'
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexWrap: 'wrap',
      gap: 4
    }
  }, TAGS.map(([name, src]) => /*#__PURE__*/React.createElement("span", {
    key: name,
    style: {
      position: 'relative',
      padding: '2px 8px 2px 10px',
      borderRadius: 12,
      background: 'color-mix(in srgb, var(--primary-color) 15%, transparent)',
      color: 'var(--primary-color)',
      fontSize: 11,
      overflow: 'hidden'
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      position: 'absolute',
      top: 0,
      left: 0,
      bottom: 0,
      width: 3,
      background: STRIPE[src]
    }
  }), name)))), /*#__PURE__*/React.createElement(MetaSection, {
    title: "File Information"
  }, /*#__PURE__*/React.createElement(MetaField, {
    label: "Name",
    value: item.name
  }), /*#__PURE__*/React.createElement(MetaField, {
    label: "Path",
    value: '/Users/jk/gws/metascan/assets/media/' + item.name
  }), /*#__PURE__*/React.createElement(MetaField, {
    label: "Size",
    value: "476.0 KB"
  }), /*#__PURE__*/React.createElement(MetaField, {
    label: "Modified",
    value: "8/23/2025, 2:15:02 PM"
  })), /*#__PURE__*/React.createElement(MetaSection, {
    title: "Properties"
  }, /*#__PURE__*/React.createElement(MetaField, {
    label: "Resolution",
    value: "1024 x 1024"
  }), /*#__PURE__*/React.createElement(MetaField, {
    label: "Format",
    value: "JPEG"
  }), /*#__PURE__*/React.createElement(MetaField, {
    label: "Type",
    value: item.video ? 'video' : 'image'
  })), /*#__PURE__*/React.createElement(MetaSection, {
    title: "AI Generation"
  }, /*#__PURE__*/React.createElement(MetaField, {
    label: "Source",
    value: "SwarmUI"
  }), /*#__PURE__*/React.createElement(MetaField, {
    label: "Model",
    value: "realisticStockPhoto_v20"
  }), /*#__PURE__*/React.createElement(MetaField, {
    label: "Sampler",
    value: "lcm"
  }), /*#__PURE__*/React.createElement(MetaField, {
    label: "Steps",
    value: "10"
  }), /*#__PURE__*/React.createElement(MetaField, {
    label: "CFG Scale",
    value: "1"
  }), /*#__PURE__*/React.createElement(MetaField, {
    label: "Seed",
    value: "481314106"
  }), /*#__PURE__*/React.createElement(MetaField, {
    label: "Prompt",
    value: "A full body shot, portrait shot of a villager, on a beach, fantasy setting, an ogre in the background"
  })), /*#__PURE__*/React.createElement(MetaSection, {
    title: "LoRAs (2)",
    open: false
  }, /*#__PURE__*/React.createElement(MetaField, {
    label: "dmd2_sdxl_4step_lora_fp16",
    value: "weight: 1.0"
  }), /*#__PURE__*/React.createElement(MetaField, {
    label: "sts_age_slider_v1_initial_release",
    value: "weight: 0.4"
  })));
}
function LibraryApp() {
  const [view, setView] = React.useState('Home');
  const [size, setSize] = React.useState('medium');
  const [sel, setSel] = React.useState(MEDIA[6]);
  const [query, setQuery] = React.useState('');
  const [threshold, setThreshold] = React.useState(0.2);
  const [filters, setFilters] = React.useState(['beach']);
  const [hidden, setHidden] = React.useState(false);
  const [toast, setToast] = React.useState(null);
  const px = size === 'small' ? 120 : size === 'large' ? 260 : 180;
  const shown = MEDIA.filter(m => view === 'Favorites' ? m.favorite : view === 'Video' ? m.video : view === 'Images' ? !m.video : true);
  const flash = msg => {
    setToast(msg);
    setTimeout(() => setToast(null), 1600);
  };
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      height: '100%'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flex: 1,
      overflow: 'hidden'
    }
  }, /*#__PURE__*/React.createElement("aside", {
    style: {
      width: 250,
      flexShrink: 0,
      borderRight: '1px solid var(--surface-border)',
      overflowY: 'auto'
    }
  }, /*#__PURE__*/React.createElement(FilterPanel, {
    query: query,
    setQuery: setQuery,
    threshold: threshold,
    setThreshold: setThreshold,
    selected: filters,
    onToggle: k => setFilters(filters.includes(k) ? filters.filter(x => x !== k) : filters.concat(k))
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      width: 5,
      cursor: 'col-resize',
      flexShrink: 0
    }
  }), /*#__PURE__*/React.createElement("main", {
    style: {
      flex: 1,
      minWidth: 200,
      display: 'flex',
      flexDirection: 'column',
      minHeight: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 12,
      padding: '8px 12px',
      borderBottom: '1px solid var(--surface-border)',
      background: 'var(--surface-section)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 4,
      marginLeft: 'auto'
    }
  }, ['pi-file-import', 'pi-refresh', 'pi-file-arrow-up', 'pi-copy', 'pi-ellipsis-v', 'pi-cog', 'pi-images'].map(ic => /*#__PURE__*/React.createElement(IconButton, {
    key: ic,
    variant: "bare",
    icon: ic,
    title: ic.replace('pi-', ''),
    style: {
      fontSize: 15,
      width: 30,
      height: 30,
      borderRadius: '50%'
    }
  })))), /*#__PURE__*/React.createElement(ViewMenubar, {
    view: view,
    setView: setView,
    size: size,
    setSize: setSize,
    count: shown.length,
    hidden: hidden,
    setHidden: setHidden
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      padding: '6px 14px',
      borderBottom: '1px solid var(--surface-border)',
      fontSize: 13
    }
  }, /*#__PURE__*/React.createElement("i", {
    className: "pi pi-images",
    style: {
      fontSize: 12,
      color: 'var(--text-color-secondary)'
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      fontWeight: 600
    }
  }, "Library"), filters.length ? /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 12,
      color: 'var(--text-color-secondary)'
    }
  }, "\xB7 filtered by ", filters.join(', ')) : null, /*#__PURE__*/React.createElement("span", {
    style: {
      marginLeft: 'auto',
      fontSize: 13,
      color: 'var(--text-color-secondary)'
    }
  }, shown.length, " items")), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minHeight: 0,
      overflowY: 'auto',
      padding: 8,
      display: 'flex',
      flexWrap: 'wrap',
      gap: 8,
      alignContent: 'flex-start'
    }
  }, shown.map(m => /*#__PURE__*/React.createElement(ThumbnailCard, {
    key: m.path,
    item: m,
    size: px,
    selected: sel && sel.path === m.path,
    onClick: () => setSel(m)
  })))), /*#__PURE__*/React.createElement("div", {
    style: {
      width: 5,
      cursor: 'col-resize',
      flexShrink: 0
    }
  }), /*#__PURE__*/React.createElement("aside", {
    style: {
      width: 350,
      flexShrink: 0,
      borderLeft: '1px solid var(--surface-border)',
      overflowY: 'auto'
    }
  }, /*#__PURE__*/React.createElement(MetadataPanel, {
    item: sel,
    onCopy: () => flash('Copied metadata as JSON')
  }))), toast ? /*#__PURE__*/React.createElement(Toast, {
    kind: "success",
    message: toast
  }) : null);
}
Object.assign(window, {
  LibraryApp
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/library/LibraryApp.jsx", error: String((e && e.message) || e) }); }

// ui_kits/storyboard/BoardScreen.jsx
try { (() => {
const {
  Button,
  IconButton,
  Chip,
  TextInput,
  SceneCard,
  PanelTile,
  AddTile,
  JobBadge
} = window.MetascanDesignSystem_f2fbbd;
function BoardScreen({
  onBack
}) {
  const SB = window.SB;
  const [tree, setTree] = React.useState(SB.tree);
  const [sceneId, setSceneId] = React.useState(1);
  const [panelId, setPanelId] = React.useState(11);
  const [beatId, setBeatId] = React.useState(101);
  const [dialog, setDialog] = React.useState(null);
  const [adding, setAdding] = React.useState(false);
  const [newAction, setNewAction] = React.useState('');
  const [jobs, setJobs] = React.useState({
    105: {
      state: 'running',
      value: 14,
      max: 20
    },
    202: {
      state: 'queued'
    },
    301: {
      state: 'failed',
      error: 'preset 3: node 12 missing input'
    }
  });
  const [error, setError] = React.useState(null);
  const [busy, setBusy] = React.useState(null);
  const scene = tree.scenes.find(s => s.id === sceneId);
  const panel = scene && scene.panels.find(p => p.id === panelId);
  const beat = panel && panel.beats.find(b => b.id === beatId);
  const jobFor = id => jobs[id];
  const mutate = fn => setTree(t => {
    const next = JSON.parse(JSON.stringify(t));
    fn(next);
    return next;
  });
  const patchPanel = body => mutate(t => {
    const p = t.scenes.flatMap(s => s.panels).find(x => x.id === panelId);
    if (p) Object.assign(p, body);
  });
  const patchBeat = body => mutate(t => {
    const b = t.scenes.flatMap(s => s.panels).flatMap(p => p.beats).find(x => x.id === beatId);
    if (b) Object.assign(b, body);
  });
  const moveBeat = (i, dir) => mutate(t => {
    const p = t.scenes.flatMap(s => s.panels).find(x => x.id === panelId);
    if (!p) return;
    const j = i + dir;
    if (j < 0 || j >= p.beats.length) return;
    const tmp = p.beats[i];
    p.beats[i] = p.beats[j];
    p.beats[j] = tmp;
  });
  const addBeat = () => {
    const id = Date.now();
    mutate(t => {
      const p = t.scenes.flatMap(s => s.panels).find(x => x.id === panelId);
      if (p) p.beats.push({
        id,
        duration_s: 2,
        action: 'new beat',
        shot_size: null,
        angle: null,
        lens: null,
        subject_ids: [],
        camera_motion: null,
        camera_amplitude: null,
        camera_speed: null,
        is_cut: 0,
        dialog: [],
        sound: null,
        prompt: null,
        prompt_locked: 0,
        prompt_source: null,
        selected_image_id: null,
        images: []
      });
    });
    setBeatId(id);
  };
  const selectScene = s => {
    setSceneId(s.id);
    const first = s.panels[0];
    setPanelId(first ? first.id : null);
    setBeatId(first && first.beats[0] ? first.beats[0].id : null);
  };
  const selectPanel = p => {
    setPanelId(p.id);
    setBeatId(p.beats[0] ? p.beats[0].id : null);
  };
  const submitPanel = () => {
    const action = newAction.trim();
    if (!action) return;
    const id = Date.now();
    mutate(t => {
      const s = t.scenes.find(x => x.id === sceneId);
      if (s) s.panels.push({
        id,
        sort_order: s.panels.length,
        action,
        duration_s: 2,
        image_loras: [],
        video_loras: [],
        video_prompt: null,
        video_prompt_source: null,
        video_prompt_locked: 0,
        video_prompt_warnings: [],
        video_anchor: null,
        video_compiled_anchor: null,
        videos: [],
        beats: []
      });
    });
    setAdding(false);
    setNewAction('');
    setPanelId(id);
    setBeatId(null);
  };
  const runFakeJob = () => {
    setBusy({
      label: 'synthesizing',
      done: 0,
      total: 12
    });
    let n = 0;
    const t = setInterval(() => {
      n += 3;
      if (n >= 12) {
        clearInterval(t);
        setBusy(null);
        setJobs({});
      } else setBusy({
        label: 'synthesizing',
        done: n,
        total: 12
      });
    }, 600);
  };
  const caption = p => {
    const b = p.beats[0];
    if (!b) return '—';
    const names = b.subject_ids.map(id => (tree.subjects.find(s => s.id === id) || {}).name).filter(Boolean).join(', ');
    return names ? (b.shot_size || '—') + ' · ' + names : b.shot_size || '—';
  };
  const keeper = p => {
    const b = p.beats[0];
    if (!b) return null;
    const img = b.images.find(im => im.id === b.selected_image_id);
    return img ? img.file_path : b.images[0] ? b.images[0].file_path : null;
  };
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      minHeight: 0
    }
  }, /*#__PURE__*/React.createElement("header", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 16,
      padding: '12px 20px',
      borderBottom: '1px solid var(--surface-border)',
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement("a", {
    href: "#",
    onClick: e => {
      e.preventDefault();
      onBack();
    },
    style: {
      color: 'var(--text-color-secondary)',
      fontSize: 13,
      textDecoration: 'none',
      flexShrink: 0
    }
  }, "\u2190 Library"), /*#__PURE__*/React.createElement("h2", {
    style: {
      margin: 0,
      fontSize: 16,
      color: 'var(--text-color)',
      flex: 1,
      minWidth: 0,
      overflow: 'hidden',
      textOverflow: 'ellipsis',
      whiteSpace: 'nowrap',
      fontWeight: 600
    }
  }, tree.name), /*#__PURE__*/React.createElement(Chip, {
    tone: "primary",
    title: "Video model and mode driving the compiled video prompts \u2014 change in Storyboard settings"
  }, "MiniMax H3 \xB7 ", tree.video_mode), busy ? /*#__PURE__*/React.createElement(Chip, null, busy.label, " ", busy.done, "/", busy.total) : null, error ? /*#__PURE__*/React.createElement(Chip, {
    tone: "danger",
    title: error,
    onDismiss: () => setError(null)
  }, error) : null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement(Button, {
    variant: "quiet",
    icon: "pi-file-import",
    onClick: () => setDialog({
      kind: 'import'
    })
  }, "Import text"), /*#__PURE__*/React.createElement(Button, {
    variant: "quiet",
    icon: "pi-sparkles",
    onClick: () => setDialog({
      kind: 'compose'
    })
  }, "Compose"), /*#__PURE__*/React.createElement(Button, {
    variant: "quiet",
    onClick: runFakeJob
  }, "Synthesize"), /*#__PURE__*/React.createElement(Button, {
    variant: "quiet"
  }, "Compile video prompts"), /*#__PURE__*/React.createElement(Button, {
    variant: "primary",
    onClick: runFakeJob
  }, "Generate all"), /*#__PURE__*/React.createElement(Button, {
    variant: "quiet"
  }, "Generate video"), /*#__PURE__*/React.createElement(Button, {
    variant: "quiet",
    style: {
      color: 'var(--danger-color)'
    },
    onClick: () => setError('panel 31: comfy queue rejected the prompt')
  }, "Cancel"), /*#__PURE__*/React.createElement(IconButton, {
    variant: "outline",
    size: "lg",
    icon: "pi-cog",
    title: "Storyboard settings",
    onClick: () => setDialog({
      kind: 'settings'
    })
  }))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flex: 1,
      minHeight: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      flex: 1,
      minWidth: 0,
      minHeight: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 10,
      padding: '12px 20px',
      overflowX: 'auto',
      borderBottom: '1px solid var(--surface-border)',
      flexShrink: 0
    }
  }, tree.scenes.map(s => /*#__PURE__*/React.createElement(SceneCard, {
    key: s.id,
    name: s.name,
    subtitle: s.subtitle,
    setting: s.setting,
    thumbs: s.panels.slice(0, 6).map(keeper),
    active: s.id === sceneId,
    onClick: () => selectScene(s),
    actions: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(IconButton, {
      variant: "corner",
      glyph: "\u25B6",
      title: "Render scene videos",
      style: {
        fontSize: 8
      },
      onClick: e => e.stopPropagation()
    }), /*#__PURE__*/React.createElement(IconButton, {
      variant: "corner",
      glyph: "\u270E",
      title: "Edit scene",
      style: {
        fontSize: 10
      },
      onClick: e => {
        e.stopPropagation();
        setDialog({
          kind: 'scene',
          scene: s
        });
      }
    }), /*#__PURE__*/React.createElement(IconButton, {
      variant: "corner",
      glyph: "\xD7",
      destructive: true,
      title: "Delete scene",
      onClick: e => {
        e.stopPropagation();
        setDialog({
          kind: 'deleteScene',
          scene: s
        });
      }
    }))
  })), /*#__PURE__*/React.createElement(AddTile, {
    kind: "scene",
    label: "Scene",
    onClick: () => setDialog({
      kind: 'scene',
      scene: null
    })
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minHeight: 0,
      overflowY: 'auto',
      padding: '16px 20px',
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))',
      gap: 14,
      alignContent: 'start'
    }
  }, scene ? scene.panels.map(p => {
    const job = p.beats.map(b => jobs[b.id]).find(Boolean);
    return /*#__PURE__*/React.createElement(PanelTile, {
      key: p.id,
      src: keeper(p),
      dimmed: !!p.beats[0] && !p.beats[0].selected_image_id,
      caption: caption(p),
      active: p.id === panelId,
      locked: p.beats[0] && p.beats[0].prompt_locked === 1,
      videoCount: p.videos.length,
      job: job ? /*#__PURE__*/React.createElement(JobBadge, {
        state: job.state,
        value: job.value,
        max: job.max,
        error: job.error
      }) : null,
      onClick: () => selectPanel(p),
      onDelete: () => setDialog({
        kind: 'deletePanel',
        panel: p
      })
    });
  }) : /*#__PURE__*/React.createElement("div", {
    className: "ms-hint",
    style: {
      gridColumn: '1 / -1',
      fontSize: 14,
      padding: '24px 0'
    }
  }, "Select a scene to see its panels."), /*#__PURE__*/React.createElement(AddTile, {
    kind: "panel",
    label: "Panel",
    onClick: () => setAdding(true)
  }, adding ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(TextInput, {
    autoFocus: true,
    placeholder: "Action",
    value: newAction,
    style: {
      fontSize: 12,
      padding: '5px 8px'
    },
    onClick: e => e.stopPropagation(),
    onChange: e => setNewAction(e.target.value),
    onKeyDown: e => {
      if (e.key === 'Enter') submitPanel();
      if (e.key === 'Escape') {
        setAdding(false);
        setNewAction('');
      }
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 6,
      marginTop: 6
    }
  }, /*#__PURE__*/React.createElement(Button, {
    size: "xs",
    disabled: !newAction.trim(),
    onClick: e => {
      e.stopPropagation();
      submitPanel();
    }
  }, "Add"), /*#__PURE__*/React.createElement(Button, {
    size: "xs",
    onClick: e => {
      e.stopPropagation();
      setAdding(false);
      setNewAction('');
    }
  }, "Cancel"))) : null)), panel ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    title: "Drag to resize",
    style: {
      position: 'relative',
      zIndex: 5,
      flexShrink: 0,
      height: 7,
      margin: '-3px 0',
      cursor: 'row-resize'
    }
  }), /*#__PURE__*/React.createElement(window.ShotDetail, {
    panel: panel,
    subjects: tree.subjects,
    selectedBeatId: beatId,
    onSelectBeat: setBeatId,
    onPatchPanel: patchPanel,
    onMoveBeat: moveBeat,
    onAddBeat: addBeat,
    jobFor: jobFor
  })) : null), panel ? /*#__PURE__*/React.createElement(window.SidePanel, {
    panel: panel,
    scene: scene,
    beat: beat,
    subjects: tree.subjects,
    selectedBeatId: beatId,
    onSelectBeat: setBeatId,
    onPatchBeat: patchBeat,
    onPatchPanel: patchPanel
  }) : null), dialog && dialog.kind === 'compose' ? /*#__PURE__*/React.createElement(window.ComposeDialog, {
    onClose: () => setDialog(null)
  }) : null, dialog && dialog.kind === 'scene' ? /*#__PURE__*/React.createElement(window.SceneEditDialog, {
    scene: dialog.scene,
    onClose: () => setDialog(null)
  }) : null, dialog && dialog.kind === 'import' ? /*#__PURE__*/React.createElement(window.ImportTextDialog, {
    onClose: () => setDialog(null)
  }) : null, dialog && dialog.kind === 'settings' ? /*#__PURE__*/React.createElement(window.SettingsDialog, {
    onClose: () => setDialog(null)
  }) : null, dialog && dialog.kind === 'deletePanel' ? /*#__PURE__*/React.createElement(window.DeleteImagesDialog, {
    title: "Delete panel?",
    message: "This panel has generated images. Delete them permanently, or keep them visible in the media library?",
    imageCount: dialog.panel.videos.length + dialog.panel.beats.reduce((n, b) => n + b.images.length, 0),
    onPurge: () => {
      mutate(t => {
        const s = t.scenes.find(x => x.id === sceneId);
        s.panels = s.panels.filter(p => p.id !== dialog.panel.id);
      });
      setDialog(null);
      setPanelId(null);
    },
    onKeep: () => {
      mutate(t => {
        const s = t.scenes.find(x => x.id === sceneId);
        s.panels = s.panels.filter(p => p.id !== dialog.panel.id);
      });
      setDialog(null);
      setPanelId(null);
    },
    onCancel: () => setDialog(null)
  }) : null, dialog && dialog.kind === 'deleteScene' ? /*#__PURE__*/React.createElement(window.DeleteImagesDialog, {
    title: 'Delete scene "' + dialog.scene.name + '"?',
    message: "Its panels have generated images. Delete them permanently, or keep them visible in the media library?",
    imageCount: dialog.scene.panels.reduce((n, p) => n + p.videos.length + p.beats.reduce((m, b) => m + b.images.length, 0), 0),
    onPurge: () => {
      mutate(t => {
        t.scenes = t.scenes.filter(s => s.id !== dialog.scene.id);
      });
      setDialog(null);
      setSceneId(tree.scenes[0].id);
    },
    onKeep: () => {
      mutate(t => {
        t.scenes = t.scenes.filter(s => s.id !== dialog.scene.id);
      });
      setDialog(null);
      setSceneId(tree.scenes[0].id);
    },
    onCancel: () => setDialog(null)
  }) : null);
}
Object.assign(window, {
  BoardScreen
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/storyboard/BoardScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/storyboard/Dialogs.jsx
try { (() => {
const {
  Button,
  IconButton,
  Chip,
  Field,
  TextInput,
  Select,
  Textarea,
  Checkbox,
  Dialog,
  ConfirmBanner
} = window.MetascanDesignSystem_f2fbbd;
function NewStoryboardDialog({
  onClose,
  onCreated
}) {
  const [name, setName] = React.useState('');
  const [video, setVideo] = React.useState('minimax');
  return /*#__PURE__*/React.createElement(Dialog, {
    title: "New storyboard",
    onDismiss: onClose,
    actions: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Button, {
      variant: "primary",
      size: "lg",
      disabled: !name.trim(),
      onClick: () => onCreated(name.trim())
    }, "Create"), /*#__PURE__*/React.createElement(Button, {
      variant: "secondary",
      size: "lg",
      onClick: onClose
    }, "Cancel"))
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 14
    }
  }, /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Name",
    htmlFor: "sb-name"
  }, /*#__PURE__*/React.createElement(TextInput, {
    id: "sb-name",
    variant: "dialog",
    placeholder: "e.g. Coffee shop meet-cute",
    value: name,
    onChange: e => setName(e.target.value)
  })), /*#__PURE__*/React.createElement(Field, {
    row: true
  }, /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Target model"
  }, /*#__PURE__*/React.createElement(Select, {
    variant: "dialog",
    includeEmpty: false,
    options: window.SB.TARGET_MODELS,
    defaultValue: "flux1"
  })), /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Aspect ratio"
  }, /*#__PURE__*/React.createElement(Select, {
    variant: "dialog",
    includeEmpty: false,
    options: window.SB.ASPECT_RATIOS,
    defaultValue: "16:9"
  }))), /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Workflow preset",
    hint: /*#__PURE__*/React.createElement(React.Fragment, null, "None yet. ", /*#__PURE__*/React.createElement("button", {
      type: "button",
      className: "ms-btn ms-btn--link",
      style: {
        fontSize: 12
      }
    }, "Register one"))
  }, /*#__PURE__*/React.createElement(Select, {
    variant: "dialog",
    placeholder: "None",
    options: [{
      value: 2,
      label: 'flux1 stills (t2i)'
    }, {
      value: 3,
      label: 'flux1 ref (ref)'
    }],
    defaultValue: 2
  })), /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Video target (optional)"
  }, /*#__PURE__*/React.createElement(Select, {
    variant: "dialog",
    includeEmpty: false,
    value: video,
    onChange: e => setVideo(e.target.value),
    options: [{
      value: '',
      label: 'None — stills only'
    }, {
      value: 'minimax',
      label: 'MiniMax H3'
    }]
  })), video ? /*#__PURE__*/React.createElement(Field, {
    row: true
  }, /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Video mode"
  }, /*#__PURE__*/React.createElement(Select, {
    variant: "dialog",
    includeEmpty: false,
    options: window.SB.VIDEO_MODES,
    defaultValue: "ref2va"
  })), /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Video workflow preset"
  }, /*#__PURE__*/React.createElement(Select, {
    variant: "dialog",
    placeholder: "None",
    options: [{
      value: 4,
      label: 'h3 ref2v'
    }],
    defaultValue: 4
  }))) : null, /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Batch size"
  }, /*#__PURE__*/React.createElement(TextInput, {
    variant: "dialog",
    type: "number",
    min: "1",
    max: "16",
    defaultValue: 4
  })), /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Style block (optional)"
  }, /*#__PURE__*/React.createElement(Textarea, {
    variant: "dialog",
    rows: 3,
    placeholder: "Shared style/quality tags applied to every panel prompt",
    defaultValue: "muted palette, 35mm film grain, no text"
  })), /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Negative prompt (optional)"
  }, /*#__PURE__*/React.createElement(Textarea, {
    variant: "dialog",
    rows: 2,
    placeholder: "Things to avoid"
  }))));
}
function ComposeDialog({
  onClose
}) {
  const SB = window.SB;
  const [premise, setPremise] = React.useState(SB.tree.source_text);
  const [stages, setStages] = React.useState({
    outline: false,
    scenes: true,
    shots: true,
    beats: true
  });
  const [confirming, setConfirming] = React.useState(false);
  const checked = SB.COMPOSE_STAGES.filter(s => stages[s]);
  return /*#__PURE__*/React.createElement(Dialog, {
    size: "md",
    title: "Compose story",
    onDismiss: onClose,
    actions: /*#__PURE__*/React.createElement(Button, {
      variant: "secondary",
      size: "lg",
      onClick: onClose
    }, "Close")
  }, /*#__PURE__*/React.createElement("p", {
    className: "ms-hint",
    style: {
      fontSize: 12,
      marginBottom: 14
    }
  }, "Write a premise, generate an outline, then build scenes, shots and beats from it."), /*#__PURE__*/React.createElement("p", {
    className: "ms-hint",
    style: {
      fontSize: 12,
      marginBottom: 14
    }
  }, "Video: MiniMax H3 \xB7 ref2va (change in Settings)"), /*#__PURE__*/React.createElement("label", {
    className: "ms-label ms-label--plain",
    style: {
      display: 'block',
      margin: '14px 0 6px'
    }
  }, "Premise"), /*#__PURE__*/React.createElement(Textarea, {
    variant: "dialog",
    rows: 5,
    value: premise,
    onChange: e => setPremise(e.target.value)
  }), /*#__PURE__*/React.createElement("div", {
    className: "ms-dialog__actions",
    style: {
      marginTop: 14
    }
  }, /*#__PURE__*/React.createElement(Button, {
    variant: "primary",
    size: "lg",
    disabled: !premise.trim()
  }, "Generate outline")), /*#__PURE__*/React.createElement("label", {
    className: "ms-label ms-label--plain",
    style: {
      display: 'block',
      margin: '14px 0 6px'
    }
  }, "Outline"), /*#__PURE__*/React.createElement(Textarea, {
    variant: "dialog",
    mono: true,
    rows: 8,
    defaultValue: SB.tree.outline
  }), /*#__PURE__*/React.createElement("label", {
    className: "ms-label ms-label--plain",
    style: {
      display: 'block',
      margin: '14px 0 6px'
    }
  }, "Stages to build"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexWrap: 'wrap',
      gap: 16
    }
  }, SB.COMPOSE_STAGES.map(s => /*#__PURE__*/React.createElement(Checkbox, {
    key: s,
    size: "md",
    label: s[0].toUpperCase() + s.slice(1),
    checked: stages[s],
    onChange: () => setStages({
      ...stages,
      [s]: !stages[s]
    })
  }))), /*#__PURE__*/React.createElement("div", {
    className: "ms-dialog__actions",
    style: {
      marginTop: 14
    }
  }, /*#__PURE__*/React.createElement(Button, {
    variant: "primary",
    size: "lg",
    disabled: checked.length === 0,
    onClick: () => setConfirming(true)
  }, "Build checked stages")), confirming ? /*#__PURE__*/React.createElement(ConfirmBanner, {
    size: "lg",
    message: "This replaces existing content \u2014 continue?",
    actions: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Button, {
      variant: "dangerSolid",
      size: "lg",
      onClick: onClose
    }, "Continue"), /*#__PURE__*/React.createElement(Button, {
      variant: "secondary",
      size: "lg",
      onClick: () => setConfirming(false)
    }, "Cancel"))
  }) : null);
}
function DeleteImagesDialog({
  title,
  message,
  imageCount,
  onPurge,
  onKeep,
  onCancel
}) {
  return /*#__PURE__*/React.createElement(Dialog, {
    nested: true,
    title: title,
    message: message,
    meta: imageCount === undefined ? undefined : imageCount + ' generated image' + (imageCount === 1 ? '' : 's') + ' affected.',
    onDismiss: onCancel,
    actions: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Button, {
      variant: "danger",
      size: "lg",
      onClick: onPurge
    }, "Delete images permanently"), /*#__PURE__*/React.createElement(Button, {
      variant: "primary",
      size: "lg",
      onClick: onKeep
    }, "Keep images in library"), /*#__PURE__*/React.createElement(Button, {
      variant: "secondary",
      size: "lg",
      onClick: onCancel
    }, "Cancel"))
  });
}
function SceneEditDialog({
  scene,
  onClose
}) {
  const s = scene ?? {};
  return /*#__PURE__*/React.createElement(Dialog, {
    size: "md",
    title: scene ? 'Edit scene' : 'New scene',
    onDismiss: onClose,
    actions: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Button, {
      variant: "primary",
      size: "lg",
      onClick: onClose
    }, "Save"), /*#__PURE__*/React.createElement(Button, {
      variant: "secondary",
      size: "lg",
      onClick: onClose
    }, "Cancel"))
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 14
    }
  }, /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Name"
  }, /*#__PURE__*/React.createElement(TextInput, {
    variant: "dialog",
    defaultValue: s.name ?? '',
    placeholder: "e.g. Kitchen, dawn"
  })), /*#__PURE__*/React.createElement(Field, {
    row: true
  }, /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Location"
  }, /*#__PURE__*/React.createElement(TextInput, {
    variant: "dialog",
    defaultValue: s.location ?? ''
  })), /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Time of day"
  }, /*#__PURE__*/React.createElement(TextInput, {
    variant: "dialog",
    defaultValue: s.time_of_day ?? ''
  }))), /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Setting"
  }, /*#__PURE__*/React.createElement(Textarea, {
    variant: "dialog",
    rows: 3,
    defaultValue: s.setting ?? ''
  })), /*#__PURE__*/React.createElement(Field, {
    row: true
  }, /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Mood"
  }, /*#__PURE__*/React.createElement(TextInput, {
    variant: "dialog",
    defaultValue: s.mood ?? ''
  })), /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Lighting"
  }, /*#__PURE__*/React.createElement(TextInput, {
    variant: "dialog",
    defaultValue: s.lighting ?? ''
  }))), /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Setting reference",
    hint: "Pick an image from the library to describe this setting."
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8,
      alignItems: 'center'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: 72,
      height: 72,
      borderRadius: 6,
      overflow: 'hidden',
      background: 'var(--surface-ground)',
      border: '1px dashed var(--surface-border)'
    }
  }), /*#__PURE__*/React.createElement(Button, {
    size: "md"
  }, "Choose reference\u2026"), /*#__PURE__*/React.createElement(Button, {
    size: "md",
    variant: "quiet"
  }, "Describe with VLM")))));
}
Object.assign(window, {
  NewStoryboardDialog,
  ComposeDialog,
  DeleteImagesDialog,
  SceneEditDialog
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/storyboard/Dialogs.jsx", error: String((e && e.message) || e) }); }

// ui_kits/storyboard/LandingScreen.jsx
try { (() => {
const {
  Button,
  BoardRow
} = window.MetascanDesignSystem_f2fbbd;
function LandingScreen({
  onOpen
}) {
  const [showCreate, setShowCreate] = React.useState(false);
  const [deleteTarget, setDeleteTarget] = React.useState(null);
  const [boards, setBoards] = React.useState(window.SB.list);
  return /*#__PURE__*/React.createElement("div", {
    style: {
      height: '100%',
      overflowY: 'auto',
      padding: '32px 20px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: 720,
      margin: '0 auto',
      padding: '0 0 60px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      marginBottom: 20
    }
  }, /*#__PURE__*/React.createElement("h2", {
    style: {
      margin: 0,
      fontSize: 20,
      color: 'var(--text-color)',
      fontWeight: 600
    }
  }, "Storyboards"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement(Button, {
    variant: "primary",
    size: "lg",
    icon: "pi-plus",
    onClick: () => setShowCreate(true)
  }, "New storyboard"), /*#__PURE__*/React.createElement(Button, {
    variant: "secondary",
    size: "lg"
  }, "Workflow presets\u2026"))), boards.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "ms-empty-state"
  }, "No storyboards yet \u2014 create one and paste your scene text.") : /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 8
    }
  }, boards.map(b => /*#__PURE__*/React.createElement(BoardRow, {
    key: b.id,
    name: b.name,
    meta: b.aspect_ratio + ' · ' + b.target_model + ' · updated ' + b.updated_at,
    actions: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Button, {
      onClick: () => onOpen(b.id)
    }, "Open"), /*#__PURE__*/React.createElement(Button, {
      variant: "danger",
      onClick: () => setDeleteTarget(b)
    }, "Delete"))
  })))), showCreate ? /*#__PURE__*/React.createElement(window.NewStoryboardDialog, {
    onClose: () => setShowCreate(false),
    onCreated: () => {
      setShowCreate(false);
      onOpen(1);
    }
  }) : null, deleteTarget ? /*#__PURE__*/React.createElement(window.DeleteImagesDialog, {
    title: 'Delete storyboard "' + deleteTarget.name + '"?',
    message: "Its scenes, panels, and library folder are removed. What should happen to the generated images?",
    onPurge: () => {
      setBoards(boards.filter(b => b.id !== deleteTarget.id));
      setDeleteTarget(null);
    },
    onKeep: () => {
      setBoards(boards.filter(b => b.id !== deleteTarget.id));
      setDeleteTarget(null);
    },
    onCancel: () => setDeleteTarget(null)
  }) : null);
}
Object.assign(window, {
  LandingScreen
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/storyboard/LandingScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/storyboard/MoreDialogs.jsx
try { (() => {
const {
  Button,
  IconButton,
  Field,
  TextInput,
  Select,
  Textarea,
  Dialog,
  ConfirmBanner
} = window.MetascanDesignSystem_f2fbbd;
function ImportTextDialog({
  onClose
}) {
  const [text, setText] = React.useState('');
  const [confirming, setConfirming] = React.useState(false);
  return /*#__PURE__*/React.createElement(Dialog, {
    size: "md",
    title: "Import text",
    onDismiss: onClose,
    actions: confirming ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Button, {
      variant: "dangerSolid",
      size: "lg",
      onClick: onClose
    }, "Replace structure"), /*#__PURE__*/React.createElement(Button, {
      variant: "secondary",
      size: "lg",
      onClick: () => setConfirming(false)
    }, "Cancel")) : /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Button, {
      variant: "primary",
      size: "lg",
      disabled: !text.trim(),
      onClick: () => setConfirming(true)
    }, "Import"), /*#__PURE__*/React.createElement(Button, {
      variant: "secondary",
      size: "lg",
      onClick: onClose
    }, "Cancel"))
  }, /*#__PURE__*/React.createElement("p", {
    className: "ms-hint",
    style: {
      fontSize: 12,
      marginBottom: 14
    }
  }, "Paste your scene text \u2014 subjects, locations, one or two sentences per shot."), /*#__PURE__*/React.createElement(Textarea, {
    variant: "dialog",
    rows: 12,
    placeholder: "Paste scene text here\u2026",
    value: text,
    onChange: e => setText(e.target.value)
  }), confirming ? /*#__PURE__*/React.createElement(ConfirmBanner, {
    size: "lg",
    message: "This storyboard already has scenes. Re-parsing replaces all scenes, panels and hand-edited prompts."
  }) : null);
}
function SettingsDialog({
  onClose
}) {
  const SB = window.SB;
  const t = SB.tree;
  const [picker, setPicker] = React.useState(false);
  return /*#__PURE__*/React.createElement(Dialog, {
    size: "md",
    title: "Storyboard settings",
    onDismiss: onClose,
    actions: /*#__PURE__*/React.createElement(Button, {
      variant: "secondary",
      size: "lg",
      onClick: onClose
    }, "Close")
  }, /*#__PURE__*/React.createElement("section", {
    style: {
      marginBottom: 22
    }
  }, /*#__PURE__*/React.createElement("h4", {
    style: {
      margin: '0 0 12px',
      fontSize: 14,
      fontWeight: 600
    }
  }, "Fields"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 14
    }
  }, /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Name"
  }, /*#__PURE__*/React.createElement(TextInput, {
    variant: "dialog",
    defaultValue: t.name
  })), /*#__PURE__*/React.createElement(Field, {
    row: true
  }, /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Aspect ratio"
  }, /*#__PURE__*/React.createElement(Select, {
    variant: "dialog",
    includeEmpty: false,
    options: SB.ASPECT_RATIOS,
    defaultValue: t.aspect_ratio
  })), /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Target model"
  }, /*#__PURE__*/React.createElement(Select, {
    variant: "dialog",
    includeEmpty: false,
    options: SB.TARGET_MODELS,
    defaultValue: t.target_model
  }))), /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Workflow preset"
  }, /*#__PURE__*/React.createElement(Select, {
    variant: "dialog",
    placeholder: "None",
    options: [{
      value: 2,
      label: 'flux1 stills (t2i)'
    }],
    defaultValue: 2
  })), /*#__PURE__*/React.createElement(Field, {
    row: true
  }, /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Batch size"
  }, /*#__PURE__*/React.createElement(TextInput, {
    variant: "dialog",
    type: "number",
    min: "1",
    max: "16",
    defaultValue: t.batch_size
  })), /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Base seed"
  }, /*#__PURE__*/React.createElement(TextInput, {
    variant: "dialog",
    type: "number",
    defaultValue: t.base_seed
  }))), /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Style block"
  }, /*#__PURE__*/React.createElement(Textarea, {
    variant: "dialog",
    rows: 2,
    defaultValue: t.style_block
  })), /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Negative"
  }, /*#__PURE__*/React.createElement(Textarea, {
    variant: "dialog",
    rows: 2,
    defaultValue: t.negative
  })), /*#__PURE__*/React.createElement(Field, {
    row: true
  }, /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Video target"
  }, /*#__PURE__*/React.createElement(Select, {
    variant: "dialog",
    placeholder: "None",
    options: [{
      value: 'minimax',
      label: 'MiniMax H3'
    }],
    defaultValue: "minimax"
  })), /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Video mode"
  }, /*#__PURE__*/React.createElement(Select, {
    variant: "dialog",
    includeEmpty: false,
    options: SB.VIDEO_MODES,
    defaultValue: t.video_mode
  }))), /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Video output directory"
  }, /*#__PURE__*/React.createElement(Select, {
    variant: "dialog",
    placeholder: "Default",
    options: ['/Users/jk/gws/metascan/assets/media']
  })), /*#__PURE__*/React.createElement(Field, {
    row: true
  }, /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Video name prefix"
  }, /*#__PURE__*/React.createElement(TextInput, {
    variant: "dialog",
    placeholder: "{board}_{scene}_{shot}"
  })), /*#__PURE__*/React.createElement(Field, {
    plainLabel: true,
    label: "Image name prefix"
  }, /*#__PURE__*/React.createElement(TextInput, {
    variant: "dialog",
    placeholder: "{board}_{shot}_{beat}"
  }))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement(Button, {
    variant: "primary",
    size: "lg"
  }, "Save")))), /*#__PURE__*/React.createElement("section", null, /*#__PURE__*/React.createElement("h4", {
    style: {
      margin: '0 0 12px',
      fontSize: 14,
      fontWeight: 600
    }
  }, "Subjects"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 14
    }
  }, t.subjects.map(s => /*#__PURE__*/React.createElement("div", {
    key: s.id,
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 8,
      padding: 12,
      border: '1px solid var(--surface-border)',
      borderRadius: 8,
      background: 'var(--surface-card)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8
    }
  }, /*#__PURE__*/React.createElement(TextInput, {
    variant: "dialog",
    style: {
      flex: '0 0 130px'
    },
    defaultValue: s.name
  }), /*#__PURE__*/React.createElement(TextInput, {
    variant: "dialog",
    defaultValue: s.description
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8
    }
  }, /*#__PURE__*/React.createElement(TextInput, {
    variant: "dialog",
    style: {
      flex: 1
    },
    placeholder: "lora file\u2026",
    defaultValue: s.lora_name ?? ''
  }), /*#__PURE__*/React.createElement(TextInput, {
    variant: "dialog",
    style: {
      flex: '0 0 80px'
    },
    type: "number",
    step: "0.05",
    defaultValue: s.lora_strength ?? 1
  }), /*#__PURE__*/React.createElement(TextInput, {
    variant: "dialog",
    style: {
      flex: '0 0 110px'
    },
    placeholder: "voice"
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 10,
      alignItems: 'center'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: 56,
      height: 56,
      borderRadius: 6,
      border: '1px dashed var(--surface-border)',
      background: 'var(--surface-ground)',
      flexShrink: 0
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      width: 56,
      height: 56,
      borderRadius: 6,
      border: '1px dashed var(--surface-border)',
      background: 'var(--surface-ground)',
      flexShrink: 0
    }
  }), /*#__PURE__*/React.createElement(Button, {
    size: "md",
    onClick: () => setPicker(true)
  }, "Choose reference\u2026"), /*#__PURE__*/React.createElement(Button, {
    size: "md",
    variant: "quiet"
  }, "Describe with VLM"), /*#__PURE__*/React.createElement(IconButton, {
    glyph: "\u2715",
    destructive: true,
    title: "Remove subject"
  })))), /*#__PURE__*/React.createElement(Button, {
    variant: "dashed",
    size: "md",
    style: {
      alignSelf: 'flex-start'
    }
  }, "+ Subject"))), picker ? /*#__PURE__*/React.createElement(window.ReferencePicker, {
    onClose: () => setPicker(false)
  }) : null);
}
function ReferencePicker({
  onClose
}) {
  const F = window.SB.F;
  const [q, setQ] = React.useState('');
  const all = Array.from({
    length: 18
  }, (_, i) => ({
    path: F(i + 1),
    name: '1414-A full body shot ' + (i + 1) + '.jpg'
  }));
  const shown = all.filter(m => m.name.toLowerCase().includes(q.trim().toLowerCase()));
  return /*#__PURE__*/React.createElement("div", {
    className: "ms-dialog-overlay ms-dialog-overlay--nested",
    onClick: e => {
      if (e.target === e.currentTarget) onClose();
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "ms-dialog ms-dialog--lg"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 12,
      marginBottom: 12
    }
  }, /*#__PURE__*/React.createElement("h4", {
    style: {
      margin: 0,
      fontSize: 14,
      flexShrink: 0,
      fontWeight: 600
    }
  }, "Choose a reference image"), /*#__PURE__*/React.createElement(TextInput, {
    variant: "dialog",
    placeholder: "Filter by file name\u2026",
    value: q,
    onChange: e => setQ(e.target.value)
  }), /*#__PURE__*/React.createElement(IconButton, {
    variant: "bare",
    glyph: "\xD7",
    title: "Close",
    onClick: onClose
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))',
      gap: 10,
      overflowY: 'auto',
      minHeight: 120
    }
  }, shown.map(m => /*#__PURE__*/React.createElement("button", {
    key: m.path,
    type: "button",
    onClick: onClose,
    style: {
      background: 'var(--surface-card)',
      border: '1px solid var(--surface-border)',
      borderRadius: 8,
      padding: 6,
      cursor: 'pointer',
      display: 'flex',
      flexDirection: 'column',
      gap: 4,
      textAlign: 'left'
    }
  }, /*#__PURE__*/React.createElement("img", {
    src: m.path,
    alt: "",
    style: {
      width: '100%',
      aspectRatio: 1,
      objectFit: 'cover',
      borderRadius: 4,
      display: 'block'
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 11,
      color: 'var(--text-color-secondary)',
      overflow: 'hidden',
      textOverflow: 'ellipsis',
      whiteSpace: 'nowrap'
    }
  }, m.name))))));
}
Object.assign(window, {
  ImportTextDialog,
  SettingsDialog,
  ReferencePicker
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/storyboard/MoreDialogs.jsx", error: String((e && e.message) || e) }); }

// ui_kits/storyboard/ShotDetail.jsx
try { (() => {
const {
  Button,
  IconButton,
  Field,
  TextInput,
  LoraListEditor,
  BeatPill,
  VideoTakeTile,
  ConfirmBanner,
  JobBadge
} = window.MetascanDesignSystem_f2fbbd;
function ShotDetail({
  panel,
  subjects,
  selectedBeatId,
  onSelectBeat,
  onPatchPanel,
  onMoveBeat,
  onAddBeat,
  jobFor
}) {
  const [rebeatConfirm, setRebeatConfirm] = React.useState(false);
  const total = panel.beats.reduce((s, b) => s + b.duration_s, 0);
  const over = total > 15;
  return /*#__PURE__*/React.createElement("div", {
    style: {
      flexShrink: 0,
      maxHeight: '44vh',
      overflowY: 'auto',
      borderTop: '1px solid var(--surface-border)',
      background: 'var(--surface-card)',
      padding: '12px 20px 16px',
      display: 'flex',
      flexDirection: 'column',
      gap: 12
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 12
    }
  }, /*#__PURE__*/React.createElement("h4", {
    style: {
      margin: 0,
      fontSize: 14,
      color: 'var(--text-color)',
      fontWeight: 600
    }
  }, "Panel ", (panel.sort_order ?? 0) + 1), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8,
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement(Button, {
    size: "sm",
    disabled: panel.beats.length === 0
  }, "Reroll shot"), /*#__PURE__*/React.createElement(Button, {
    size: "sm",
    disabled: panel.beats.length === 0
  }, "Re-synth shot"))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement(Field, {
    label: "Action"
  }, /*#__PURE__*/React.createElement(TextInput, {
    style: {
      maxWidth: 320
    },
    value: panel.action,
    onChange: e => onPatchPanel({
      action: e.target.value
    })
  })), /*#__PURE__*/React.createElement(Field, {
    label: "Duration (s)"
  }, /*#__PURE__*/React.createElement(TextInput, {
    readOnly: true,
    style: {
      maxWidth: 120
    },
    value: total.toFixed(1),
    title: "Derived from the sum of this shot's beat durations. Edit the beats to change it."
  })), /*#__PURE__*/React.createElement(LoraListEditor, {
    label: "Image LoRAs",
    entries: panel.image_loras,
    options: window.SB.loraOptions,
    listId: "lora-img",
    onChange: entries => onPatchPanel({
      image_loras: entries
    })
  }), /*#__PURE__*/React.createElement(LoraListEditor, {
    label: "Video LoRAs",
    entries: panel.video_loras,
    options: window.SB.loraOptions,
    listId: "lora-vid",
    onChange: entries => onPatchPanel({
      video_loras: entries
    })
  }), panel.videos.length ? /*#__PURE__*/React.createElement(Field, {
    label: "Video takes"
  }, /*#__PURE__*/React.createElement("div", {
    className: "ms-candidates-row"
  }, panel.videos.map(v => /*#__PURE__*/React.createElement(VideoTakeTile, {
    key: v.id,
    src: v.file_path,
    title: 'seed ' + (v.seed ?? '—') + ' · take ' + (v.variant_index + 1),
    onDelete: () => onPatchPanel({
      videos: panel.videos.filter(x => x.id !== v.id)
    })
  })))) : null, /*#__PURE__*/React.createElement("div", {
    className: "ms-field"
  }, /*#__PURE__*/React.createElement("label", {
    className: "ms-label"
  }, "Beats \u2014 ", total.toFixed(1), "s", over ? /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--warn)',
      marginLeft: '0.5rem',
      textTransform: 'none',
      fontWeight: 600
    }
  }, "exceeds H3 15s clip cap") : null), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 8
    }
  }, panel.beats.map((b, i) => {
    const keeper = b.images.find(im => im.id === b.selected_image_id) || b.images[0];
    const job = jobFor(b.id);
    return /*#__PURE__*/React.createElement(BeatPill, {
      key: b.id,
      index: i + 1,
      action: b.action,
      duration: b.duration_s,
      motion: b.camera_motion,
      isCut: !!b.is_cut,
      imageCount: b.images.length,
      dialogCount: b.dialog.length,
      thumb: keeper ? keeper.file_path : null,
      job: job ? /*#__PURE__*/React.createElement(JobBadge, {
        state: job.state,
        layout: "fill",
        error: job.error
      }) : null,
      selected: b.id === selectedBeatId,
      onClick: () => onSelectBeat(b.id),
      actions: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(IconButton, {
        glyph: "\u2191",
        title: "Move beat up",
        disabled: i === 0,
        onClick: e => {
          e.stopPropagation();
          onMoveBeat(i, -1);
        }
      }), /*#__PURE__*/React.createElement(IconButton, {
        glyph: "\u2193",
        title: "Move beat down",
        disabled: i === panel.beats.length - 1,
        onClick: e => {
          e.stopPropagation();
          onMoveBeat(i, 1);
        }
      }), /*#__PURE__*/React.createElement(IconButton, {
        glyph: "\u2715",
        destructive: true,
        title: "Delete beat",
        onClick: e => e.stopPropagation()
      }))
    });
  }), panel.beats.length === 0 ? /*#__PURE__*/React.createElement("span", {
    className: "ms-hint"
  }, "No beats yet.") : null), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8,
      marginTop: 4
    }
  }, /*#__PURE__*/React.createElement(Button, {
    size: "sm",
    onClick: onAddBeat
  }, "+ Beat"), /*#__PURE__*/React.createElement(Button, {
    size: "sm",
    onClick: () => setRebeatConfirm(true)
  }, "Re-beat shot")), rebeatConfirm ? /*#__PURE__*/React.createElement(ConfirmBanner, {
    message: "Beats have generated images or locked prompts \u2014 recompose anyway?",
    actions: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Button, {
      variant: "danger",
      size: "sm",
      onClick: () => setRebeatConfirm(false)
    }, "Continue"), /*#__PURE__*/React.createElement(Button, {
      size: "sm",
      onClick: () => setRebeatConfirm(false)
    }, "Cancel"))
  }) : null)));
}
Object.assign(window, {
  ShotDetail
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/storyboard/ShotDetail.jsx", error: String((e && e.message) || e) }); }

// ui_kits/storyboard/SidePanel.jsx
try { (() => {
const {
  Button,
  IconButton,
  Field,
  TextInput,
  Select,
  Textarea,
  Checkbox,
  Chip,
  Tabs,
  SubjectChip,
  CandidateTile,
  ScriptBlock
} = window.MetascanDesignSystem_f2fbbd;
function BeatEditor({
  beat,
  subjects,
  onPatch
}) {
  const SB = window.SB;
  const status = beat.prompt_locked === 1 ? '🔒 edited' : beat.prompt_source === 'brief' ? 'brief fallback' : beat.prompt_source === 'llm' ? 'synthesized' : '—';
  const name = id => (subjects.find(s => s.id === id) || {}).name || '#' + id;
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 14
    }
  }, /*#__PURE__*/React.createElement(Field, {
    label: "Action"
  }, /*#__PURE__*/React.createElement(Textarea, {
    rows: 3,
    value: beat.action,
    onChange: e => onPatch({
      action: e.target.value
    })
  })), /*#__PURE__*/React.createElement(Field, {
    row: true
  }, /*#__PURE__*/React.createElement(Field, {
    label: "Shot size"
  }, /*#__PURE__*/React.createElement(Select, {
    options: SB.SHOT_SIZES,
    value: beat.shot_size ?? '',
    onChange: e => onPatch({
      shot_size: e.target.value || null
    })
  })), /*#__PURE__*/React.createElement(Field, {
    label: "Angle"
  }, /*#__PURE__*/React.createElement(Select, {
    options: SB.ANGLES,
    value: beat.angle ?? '',
    onChange: e => onPatch({
      angle: e.target.value || null
    })
  })), /*#__PURE__*/React.createElement(Field, {
    label: "Lens"
  }, /*#__PURE__*/React.createElement(Select, {
    options: SB.LENSES,
    value: beat.lens ?? '',
    onChange: e => onPatch({
      lens: e.target.value || null
    })
  }))), /*#__PURE__*/React.createElement(Field, {
    label: "Subjects"
  }, beat.subject_ids.length ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexWrap: 'wrap',
      gap: 6,
      marginBottom: 4
    }
  }, beat.subject_ids.map((sid, i) => /*#__PURE__*/React.createElement(SubjectChip, {
    key: sid,
    name: name(sid),
    primary: i === 0,
    onClick: () => onPatch({
      subject_ids: [sid].concat(beat.subject_ids.filter(x => x !== sid))
    })
  }))) : null, /*#__PURE__*/React.createElement("div", {
    className: "ms-checklist"
  }, subjects.map(s => /*#__PURE__*/React.createElement(Checkbox, {
    key: s.id,
    label: s.name,
    checked: beat.subject_ids.includes(s.id),
    onChange: () => onPatch({
      subject_ids: beat.subject_ids.includes(s.id) ? beat.subject_ids.filter(x => x !== s.id) : beat.subject_ids.concat(s.id)
    })
  })))), /*#__PURE__*/React.createElement(Field, {
    label: "Prompt",
    aside: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("span", {
      className: "ms-hint",
      style: {
        flex: 1
      }
    }, status), beat.prompt_locked === 1 ? /*#__PURE__*/React.createElement(Button, {
      variant: "link",
      onClick: () => onPatch({
        prompt_locked: 0
      })
    }, "Unlock") : null)
  }, /*#__PURE__*/React.createElement(Textarea, {
    rows: 4,
    value: beat.prompt ?? '',
    placeholder: "No prompt synthesized yet.",
    onChange: e => onPatch({
      prompt: e.target.value,
      prompt_source: 'user',
      prompt_locked: 1
    })
  }), /*#__PURE__*/React.createElement(Button, {
    size: "xs",
    style: {
      alignSelf: 'flex-start',
      marginTop: 6
    }
  }, "Re-synth prompt")), /*#__PURE__*/React.createElement("div", {
    className: "ms-field"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("label", {
    className: "ms-label"
  }, "Candidates"), /*#__PURE__*/React.createElement(Button, {
    size: "xs"
  }, "Reroll")), /*#__PURE__*/React.createElement("div", {
    className: "ms-candidates-row"
  }, beat.images.map(img => /*#__PURE__*/React.createElement(CandidateTile, {
    key: img.id,
    src: img.file_path,
    selected: img.id === beat.selected_image_id,
    title: 'seed ' + (img.seed ?? '—') + ' · variant ' + img.variant_index,
    onClick: () => onPatch({
      selected_image_id: beat.selected_image_id === img.id ? null : img.id
    }),
    onExpand: () => {}
  })), beat.images.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "ms-hint",
    style: {
      padding: '8px 0',
      fontSize: 12
    }
  }, "No candidates yet.") : null)), /*#__PURE__*/React.createElement(Field, {
    row: true
  }, /*#__PURE__*/React.createElement(Field, {
    label: "Duration (s)",
    style: {
      flex: '0 0 120px'
    }
  }, /*#__PURE__*/React.createElement(TextInput, {
    type: "number",
    step: "0.5",
    min: "0.5",
    value: beat.duration_s,
    onChange: e => onPatch({
      duration_s: Number(e.target.value)
    })
  })), /*#__PURE__*/React.createElement(Button, {
    size: "md",
    active: !!beat.is_cut,
    title: "Toggle hard cut before this beat",
    onClick: () => onPatch({
      is_cut: beat.is_cut ? 0 : 1
    })
  }, "Cut")), /*#__PURE__*/React.createElement(Field, {
    row: true
  }, /*#__PURE__*/React.createElement(Field, {
    label: "Motion"
  }, /*#__PURE__*/React.createElement(Select, {
    options: SB.CAMERA_MOTIONS,
    value: beat.camera_motion ?? '',
    onChange: e => onPatch({
      camera_motion: e.target.value || null
    })
  })), /*#__PURE__*/React.createElement(Field, {
    label: "Amplitude"
  }, /*#__PURE__*/React.createElement(Select, {
    options: SB.CAMERA_AMPLITUDES,
    value: beat.camera_amplitude ?? '',
    onChange: e => onPatch({
      camera_amplitude: e.target.value || null
    })
  })), /*#__PURE__*/React.createElement(Field, {
    label: "Speed"
  }, /*#__PURE__*/React.createElement(Select, {
    options: SB.CAMERA_SPEEDS,
    value: beat.camera_speed ?? '',
    onChange: e => onPatch({
      camera_speed: e.target.value || null
    })
  }))), /*#__PURE__*/React.createElement(Field, {
    label: "Sound"
  }, /*#__PURE__*/React.createElement(Textarea, {
    rows: 2,
    value: beat.sound ?? '',
    onChange: e => onPatch({
      sound: e.target.value
    })
  })), /*#__PURE__*/React.createElement(Field, {
    label: "Dialog"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 8,
      paddingLeft: 8,
      borderLeft: '2px solid var(--surface-border)'
    }
  }, beat.dialog.map((line, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 4
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement(Select, {
    style: {
      flex: '0 0 100px'
    },
    includeEmpty: false,
    value: line.subject_id ?? '',
    options: [{
      value: '',
      label: 'other voice'
    }].concat(subjects.map(s => ({
      value: s.id,
      label: s.name
    }))),
    onChange: e => onPatch({
      dialog: beat.dialog.map((l, j) => j === i ? {
        ...l,
        subject_id: e.target.value === '' ? null : Number(e.target.value)
      } : l)
    })
  }), line.subject_id === null ? /*#__PURE__*/React.createElement(TextInput, {
    style: {
      flex: '0 0 80px'
    },
    placeholder: "voice",
    defaultValue: line.voice ?? ''
  }) : null, /*#__PURE__*/React.createElement(TextInput, {
    style: {
      flex: '0 0 80px'
    },
    placeholder: "delivery",
    defaultValue: line.delivery ?? ''
  }), /*#__PURE__*/React.createElement(TextInput, {
    style: {
      flex: '0 0 72px'
    },
    placeholder: "language",
    defaultValue: line.language
  }), /*#__PURE__*/React.createElement(IconButton, {
    size: "lg",
    glyph: "\u2715",
    title: "Remove line",
    onClick: () => onPatch({
      dialog: beat.dialog.filter((_, j) => j !== i)
    })
  })), /*#__PURE__*/React.createElement(Textarea, {
    rows: 2,
    placeholder: "spoken line",
    defaultValue: line.text
  }))), /*#__PURE__*/React.createElement(Button, {
    variant: "dashed",
    size: "xs",
    style: {
      alignSelf: 'flex-start'
    },
    onClick: () => onPatch({
      dialog: beat.dialog.concat({
        subject_id: null,
        voice: null,
        delivery: null,
        language: 'English',
        text: ''
      })
    })
  }, "+ line"))), /*#__PURE__*/React.createElement(Button, {
    variant: "danger",
    size: "md",
    style: {
      alignSelf: 'flex-start'
    }
  }, "Delete beat"));
}
function PreviewPane({
  panel,
  scene,
  beat,
  subjects,
  selectedBeatId,
  onSelectBeat,
  onPatchPanel
}) {
  const [copied, setCopied] = React.useState(null);
  const flash = k => {
    setCopied(k);
    setTimeout(() => setCopied(null), 1500);
  };
  const name = id => (subjects.find(s => s.id === id) || {}).name || '#' + id;
  const header = 'SHOT ' + ((panel.sort_order ?? 0) + 1) + ' — ' + scene.name.toUpperCase() + '\n' + (scene.location || '') + (scene.time_of_day ? ' · ' + scene.time_of_day : '') + (scene.lighting ? ' · ' + scene.lighting : '');
  const blocks = panel.beats.map((b, i) => ({
    beatId: b.id,
    text: '[Beat ' + (i + 1) + '] ' + b.duration_s.toFixed(1) + 's  ' + (b.shot_size || '—') + ', ' + (b.angle || '—') + ', ' + (b.lens || '—') + (b.camera_motion ? ', ' + b.camera_motion.replace(/_/g, ' ') : '') + (b.is_cut ? '  (cut)' : '') + '\n  ' + b.action + (b.subject_ids.length ? '\n  cast: ' + b.subject_ids.map(name).join(', ') : '') + (b.dialog.length ? '\n  ' + b.dialog.map(l => (l.subject_id ? name(l.subject_id) : l.voice || 'voice').toUpperCase() + (l.delivery ? ' (' + l.delivery + ')' : '') + ': ' + l.text).join('\n  ') : '') + (b.sound ? '\n  sound: ' + b.sound : '')
  }));
  const anchorRelevant = window.SB.tree.video_mode === 'i2va' || window.SB.tree.video_mode === 'fl2va';
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 18
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "ms-field",
    style: {
      gap: 6
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("label", {
    className: "ms-label"
  }, "Video prompt"), /*#__PURE__*/React.createElement("span", {
    className: "ms-hint",
    style: {
      flex: 1
    }
  }, panel.video_prompt_source === 'user' ? 'user edited' : panel.video_prompt_source === 'compiled' ? 'compiled' : '—'), panel.video_anchor !== panel.video_compiled_anchor && anchorRelevant ? /*#__PURE__*/React.createElement(Chip, {
    tone: "warn"
  }, "recompile suggested") : null, panel.video_prompt_locked === 1 ? /*#__PURE__*/React.createElement(Button, {
    variant: "link",
    onClick: () => onPatchPanel({
      video_prompt_locked: 0
    })
  }, "\uD83D\uDD12 Unlock") : null), panel.video_prompt_warnings.length ? /*#__PURE__*/React.createElement("ul", {
    style: {
      margin: 0,
      padding: '0 0 0 16px',
      listStyle: 'disc',
      color: 'var(--warn)',
      fontSize: 11,
      lineHeight: 1.5
    }
  }, panel.video_prompt_warnings.map((w, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, w))) : null, anchorRelevant ? /*#__PURE__*/React.createElement(Field, {
    label: "Anchor"
  }, /*#__PURE__*/React.createElement(Select, {
    placeholder: "None",
    value: panel.video_anchor ?? '',
    onChange: e => onPatchPanel({
      video_anchor: e.target.value || null
    }),
    options: [{
      value: 'keeper',
      label: 'First frame from keeper'
    }, {
      value: 'prev_last',
      label: 'Continue from previous shot'
    }]
  })) : null, /*#__PURE__*/React.createElement(Textarea, {
    rows: 8,
    mono: true,
    placeholder: "No video prompt compiled yet.",
    value: panel.video_prompt ?? '',
    onChange: e => onPatchPanel({
      video_prompt: e.target.value,
      video_prompt_source: 'user'
    })
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8
    }
  }, /*#__PURE__*/React.createElement(Button, {
    size: "xs"
  }, "Compile"), /*#__PURE__*/React.createElement(Button, {
    size: "xs",
    disabled: !panel.video_prompt,
    onClick: () => flash('video')
  }, copied === 'video' ? 'Copied' : 'Copy'), /*#__PURE__*/React.createElement(Button, {
    size: "xs",
    disabled: !panel.video_prompt
  }, "Render video"), /*#__PURE__*/React.createElement(Button, {
    size: "xs",
    title: "Render video for every shot in this scene"
  }, "Render scene"))), /*#__PURE__*/React.createElement(Field, {
    label: "Shot script",
    aside: /*#__PURE__*/React.createElement(Button, {
      size: "xs",
      onClick: () => flash('script')
    }, copied === 'script' ? 'Copied' : 'Copy')
  }, /*#__PURE__*/React.createElement(ScriptBlock, {
    header: header,
    beats: blocks,
    selectedBeatId: selectedBeatId,
    onSelectBeat: onSelectBeat
  })), /*#__PURE__*/React.createElement(Field, {
    label: "Image prompt",
    aside: /*#__PURE__*/React.createElement(Button, {
      size: "xs",
      disabled: !beat || !beat.prompt,
      onClick: () => flash('prompt')
    }, copied === 'prompt' ? 'Copied' : 'Copy')
  }, beat && beat.prompt ? /*#__PURE__*/React.createElement(ScriptBlock, {
    text: beat.prompt
  }) : /*#__PURE__*/React.createElement("p", {
    className: "ms-hint",
    style: {
      fontSize: 12
    }
  }, "No prompt synthesized yet.")));
}
function SidePanel(props) {
  const [tab, setTab] = React.useState('edit');
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      minHeight: 0,
      borderLeft: '1px solid var(--surface-border)',
      background: 'var(--surface-card)',
      flex: '0 0 400px'
    }
  }, /*#__PURE__*/React.createElement(Tabs, {
    tabs: [{
      value: 'edit',
      label: 'Edit'
    }, {
      value: 'preview',
      label: 'Preview'
    }],
    value: tab,
    onChange: setTab
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minHeight: 0,
      overflowY: 'auto',
      padding: 14
    }
  }, tab === 'edit' ? props.beat ? /*#__PURE__*/React.createElement(BeatEditor, {
    beat: props.beat,
    subjects: props.subjects,
    onPatch: props.onPatchBeat
  }) : /*#__PURE__*/React.createElement("p", {
    className: "ms-hint",
    style: {
      fontSize: 12
    }
  }, "Select a beat to edit.") : /*#__PURE__*/React.createElement(PreviewPane, props)));
}
Object.assign(window, {
  SidePanel,
  BeatEditor,
  PreviewPane
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/storyboard/SidePanel.jsx", error: String((e && e.message) || e) }); }

// ui_kits/storyboard/fixtures.js
try { (() => {
window.SB = function () {
  const BASE = window.SB_ASSET_BASE || '../..';
  const F = n => BASE + '/assets/placeholders/frame-' + String(n).padStart(2, '0') + '.png';
  const subjects = [{
    id: 1,
    name: 'Mara',
    description: 'Late twenties, canvas jacket, cropped hair.',
    lora_name: 'mara_v3.safetensors'
  }, {
    id: 2,
    name: 'Ines',
    description: 'Older, silver rings, always mid-sentence.',
    lora_name: null
  }, {
    id: 3,
    name: 'The clerk',
    description: 'Bored, apron, chipped nail polish.',
    lora_name: null
  }];
  const beat = (id, o) => Object.assign({
    id,
    duration_s: 2.5,
    action: '',
    shot_size: 'MCU',
    angle: 'eye',
    lens: 'normal',
    subject_ids: [1],
    camera_motion: null,
    camera_amplitude: 'small',
    camera_speed: 'slow',
    is_cut: 0,
    dialog: [],
    sound: null,
    prompt: null,
    prompt_locked: 0,
    prompt_source: 'llm',
    selected_image_id: null,
    images: []
  }, o);
  const scenes = [{
    id: 1,
    name: 'Kitchen, dawn',
    subtitle: 'interior · dawn',
    setting: 'Cold light through slatted blinds, unwashed cups on the counter.',
    location: 'interior',
    time_of_day: 'dawn',
    mood: 'held breath',
    lighting: 'hard side light',
    panels: [{
      id: 11,
      sort_order: 0,
      action: 'Mara waits for the kettle and does not look at the door.',
      duration_s: 6.0,
      image_loras: [{
        name: 'mara_v3.safetensors',
        strength: 0.85
      }, {
        name: 'filmgrain_xl.safetensors',
        strength: 0.4
      }, {
        name: 'dmd2_sdxl_4step_lora_fp16.safetensors',
        strength: 1.0
      }],
      video_loras: [{
        name: 'h3_motion_smooth.safetensors',
        strength: 0.6
      }, {
        name: 'filmgrain_xl.safetensors',
        strength: 0.25
      }],
      video_prompt: '[Shot 1] MCU, eye level, normal lens. Mara stands at the counter, hands flat on the worktop.\n[Camera] push in, small, slow.\n[Lighting] hard side light through slatted blinds.\n[Sound] kettle rising, no music.\n[Dialogue] none.\n[Negative] no crowd, no text overlay.',
      video_prompt_source: 'compiled',
      video_prompt_locked: 0,
      video_prompt_warnings: [],
      video_anchor: 'keeper',
      video_compiled_anchor: 'keeper',
      videos: [{
        id: 91,
        file_path: F(12),
        seed: 9931,
        variant_index: 0
      }, {
        id: 92,
        file_path: F(13),
        seed: 9932,
        variant_index: 1
      }],
      beats: [beat(101, {
        action: 'She sets the cup down without looking up.',
        duration_s: 2.5,
        camera_motion: 'push_in',
        images: [{
          id: 1001,
          file_path: F(7),
          seed: 481314106,
          variant_index: 0
        }, {
          id: 1002,
          file_path: F(1),
          seed: 481314107,
          variant_index: 1
        }, {
          id: 1003,
          file_path: F(2),
          seed: 481314108,
          variant_index: 2
        }, {
          id: 1004,
          file_path: F(3),
          seed: 481314109,
          variant_index: 3
        }],
        selected_image_id: 1001,
        prompt: 'medium close-up, eye level, normal lens, Mara at a kitchen counter at dawn, hands flat on the worktop, hard side light through slatted blinds, unwashed cups, muted palette, 35mm film grain',
        subject_ids: [1]
      }), beat(102, {
        action: 'The bell over the door does not ring.',
        duration_s: 3.0,
        camera_motion: 'pan_right',
        is_cut: 1,
        subject_ids: [1, 2],
        dialog: [{
          subject_id: 2,
          voice: null,
          delivery: 'flat',
          language: 'English',
          text: 'You left it unlocked again.'
        }],
        sound: 'kettle at full boil, a chair scraping off screen',
        images: [{
          id: 1005,
          file_path: F(8),
          seed: 55120,
          variant_index: 0
        }],
        selected_image_id: 1005,
        prompt: 'medium shot, eye level, normal lens, Mara and Ines in a dawn kitchen, doorway empty behind them, cold slatted light',
        shot_size: 'MS'
      }), beat(103, {
        action: 'Cut to the empty doorway.',
        duration_s: 0.5,
        shot_size: 'CU',
        subject_ids: [],
        images: [],
        prompt: null,
        prompt_source: null
      })]
    }, {
      id: 12,
      sort_order: 1,
      action: 'Ines answers from the hallway.',
      duration_s: 4.0,
      image_loras: [],
      video_loras: [],
      video_prompt: null,
      video_prompt_source: null,
      video_prompt_locked: 0,
      video_prompt_warnings: ['no dialogue block: H3 will improvise'],
      video_anchor: 'prev_last',
      video_compiled_anchor: null,
      videos: [],
      beats: [beat(104, {
        action: 'Ines leans into frame, still talking.',
        subject_ids: [2],
        shot_size: 'MCU',
        camera_motion: 'static',
        images: [{
          id: 1006,
          file_path: F(9),
          seed: 771,
          variant_index: 0
        }],
        selected_image_id: 1006,
        prompt: 'medium close-up of Ines mid-sentence in a narrow hallway, warm bulb overhead'
      })]
    }, {
      id: 13,
      sort_order: 2,
      action: 'The kettle boils over.',
      duration_s: 2.0,
      image_loras: [],
      video_loras: [],
      video_prompt: null,
      video_prompt_source: null,
      video_prompt_locked: 0,
      video_prompt_warnings: [],
      video_anchor: null,
      video_compiled_anchor: null,
      videos: [],
      beats: [beat(105, {
        action: 'Steam floods the window.',
        subject_ids: [],
        shot_size: 'ECU',
        images: []
      })]
    }, {
      id: 14,
      sort_order: 3,
      action: 'Mara takes the cup to the table.',
      duration_s: 3.0,
      image_loras: [],
      video_loras: [],
      video_prompt: null,
      video_prompt_source: null,
      video_prompt_locked: 1,
      video_prompt_warnings: [],
      video_anchor: null,
      video_compiled_anchor: null,
      videos: [],
      beats: [beat(106, {
        action: 'She sits without pulling the chair in.',
        images: [{
          id: 1007,
          file_path: F(10),
          seed: 3311,
          variant_index: 0
        }],
        selected_image_id: null,
        prompt_locked: 1,
        prompt_source: 'user',
        prompt: 'medium close-up, Mara seated at a kitchen table, chair left pulled out, cold dawn light'
      })]
    }]
  }, {
    id: 2,
    name: 'Courtyard',
    subtitle: 'exterior · midday',
    setting: 'Flat white light, one plastic chair, the gate standing open.',
    location: 'exterior',
    time_of_day: 'midday',
    mood: 'exposed',
    lighting: 'flat overcast',
    panels: [{
      id: 21,
      sort_order: 0,
      action: 'She crosses the courtyard.',
      duration_s: 5.0,
      image_loras: [],
      video_loras: [],
      video_prompt: null,
      video_prompt_source: null,
      video_prompt_locked: 0,
      video_prompt_warnings: [],
      video_anchor: null,
      video_compiled_anchor: null,
      videos: [],
      beats: [beat(201, {
        action: 'Mara crosses left to right, fast.',
        shot_size: 'WS',
        camera_motion: 'truck_right',
        camera_speed: 'fast',
        images: [{
          id: 2001,
          file_path: F(4),
          seed: 61,
          variant_index: 0
        }],
        selected_image_id: 2001,
        prompt: 'wide shot, flat overcast courtyard, Mara crossing frame left to right'
      })]
    }, {
      id: 22,
      sort_order: 1,
      action: 'The clerk watches from the gate.',
      duration_s: 3.0,
      image_loras: [],
      video_loras: [],
      video_prompt: null,
      video_prompt_source: null,
      video_prompt_locked: 0,
      video_prompt_warnings: [],
      video_anchor: null,
      video_compiled_anchor: null,
      videos: [],
      beats: [beat(202, {
        action: 'He does not move out of the way.',
        subject_ids: [3],
        shot_size: 'MLS',
        images: [],
        prompt: null,
        prompt_source: null
      })]
    }]
  }, {
    id: 3,
    name: 'Stairwell',
    subtitle: 'interior · night',
    setting: null,
    location: 'interior',
    time_of_day: 'night',
    mood: null,
    lighting: null,
    panels: [{
      id: 31,
      sort_order: 0,
      action: 'Two floors of nothing happening.',
      duration_s: 4.0,
      image_loras: [],
      video_loras: [],
      video_prompt: null,
      video_prompt_source: null,
      video_prompt_locked: 0,
      video_prompt_warnings: [],
      video_anchor: null,
      video_compiled_anchor: null,
      videos: [],
      beats: [beat(301, {
        action: 'Mara climbs into the dark.',
        shot_size: 'MLS',
        camera_motion: 'tracking',
        images: [],
        prompt: null,
        prompt_source: null
      })]
    }]
  }];
  const list = [{
    id: 1,
    name: 'Coffee shop meet-cute',
    aspect_ratio: '16:9',
    target_model: 'flux1',
    updated_at: '8/23/2025, 2:15:02 PM'
  }, {
    id: 2,
    name: 'Cold open — stairwell',
    aspect_ratio: '2.39:1',
    target_model: 'qwen',
    updated_at: '8/21/2025, 9:04:47 AM'
  }, {
    id: 3,
    name: 'Product loop (vertical)',
    aspect_ratio: '9:16',
    target_model: 'zimage',
    updated_at: '8/14/2025, 6:38:11 PM'
  }];
  const tree = {
    id: 1,
    name: 'Coffee shop meet-cute',
    aspect_ratio: '16:9',
    target_model: 'flux1',
    batch_size: 4,
    base_seed: 481314106,
    video_target: 'minimax',
    video_mode: 'ref2va',
    video_preset_id: 4,
    preset_id: 2,
    subjects,
    scenes,
    style_block: 'muted palette, 35mm film grain, no text',
    negative: 'text overlay, watermark, extra fingers',
    source_text: 'Two women who have known each other too long share a kitchen at dawn. Neither says the thing.',
    outline: '{\n  "logline": "Two women share a kitchen at dawn and neither says the thing.",\n  "scenes": [\n    { "name": "Kitchen, dawn", "beats": 3 },\n    { "name": "Courtyard", "beats": 2 },\n    { "name": "Stairwell", "beats": 1 }\n  ]\n}'
  };
  const loraOptions = ['mara_v3.safetensors', 'filmgrain_xl.safetensors', 'dmd2_sdxl_4step_lora_fp16.safetensors', 'sts_age_slider_v1.safetensors'];
  return {
    F,
    tree,
    list,
    subjects,
    loraOptions,
    SHOT_SIZES: ['ECU', 'CU', 'MCU', 'MS', 'MLS', 'WS', 'EWS'],
    ANGLES: ['eye', 'low', 'high', 'overhead', 'dutch', 'ots', 'pov'],
    LENSES: ['wide', 'normal', 'tele', 'macro'],
    CAMERA_MOTIONS: ['zoom_in', 'zoom_out', 'push_in', 'pull_out', 'pan_left', 'pan_right', 'truck_left', 'truck_right', 'tilt_up', 'tilt_down', 'pedestal_up', 'pedestal_down', 'arc', 'tracking', 'static', 'shake_slight', 'shake_strong', 'pov', 'roll_cw', 'roll_ccw'],
    CAMERA_AMPLITUDES: ['small', 'large'],
    CAMERA_SPEEDS: ['slow', 'fast'],
    ASPECT_RATIOS: ['1:1', '4:3', '16:9', '2.39:1', '9:16'],
    TARGET_MODELS: ['sd', 'pony', 'flux1', 'flux2', 'zimage', 'chroma', 'qwen'],
    VIDEO_MODES: ['t2va', 'i2va', 'fl2va', 'ref2va'],
    COMPOSE_STAGES: ['outline', 'scenes', 'shots', 'beats']
  };
}();
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/storyboard/fixtures.js", error: String((e && e.message) || e) }); }

__ds_ns.Button = __ds_scope.Button;

__ds_ns.Checkbox = __ds_scope.Checkbox;

__ds_ns.Chip = __ds_scope.Chip;

__ds_ns.Field = __ds_scope.Field;

__ds_ns.IconButton = __ds_scope.IconButton;

__ds_ns.Select = __ds_scope.Select;

__ds_ns.TextInput = __ds_scope.TextInput;

__ds_ns.Textarea = __ds_scope.Textarea;

__ds_ns.ConfirmBanner = __ds_scope.ConfirmBanner;

__ds_ns.JobBadge = __ds_scope.JobBadge;

__ds_ns.Tabs = __ds_scope.Tabs;

__ds_ns.Dialog = __ds_scope.Dialog;

__ds_ns.Toast = __ds_scope.Toast;

__ds_ns.AddTile = __ds_scope.AddTile;

__ds_ns.BeatPill = __ds_scope.BeatPill;

__ds_ns.BoardRow = __ds_scope.BoardRow;

__ds_ns.CandidateTile = __ds_scope.CandidateTile;

__ds_ns.LoraListEditor = __ds_scope.LoraListEditor;

__ds_ns.PanelTile = __ds_scope.PanelTile;

__ds_ns.SceneCard = __ds_scope.SceneCard;

__ds_ns.ScriptBlock = __ds_scope.ScriptBlock;

__ds_ns.SubjectChip = __ds_scope.SubjectChip;

__ds_ns.VideoTakeTile = __ds_scope.VideoTakeTile;

})();

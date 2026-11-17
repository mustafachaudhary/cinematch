import { useEffect, useId, useRef } from 'react'
import { createPortal } from 'react-dom'
import styles from './Modal.module.css'

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

export default function Modal({
  title,
  onClose,
  size = 'default',
  children,
  className = '',
}) {
  const titleId = useId()
  const dialogRef = useRef(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  useEffect(() => {
    const previous = document.activeElement
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    const node = dialogRef.current
    const focusables = node ? [...node.querySelectorAll(FOCUSABLE)] : []
    const target = node?.querySelector('[data-autofocus]') || focusables[0] || node
    target?.focus()

    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        closeRef.current?.()
        return
      }
      if (event.key !== 'Tab' || !node) return
      const items = [...node.querySelectorAll(FOCUSABLE)].filter(
        (item) => item.offsetParent !== null,
      )
      if (!items.length) return
      const first = items[0]
      const last = items[items.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = overflow
      if (previous instanceof HTMLElement) previous.focus()
    }
  }, [])

  return createPortal(
    <div
      className={styles.overlay}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) closeRef.current?.()
      }}
    >
      <div
        ref={dialogRef}
        className={`${styles.dialog} ${className}`}
        data-size={size}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <h2 id={titleId} className={styles.title}>
          {title}
        </h2>
        <button
          type="button"
          className={styles.close}
          onClick={() => closeRef.current?.()}
          aria-label="Close dialog"
        >
          <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
            <path d="M2 2l12 12M14 2L2 14" stroke="currentColor" strokeWidth="1.4" />
          </svg>
        </button>
        <div className={styles.body}>{children}</div>
      </div>
    </div>,
    document.body,
  )
}

import { createContext, useCallback, useContext, useRef, useState } from 'react'
import styles from './Toast.module.css'

const ToastContext = createContext(() => {})

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const nextId = useRef(0)

  const push = useCallback((message, kind = 'success') => {
    const id = ++nextId.current
    setToasts((list) => [...list, { id, message, kind }])
    setTimeout(() => {
      setToasts((list) => list.filter((toast) => toast.id !== id))
    }, 3800)
  }, [])

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className={styles.stack} aria-live="polite" aria-atomic="false">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`${styles.toast} ${toast.kind === 'error' ? styles.error : ''}`}
            role={toast.kind === 'error' ? 'alert' : 'status'}
          >
            {toast.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  return useContext(ToastContext)
}

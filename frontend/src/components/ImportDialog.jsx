import { useState } from 'react'
import { api } from '../hooks/api'
import Button from './Button'
import Modal from './Modal'
import { useToast } from './Toast'
import styles from './ImportDialog.module.css'

export default function ImportDialog({ onClose, onImported }) {
  const toast = useToast()
  const [file, setFile] = useState(null)
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState('')

  const submit = async (event) => {
    event.preventDefault()
    if (!file) return
    setError('')
    setImporting(true)
    const formData = new FormData()
    formData.append('file', file)
    try {
      const result = await api.importLetterboxd(formData)
      toast(
        `Imported ${result.imported} ratings (${result.skipped} skipped, ${result.watched_imported || 0} watched).`,
      )
      onImported?.()
      onClose()
    } catch (err) {
      setError(err.message)
      toast(err.message, 'error')
    } finally {
      setImporting(false)
    }
  }

  return (
    <Modal title="Import from Letterboxd" onClose={onClose}>
      <form className={styles.form} onSubmit={submit}>
        <p className={styles.intro}>
          Upload the zip from your Letterboxd export. Ratings and watched history are
          resolved against TMDB and added to your archive.
        </p>

        <label className={styles.fileField} htmlFor="letterboxd-file">
          <span className={styles.fileLabel}>Letterboxd export</span>
          <span className={styles.fileBox}>
            {file ? file.name : 'Choose a .zip file'}
          </span>
          <input
            id="letterboxd-file"
            className={styles.fileInput}
            type="file"
            accept=".zip,application/zip"
            onChange={(event) => setFile(event.target.files?.[0] || null)}
          />
        </label>

        <p className={styles.note}>
          Existing ratings are updated in place. A large archive can take a little while
          to resolve.
        </p>

        {error ? (
          <p className={styles.error} role="alert">
            {error}
          </p>
        ) : null}

        <div className={styles.actions}>
          <Button variant="quiet" onClick={onClose} disabled={importing}>
            Cancel
          </Button>
          <Button type="submit" disabled={!file || importing}>
            {importing ? 'Importing…' : 'Start import'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}

/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** The backend's address, if it isn't http://localhost:8000. */
  readonly VITE_API_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

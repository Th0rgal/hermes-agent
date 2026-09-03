#!/usr/bin/env node
// The desktop package version must match the Hermes release version in
// pyproject.toml. release.py keeps them in lockstep on a cut; this guards a
// branch that was advanced by hand (production drifted 0.17.0 vs 0.20.4).
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export function versionsInLockstep(packageJson, pyprojectToml) {
  const desktop = JSON.parse(packageJson).version
  const match = /^version\s*=\s*"([^"]+)"/m.exec(pyprojectToml)
  const hermes = match ? match[1] : null

  return { desktop, hermes, ok: Boolean(desktop && hermes && desktop === hermes) }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const here = dirname(fileURLToPath(import.meta.url))
  const result = versionsInLockstep(
    readFileSync(resolve(here, '../package.json'), 'utf8'),
    readFileSync(resolve(here, '../../../pyproject.toml'), 'utf8')
  )

  if (!result.ok) {
    console.error(
      `apps/desktop/package.json version ${result.desktop} != pyproject.toml version ${result.hermes}; bump apps/desktop/package.json (release.py does this on a cut)`
    )
    process.exit(1)
  }

  console.log(`desktop ${result.desktop} matches hermes ${result.hermes}`)
}

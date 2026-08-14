/**
 * The two-way seam between the core chat sidebar and the projects-board
 * plugin's "Projects" section. A conversation bound to a project lives under
 * Projects — showing it a second time in Recents made clicking a project
 * highlight a row somewhere else entirely.
 *
 * - The plugin publishes its bindings here (stored session id → slug); the
 *   sidebar's Recents list excludes those sessions. Search and explicit pins
 *   still surface them — dedup, not exile.
 * - The sidebar publishes the active chat session's ids (live + lineage
 *   root); the plugin's project rows render their selected state from it.
 */

import { atom } from 'nanostores'

/** Stored session id → project slug, published by the projects-board plugin. */
export const $projectBoundSessionIds = atom<Record<string, string>>({})

/** Every id the currently open chat session answers to (live id + lineage
 *  root); empty when another view is active. */
export const $activeChatSessionIds = atom<readonly string[]>([])

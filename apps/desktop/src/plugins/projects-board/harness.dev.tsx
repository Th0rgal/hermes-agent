/**
 * Dev-only screenshot harness for the projects board — served by the Vite dev
 * server at /harness.html, no Electron bridge needed. Mounts the real
 * ProjectsBoardPage over deterministic fixtures (every drawer/grant/roadmap
 * state represented) so visual review and automated screenshots don't depend
 * on a live gateway. `?theme=dark` flips the theme.
 */

/* eslint-disable no-restricted-imports -- dev harness, not plugin runtime: it
   plays the HOST (styles, theme provider, locale registry), which real plugin
   code must reach only through the SDK. */

import '../../styles.css'
import './projects-board.css'

import { TooltipProvider } from '@hermes/plugin-sdk'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createRoot } from 'react-dom/client'

import { registerPluginLocales } from '@/i18n/plugin-i18n'
import { ThemeProvider } from '@/themes'

import { bindApi, type ProjectDetail, type ProjectRow, type ProjectTasksResponse } from './api'
import { ProjectsBoardPage } from './board'
import { BOARD_LOCALES } from './i18n'

const row = (slug: string, bucket: string, extra: Partial<ProjectRow> = {}): ProjectRow => ({
  attention_reasons: [],
  bucket,
  latest_update: null,
  missions: [],
  slug,
  title: slug[0].toUpperCase() + slug.slice(1),
  ...extra
})

const projects: ProjectRow[] = [
  row('verity', 'attention', {
    attention_reasons: ['controller blocked on CI'],
    autonomy_level: 'propose',
    conversation: { session_id: '20260806-verity-controller', source: 'binding' },
    latest_update: {
      at: new Date(Date.now() - 3600e3).toISOString(),
      headline: 'CI red on #2213',
      mode: 'blocked: ci'
    },
    missions: [{ github_pr: '#2213', id: 'm1', status: 'awaiting_user', title: 'Fix receipts CI', updated_at: null }],
    pending_decisions: 1
  }),
  row('coldcard', 'active', {
    autonomy_level: 'act_full',
    conversation: { session_id: '20260806-coldcard-controller', source: 'binding' },
    mode: 'active'
  }),
  row('hermes', 'active', {
    autonomy_level: 'act_reversible',
    latest_update: { at: new Date(Date.now() - 7200e3).toISOString(), headline: 'Shipping PR #42', mode: 'active' },
    missions: [{ github_pr: null, id: 'm2', status: 'active', title: 'Board polish', updated_at: null }]
  }),
  row('lido', 'paused', { mode: 'paused: quota' })
]

const details: Record<string, ProjectDetail> = {
  coldcard: {
    conversation: { session_id: '20260806-coldcard-controller', source: 'binding' },
    grant: { autonomy_level: 'act_full' },
    project: { mode: 'active', slug: 'coldcard', status: 'active', title: 'Coldcard' }
  },
  hermes: {
    grant: {
      autonomy_level: 'act_reversible',
      // A legacy free-text merge rule — must survive the typed editor.
      merge_authority: 'merge only docs changes',
      parallel_missions: 3
    },
    project: { objective: 'Desktop polish', slug: 'hermes', status: 'active', title: 'Hermes' }
  },
  lido: { project: { mode: 'paused', slug: 'lido', status: 'paused', title: 'Lido' } },
  verity: {
    conversation: { session_id: '20260806-verity-controller', source: 'binding' },
    grant: {
      autonomy_level: 'propose',
      budget_per_tick: '2 missions',
      merge_authority: 'review-first',
      parallel_missions: 2
    },
    open_decisions: [
      {
        at: new Date(Date.now() - 1800e3).toISOString(),
        kind: 'merge',
        question: 'Merge #2213 despite the flaky lane?',
        rationale: 'Receipts pass locally; the lane failure is the known runner OOM.',
        status: 'pending_user'
      }
    ],
    project: {
      blocker: 'CI red on receipts lane',
      mode: 'blocked',
      next_action: 'wait for owner verdict on #2213',
      objective: 'Verity 4.31 convergence',
      repository: 'lfglabs-dev/verity',
      slug: 'verity',
      status: 'active',
      title: 'Verity'
    },
    recent_decisions: [
      {
        at: new Date(Date.now() - 86400e3).toISOString(),
        evidence: { pr_url: 'https://github.com/lfglabs-dev/verity/pull/2210' },
        kind: 'dispatch',
        question: 'Dispatched exact-head receipts campaign',
        rationale: 'Within budget, reversible.',
        status: 'decided'
      }
    ],
    tracks: [{ desired_state: 'green', status: 'attention', track: 'ci', updated_at: null }]
  }
}

const tasks: Record<string, ProjectTasksResponse> = {
  coldcard: { tasks: [] },
  hermes: {
    summary: {
      declared_total: 3,
      done: 1,
      executing: 1,
      failed: 0,
      inconsistencies: 0,
      running: 1,
      satisfied: 1,
      total: 3,
      unplanned_attempts: 0
    },
    tasks: [
      { status: 'accepted', task_key: 'drawer-header', title: 'Consolidate drawer header' },
      { status: 'running', task_key: 'typed-grant', title: 'Typed grant panel', worker_mission_id: 'abcd1234' },
      // The chat-planned state: visible on the roadmap before dispatch.
      { status: 'proposed', task_key: 'skills-pack', title: 'Ship default skills in the fork' }
    ]
  },
  lido: { tasks: [] },
  verity: {
    summary: {
      declared_total: 5,
      done: 2,
      executing: 1,
      failed: 1,
      inconsistencies: 0,
      running: 1,
      satisfied: 2,
      total: 5,
      unplanned_attempts: 0
    },
    tasks: [
      { status: 'accepted', task_key: 'receipts', title: 'Exact-head receipts on #2213' },
      {
        acceptance_criteria: ['lane green twice'],
        attempts: 2,
        pr_url: 'https://github.com/lfglabs-dev/verity/pull/2213',
        result_digest: 'Retried after runner OOM; second attempt green.',
        status: 'settled',
        task_key: 'ci-lane',
        title: 'Stabilize receipts CI lane'
      },
      {
        status: 'running',
        task_key: 'bundle-digest',
        title: 'Bundle digest verification',
        worker_mission_id: 'wxyz9876'
      },
      { status: 'failed', task_key: 'toolchain', title: 'Toolchain pinning sweep' },
      { status: 'pending', task_key: 'campaign', title: 'Validation campaign rollup' }
    ]
  }
}

bindApi(<T,>(path: string): Promise<T> => {
  const detail = /^\/projects\/([^/]+)$/.exec(path)
  const taskList = /^\/projects\/([^/]+)\/tasks$/.exec(path)

  if (path === '/projects') {
    return Promise.resolve({ projects } as T)
  }

  if (taskList) {
    return Promise.resolve((tasks[taskList[1]] ?? { tasks: [] }) as T)
  }

  if (detail) {
    return Promise.resolve(details[detail[1]] as T)
  }

  if (path.includes('/state')) {
    return Promise.resolve({ states: [] } as T)
  }

  return Promise.reject(Object.assign(new Error('404'), { status: 404 }))
})

registerPluginLocales('projects-board', BOARD_LOCALES)

// The real ThemeProvider derives every token from the mode preference —
// toggling the `.dark` class alone paints nothing. Seed the persisted pref
// before mount so `?theme=dark` renders the true dark skin.
localStorage.setItem(
  'hermes-desktop-mode-v1',
  new URLSearchParams(location.search).get('theme') === 'dark' ? 'dark' : 'light'
)

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={client}>
    <ThemeProvider>
      <TooltipProvider>
        <div className="h-screen overflow-auto bg-background">
          <ProjectsBoardPage />
        </div>
      </TooltipProvider>
    </ThemeProvider>
  </QueryClientProvider>
)

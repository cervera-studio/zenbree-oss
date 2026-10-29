/** Python API paths (trimmed from the earlier implementation `packages/config/api-routes.ts` to what Zenbree calls). */
export const API_ROUTES = {
  me: '/v1/me',
  chat: {
    send: '/v1/chat',
    sessions: {
      get: (id: string) => `/v1/chat/sessions/${id}`,
      stream: (id: string) => `/v1/chat/sessions/${id}/stream`,
    },
  },
  campaigns: { list: '/v1/campaigns' },
  instantly: {
    campaigns: '/v1/integrations/instantly/campaigns',
    preview: '/v1/integrations/instantly/preview',
    enrollments: '/v1/integrations/instantly/enrollments',
    latest: '/v1/integrations/instantly/enrollments/latest',
    enrollment: (id: string) => `/v1/integrations/instantly/enrollments/${id}`,
    cancel: (id: string) => `/v1/integrations/instantly/enrollments/${id}/cancel`,
    retry: (id: string) => `/v1/integrations/instantly/enrollments/${id}/retry`,
  },
  projects: { list: '/v1/projects', one: (id: string) => `/v1/projects/${id}` },
  jobs: {
    get: (id: string) => `/v1/jobs/${id}`,
    cancel: (id: string) => `/v1/jobs/${id}/cancel`,
  },
  lists: {
    list: '/v1/lists',
    get: (id: string) => `/v1/lists/${id}`,
    restore: (id: string) => `/v1/lists/${id}/restore`,
    rows: (id: string) => `/v1/lists/${id}/rows`,
    row: (id: string, rowId: string) => `/v1/lists/${id}/rows/${rowId}`,
    rowRating: (id: string, rowId: string) => `/v1/lists/${id}/rows/${rowId}/rating`,
    rowPhone: (id: string, rowId: string) => `/v1/lists/${id}/rows/${rowId}/phone`,
    markOffTarget: (id: string) => `/v1/lists/${id}/rows/mark-off-target`,
    rateRows: (id: string) => `/v1/lists/${id}/rows/rate`,
    addToCampaign: (id: string) => `/v1/lists/${id}/rows/add-to-campaign`,
    export: (id: string, format: 'csv' | 'xlsx') => `/v1/lists/${id}/export.${format}`,
    columns: (id: string) => `/v1/lists/${id}/columns`,
    estimateColumn: (id: string) => `/v1/lists/${id}/columns/estimate`,
    column: (id: string, key: string) => `/v1/lists/${id}/columns/${encodeURIComponent(key)}`,
    fillColumn: (id: string, key: string) =>
      `/v1/lists/${id}/columns/${encodeURIComponent(key)}/fill`,
    restoreColumn: (id: string, key: string) =>
      `/v1/lists/${id}/columns/${encodeURIComponent(key)}/restore`,
    upload: '/v1/lists/upload',
    uploadPreview: '/v1/lists/upload/preview',
    activeImport: '/v1/lists/imports/active',
    csvImport: (id: string) => `/v1/lists/imports/${id}`,
    csvImportSeen: (id: string) => `/v1/lists/imports/${id}/seen`,
    csvImportDiscarded: (id: string) => `/v1/lists/imports/${id}/discarded.csv`,
    csvImportStream: (id: string) => `/v1/lists/imports/${id}/stream`,
  },
} as const;

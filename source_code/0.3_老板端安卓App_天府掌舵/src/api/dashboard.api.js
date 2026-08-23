import { dedupedFetch } from './client'

export const getCockpitSummary = (baseUrl, accessToken) =>
  dedupedFetch(baseUrl, '/api/v1/executive/cockpit/summary', { accessToken })

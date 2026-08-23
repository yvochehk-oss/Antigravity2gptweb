import { apiRequest, dedupedFetch } from './client'

export async function getProjects(baseUrl, accessToken) {
  const response = await dedupedFetch(baseUrl, '/api/v1/executive/projects', { accessToken })
  return response?.projects || []
}

export const getProject360 = (baseUrl, projectId, accessToken) =>
  // 360 drill-down is dynamic and shouldn't be coalesced.
  apiRequest(baseUrl, `/api/v1/executive/projects/${encodeURIComponent(projectId)}/360`, { accessToken })

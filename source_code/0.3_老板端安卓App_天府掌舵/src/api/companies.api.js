import { dedupedFetch } from './client'

export const getCompanyMatrix = (baseUrl, accessToken) =>
  dedupedFetch(baseUrl, '/api/v1/executive/entities/matrix', { accessToken })

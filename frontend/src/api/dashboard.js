import request from './request'
export const randomPatient = () => request.get('/dashboard/random-patient')
export const visitFrequency = () => request.get('/dashboard/stats/visit-frequency')
export const drugDistribution = () => request.get('/dashboard/stats/drug-distribution')

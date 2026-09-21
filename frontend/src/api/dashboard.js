import request from './request'
export const randomPatient = () => request.get('/dashboard/random-patient')
export const visitFrequency = () => request.get('/dashboard/stats/visit-frequency')
export const drugDistribution = () => request.get('/dashboard/stats/drug-distribution')
export const admissionRatio = () => request.get('/dashboard/stats/admission-ratio')
export const rationalDrugRate = () => request.get('/dashboard/stats/rational-drug-rate')
export const pathwayRate = () => request.get('/dashboard/stats/pathway-rate')

import request from './request'
export const createCharge = (d) => request.post('/charges', d)
export const listCharges = (params) => request.get('/charges', { params })
export const createDispense = (d) => request.post('/dispenses', d)
export const listDispenses = (params) => request.get('/dispenses', { params })

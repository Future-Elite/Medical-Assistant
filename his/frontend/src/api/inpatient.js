import request from './request'
export const listAdmissions = (params) => request.get('/admissions', { params })
export const getAdmissionTimeline = (id) => request.get(`/admissions/${id}/timeline`)
export const createAdmission = (d) => request.post('/admissions', d)
export const dischargeAdmission = (id, totalCost) =>
  request.put(`/admissions/${id}/discharge`, null, { params: { totalCost } })
export const listOrders = (params) => request.get('/orders', { params })
export const createOrder = (d) => request.post('/orders', d)
export const stopOrder = (id) => request.put(`/orders/${id}/stop`)
export const listVitalSigns = (params) => request.get('/vital-signs', { params })
export const createVitalSign = (d) => request.post('/vital-signs', d)

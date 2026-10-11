import request from './request'
export const listPatients = (params) => request.get('/patients', { params })
export const getPatient = (id) => request.get(`/patients/${id}`)
export const getTimeline = (id) => request.get(`/patients/${id}/timeline`)
export const createPatient = (data) => request.post('/patients', data)

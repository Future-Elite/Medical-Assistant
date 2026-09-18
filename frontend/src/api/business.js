import request from './request'
export const createVisit = (d) => request.post('/visits', d)
export const createDiagnosis = (d) => request.post('/diagnoses', d)
export const createPrescription = (d) => request.post('/prescriptions', d)
export const createLabTest = (d) => request.post('/lab-tests', d)

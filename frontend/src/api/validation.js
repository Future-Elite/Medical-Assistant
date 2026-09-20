import request from './request'
export const runValidation = () => request.post('/validation/run')

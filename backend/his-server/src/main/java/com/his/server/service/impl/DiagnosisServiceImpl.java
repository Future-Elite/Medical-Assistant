package com.his.server.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.his.common.entity.Diagnosis;
import com.his.common.mapper.DiagnosisMapper;
import com.his.server.service.DiagnosisService;
import org.springframework.stereotype.Service;

@Service
public class DiagnosisServiceImpl extends ServiceImpl<DiagnosisMapper, Diagnosis> implements DiagnosisService {
}

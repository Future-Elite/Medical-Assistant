package com.his.server.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.his.common.entity.LabTest;
import com.his.common.mapper.LabTestMapper;
import com.his.server.service.LabTestService;
import org.springframework.stereotype.Service;

@Service
public class LabTestServiceImpl extends ServiceImpl<LabTestMapper, LabTest> implements LabTestService {
}

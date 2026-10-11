package com.his.server.service.impl;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.his.common.entity.Patient;
import com.his.common.mapper.PatientMapper;
import com.his.server.service.PatientService;
import org.springframework.stereotype.Service;

@Service
public class PatientServiceImpl extends ServiceImpl<PatientMapper, Patient> implements PatientService {

    @Override
    public Page<Patient> pageByDepartment(long page, long size, String department, String keyword) {
        // mapper 返回 IPage，但运行时对象就是传入的 Page 实例（分页插件原地改写），强转安全
        return (Page<Patient>) baseMapper.selectPageByDepartment(new Page<>(page, size), department, keyword);
    }
}

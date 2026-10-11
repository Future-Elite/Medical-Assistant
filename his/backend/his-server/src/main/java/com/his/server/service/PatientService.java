package com.his.server.service;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.IService;
import com.his.common.entity.Patient;

public interface PatientService extends IService<Patient> {

    Page<Patient> pageByDepartment(long page, long size, String department, String keyword);
}

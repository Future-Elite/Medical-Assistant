package com.his.common.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.his.common.entity.Patient;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface PatientMapper extends BaseMapper<Patient> {

    /** 随机取一名患者的主键，避免 randomPatient() 把全表 10000 行拉进内存。 */
    @Select("SELECT id FROM patient ORDER BY RAND() LIMIT 1")
    Long selectRandomId();
}

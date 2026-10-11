package com.his.common.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.his.common.entity.Admission;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface AdmissionMapper extends BaseMapper<Admission> {

    /** 有多少名患者至少住院过一次（用于住院占比统计）。 */
    @Select("SELECT COUNT(DISTINCT patient_id) FROM admission")
    Long selectAdmissionPatientCount();
}

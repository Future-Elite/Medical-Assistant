package com.his.common.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.baomidou.mybatisplus.core.metadata.IPage;
import com.his.common.entity.Patient;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface PatientMapper extends BaseMapper<Patient> {

    /** 随机取一名患者的主键，避免 randomPatient() 把全表 10000 行拉进内存。 */
    @Select("SELECT id FROM patient ORDER BY RAND() LIMIT 1")
    Long selectRandomId();

    /**
     * 按科室分页查患者：取「在该科室有过至少一次就诊」的患者。
     * 用参数化子查询避免把 department 拼进 SQL 字符串（防注入）。
     */
    @Select("<script>"
            + "SELECT * FROM patient WHERE id IN "
            + "(SELECT DISTINCT patient_id FROM visit WHERE department = #{department})"
            + "<if test=\"keyword != null and keyword != ''\">"
            + " AND (name LIKE CONCAT('%', #{keyword}, '%')"
            + " OR id_card LIKE CONCAT('%', #{keyword}, '%')"
            + " OR phone LIKE CONCAT('%', #{keyword}, '%'))"
            + "</if>"
            + " ORDER BY id DESC"
            + "</script>")
    IPage<Patient> selectPageByDepartment(IPage<Patient> page,
                                          @Param("department") String department,
                                          @Param("keyword") String keyword);
}

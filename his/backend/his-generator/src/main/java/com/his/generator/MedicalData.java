package com.his.generator;

import java.util.List;

/**
 * 生成器里仅剩的「非本体」常量。
 *
 * <p>诊断、用药、剂量、门诊检验项已全部改从医学本体表读取
 * （{@code dept_diagnosis} / {@code diagnosis_drug} / {@code drug_dose} / {@code diagnosis_lab}），
 * 见 {@link DataGeneratorService}。这里只留下：
 * <ul>
 *   <li>姓名用字（模拟患者与药师姓名）；</li>
 *   <li>{@link #LAB_ITEMS}——<b>住院</b>常规化验组合，住院检验没有 visit_id、不参与门诊的本体断言，
 *       临床路径里的 LAB 步骤也按项目名在这份组合里查表。</li>
 * </ul>
 */
public final class MedicalData {
    public record LabItem(String code, String name, String unit, String ref, double low, double high) {}

    public static final String[] SURNAMES = {"王","李","张","刘","陈","杨","赵","黄","周","吴"};
    public static final String[] GIVEN = {"伟","芳","娜","敏","静","磊","军","洋","勇","艳","杰","涛","明","超","秀英"};

    public static final List<LabItem> LAB_ITEMS = List.of(
        new LabItem("BLD-RBC","红细胞计数","10^12/L","3.5-5.5",3.5,5.5),
        new LabItem("BLD-WBC","白细胞计数","10^9/L","4.0-10.0",4.0,10.0),
        new LabItem("BLD-GLU","空腹血糖","mmol/L","3.9-6.1",3.9,6.1),
        new LabItem("BLD-HB","血红蛋白","g/L","110-160",110,160),
        new LabItem("BLD-PLT","血小板计数","10^9/L","100-300",100,300)
    );

    private MedicalData() {}
}

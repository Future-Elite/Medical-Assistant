"""Sourced evaluation intervals; never a treatment-response model."""
from .contracts import ReferenceRule

SOURCE = 'https://www.safercare.vic.gov.au/best-practice-improvement/clinical-guidance/neonatal/normal-laboratory-values-for-neonates'
WBC_SOURCE = 'https://www.healthcare.uiowa.edu/path_handbook/handbook/test1977.html'
NOTES = ['公开范围仅作为有来源的研究评分口径，优先使用本院/本方法的参考范围。',
         'SCV参考页面注明复审暂停，不能把公开研究范围视为当前本院临床标准。',
         '血气公开范围存在单位和人群差异；原始标本类型不明时不能视为本院临床判定。',
         'PCT、胆红素等缺少适用日龄/方法规则的项目保留未评估，不当成正常。']


def rules_for(intake, metrics, overrides=(), max_hours=72):
    rules = {
        'ph': ReferenceRule(key='ph', unit='unitless', lower=7.25, upper=7.45,
                            source=SOURCE, label='SCV新生儿血气研究范围（非本院报告范围）'),
        'paco2': ReferenceRule(key='paco2', unit='mmHg', lower=35, upper=50,
                              source=SOURCE, label='SCV新生儿血气研究范围（非本院报告范围）'),
    }
    age = intake.get('postnatal_age_hours')
    term = (intake.get('gestational_weeks') or 0) >= 37
    if term and age is not None and age + max_hours <= 240:
        rules['crp'] = ReferenceRule(key='crp', unit='mg/L', lower=0, upper=7, upper_inclusive=False,
                                     source=SOURCE, label='SCV足月儿出生后10日内研究范围')
    if term and age is not None and age + max_hours < 744:
        rules['wbc'] = ReferenceRule(key='wbc', unit='10^9/L', lower=9, upper=30,
                                     source=WBC_SOURCE, label='Iowa足月儿0至不足31日研究范围')
    for rule in overrides:
        rules[rule.key] = rule
    return {key: rule.model_dump() for key, rule in rules.items()
            if key in metrics and metrics[key]['unit'] == rule.unit}


def classification(item, rule):
    if not rule or item['unit'] != rule['unit']:
        return 'unassessed'
    value = item.get('value')
    if value is None:
        # Censoring is not a point estimate. Only classify when the whole
        # represented nonnegative interval establishes the result.
        bound, op = item.get('bound'), item.get('comparator')
        if bound is not None and op in ('<', '≤') and rule.get('lower') in (None, 0) and rule.get('upper') is not None:
            if bound < rule['upper'] or bound == rule['upper'] and (op == '<' or rule['upper_inclusive']):
                return 'normal'
        return 'unknown'
    if rule.get('lower') is not None and value < rule['lower']:
        return 'low'
    if rule.get('upper') is not None and (value > rule['upper'] or value == rule['upper'] and not rule['upper_inclusive']):
        return 'high'
    return 'normal'


def score(labs, rules):
    statuses = {key: classification(labs.get(key, {'value': None, 'unit': rule['unit']}), rule)
                for key, rule in rules.items()}
    known = bool(statuses) and all(s in ('normal', 'high', 'low') for s in statuses.values())
    return {'abnormal_count': sum(s in ('high', 'low') for s in statuses.values()) if known else None,
            'evaluated_count': len(statuses), 'statuses': statuses, 'complete': known,
            'unassessed_keys': sorted(set(labs) - set(rules))}

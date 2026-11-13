"""Read-only admission-window lab extraction. No future course or model imputation."""
import random
from collections import Counter
from ehrdreamer.source import SourceCatalog, SourceError
from .references import rules_for, score, NOTES
from .admission import extract_admission, clinical_findings



def extract_baseline(raw):
    return extract_admission(raw)


class BaselineCatalog(SourceCatalog):
    @staticmethod
    def public(case):
        return {k:v for k,v in case.items() if k not in ('raw','reference','group_id','scope_eligible','partition')}

    def ensure(self):
        super().ensure()
        with self.lock:
            if getattr(self, '_baseline_stamp', None) == self.stamp:
                return
            counts = Counter()
            for case in self.rows:
                case['scope_eligible'] = case['eligible']
                case['intake']['clinical_findings'] = clinical_findings(case['raw'])
                baseline = extract_baseline(case['raw'])
                rules = rules_for(case['intake'], baseline, max_hours=72)
                result = score(baseline, rules)
                case['baseline'] = baseline
                case['metric_counts'] = {'initialized':len(baseline),'scored':len(rules),
                                        'unassessed':len(baseline)-len(rules),
                                        'ambiguous_at_admission':sum(bool(i.get('ambiguous_same_day')) for i in baseline.values())}
                case['evaluation_preview'] = {**result, 'reference_rules': rules}
                case['eligible'] = bool(case['eligible'] and baseline and result['complete'])
                case['baseline_time_precision'] = 'admission_window_not_verified_simultaneous'
                if not baseline:
                    case['quality_flags'].append('没有可核实的入院窗口化验，未用未来检验或模型填充')
                elif not result['complete']:
                    case['quality_flags'].append('基线缺少适用的完整研究评价规则，未进入默认批次')
                counts.update(baseline.keys())
            self.metadata.update(eligible_cases=sum(c['eligible'] for c in self.rows),
                                 baseline_cases=sum(bool(c['baseline']) for c in self.rows),
                                 baseline_metric_coverage=dict(counts),
                                 initialization='real_admission_window_labs',
                                 extraction='all_explicit_admission_numeric_metrics_no_seven_metric_allowlist',
                                 notes=NOTES + ['仅使用入院字段及入院当日检验窗口；不拼接出院或治疗后的结果。',
                                                '同日记录缺少小时顺序，初态不宣称严格同时或完全未经治疗。'])
            self._baseline_stamp = self.stamp

    def checked(self, identifier, request):
        case = self.get(identifier)
        if not case['scope_eligible']:
            raise SourceError('该病例未通过来源范围或重复记录校验。')
        if not case['baseline']:
            raise SourceError('该病例没有可核实的初始化验结果，请选择有基线的病例。')
        for rule in request.reference_rules:
            if rule.key in case['baseline'] and rule.unit!=case['baseline'][rule.key]['unit']:
                raise SourceError('自定义参考范围的单位与原始基线不一致。')
        rules = rules_for(case['intake'], case['baseline'], request.reference_rules, request.max_hours)
        if not score(case['baseline'], rules)['complete']:
            raise SourceError('该病例缺少适用评价规则，请提供有来源的参考范围或更换病例。')
        return case, rules

    def plan(self,body):
        self.ensure()
        if body.source_sha256 and body.source_sha256!=self.metadata['source_sha256']:
            raise SourceError('来源工作簿已变化，请重新预览。')
        unique={}
        for case in self.rows:
            rules=rules_for(case['intake'],case['baseline'],body.reference_rules,body.max_hours)
            if case['scope_eligible'] and score(case['baseline'],rules)['complete']:
                unique.setdefault(case['group_id'],case)
        if len(unique)<10:
            raise SourceError('具备真实基线和适用评价规则的独立病例不足10名。')
        rng=random.Random(body.seed);selected=rng.sample(list(unique.values()),10)
        budgets=[('short',rng.randint(1,2)) for _ in range(3)]+[('medium',rng.randint(3,5)) for _ in range(5)]+[('long',rng.randint(6,10)) for _ in range(2)]
        rng.shuffle(budgets)
        return {'source':self.metadata,'total_patients':10,'distribution':{'short':3,'medium':5,'long':2},
                'patients':[{'index':i,'length_group':group,'max_operations':64,
                             'max_cycles':min(cycles,body.max_cycles),'case':self.public(case)}
                            for i,(case,(group,cycles)) in enumerate(zip(selected,budgets),1)]}
